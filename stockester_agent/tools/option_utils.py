import calendar
import pandas as pd
from datetime import date

#---------------------------------------------------------------------------
# Expiry helpers
# ---------------------------------------------------------------------------

def _last_tuesday_of_month(ref: date) -> date:
    """Return the last Tuesday of the month containing ref."""
    last_day = calendar.monthrange(ref.year, ref.month)[1]
    for day in range(last_day, last_day - 7, -1):
        if date(ref.year, ref.month, day).weekday() == 1:  # 1 = Tuesday
            return date(ref.year, ref.month, day)


def _parse_expiries(calls_df: pd.DataFrame, puts_df: pd.DataFrame) -> pd.Series:
    """Return a sorted Series of unique parsed expiry Timestamps from the chain."""
    def get_col(df):
        if df.empty or 'expiryDate' not in df.columns:
            return pd.Series(dtype='object')
        return df['expiryDate']

    raw = pd.concat([get_col(calls_df), get_col(puts_df)], ignore_index=True).dropna()
    parsed = pd.to_datetime(raw, format='%d-%m-%Y', errors='coerce').dropna()
    return pd.Series(parsed.unique()).sort_values().reset_index(drop=True)


def _filter_expiry(df: pd.DataFrame, target_dt: pd.Timestamp) -> pd.DataFrame:
    if df.empty or 'expiryDate' not in df.columns:
        return pd.DataFrame()
    mask = pd.to_datetime(df['expiryDate'], format='%d-%m-%Y', errors='coerce') == target_dt
    return df[mask].copy()


# ---------------------------------------------------------------------------
# Shared pipeline helpers
# ---------------------------------------------------------------------------

def _liquidity_filter(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    for col in ('openInterest', 'totalTradedVolume'):
        if col not in df.columns:
            df[col] = 0
    return df[(df['openInterest'] > 0) & (df['totalTradedVolume'] > 0)].copy()


def _otm_filter(calls_df: pd.DataFrame, puts_df: pd.DataFrame, spot: float):
    """Keep only 0-5% OTM strikes."""
    if not calls_df.empty and 'strikePrice' in calls_df.columns:
        calls_df = calls_df[
            (calls_df['strikePrice'] > spot) &
            (calls_df['strikePrice'] <= spot * 1.05)
        ].copy()
    if not puts_df.empty and 'strikePrice' in puts_df.columns:
        puts_df = puts_df[
            (puts_df['strikePrice'] < spot) &
            (puts_df['strikePrice'] >= spot * 0.95)
        ].copy()
    return calls_df, puts_df


def _build_combined(calls_df: pd.DataFrame, puts_df: pd.DataFrame, spot: float) -> pd.DataFrame:
    """Tag, combine, fill required columns, add distance_pct."""
    if not calls_df.empty:
        calls_df = calls_df.copy()
        calls_df['type'] = 'CE'
    if not puts_df.empty:
        puts_df = puts_df.copy()
        puts_df['type'] = 'PE'

    combined = pd.concat([calls_df, puts_df], ignore_index=True)

    for col in ('strikePrice', 'buyPrice1', 'impliedVolatility', 'openInterest'):
        if col not in combined.columns:
            combined[col] = 0

    combined['impliedVolatility'] = combined['impliedVolatility'].fillna(0)
    combined['openInterest']      = combined['openInterest'].fillna(0)
    combined['buyPrice1']         = combined['buyPrice1'].fillna(0)

    combined['distance_pct'] = combined.apply(
        lambda r: (r['strikePrice'] - spot) / spot * 100 if r['type'] == 'CE'
                  else (spot - r['strikePrice']) / spot * 100,
        axis=1
    )
    return combined


def _maxnorm(series: pd.Series) -> pd.Series:
    mx = series.max()
    if mx == 0:
        return pd.Series([0.0] * len(series), index=series.index)
    return series / mx
