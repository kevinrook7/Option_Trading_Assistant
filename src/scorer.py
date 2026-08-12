import calendar
from datetime import date

import pandas as pd


# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# SELL scorer  –  monthly expiry (last Tuesday of current month)
# ---------------------------------------------------------------------------

def rank_strikes(chain, spot, top_n=5):
    """
    Rank liquid NIFTY OTM strikes for SELLING (premium collection).

    Expiry : last Tuesday of the current month (NIFTY 50 monthly settlement).
    Scoring: iv_score×0.5  +  distance_score×0.3  +  oi_score×0.2
             Higher IV, further OTM, and lower OI (less crowded) is better.

    Returns list of dicts:
        strike, type, premium, iv, distance_otm, score, expiry, oi
    """
    calls_df = chain.get('calls', pd.DataFrame()).copy()
    puts_df  = chain.get('puts',  pd.DataFrame()).copy()

    if calls_df.empty and puts_df.empty:
        return []

    available = _parse_expiries(calls_df, puts_df)
    if available.empty:
        return []

    target_dt      = pd.Timestamp(_last_tuesday_of_month(date.today()))
    expiry_dt      = available.iloc[(available - target_dt).abs().argsort().iloc[0]]
    expiry_str     = expiry_dt.strftime('%d-%m-%Y')

    calls_df = _filter_expiry(calls_df, expiry_dt)
    puts_df  = _filter_expiry(puts_df,  expiry_dt)

    calls_df = _liquidity_filter(calls_df)
    puts_df  = _liquidity_filter(puts_df)

    calls_df, puts_df = _otm_filter(calls_df, puts_df, spot)

    if calls_df.empty and puts_df.empty:
        return []

    combined = _build_combined(calls_df, puts_df, spot)
    combined = combined[combined['buyPrice1'] >= 5].copy().reset_index(drop=True)

    if combined.empty:
        return []

    # Scoring: high IV good, far OTM good, LOW OI good (inverted)
    combined['iv_score']       = _maxnorm(combined['impliedVolatility'])
    combined['distance_score'] = _maxnorm(combined['distance_pct'])
    combined['oi_score']       = 1 - _maxnorm(combined['openInterest'])

    combined['sell_score'] = (
        combined['iv_score']       * 0.5 +
        combined['distance_score'] * 0.3 +
        combined['oi_score']       * 0.2
    )

    result = []
    for _, row in combined.nlargest(top_n, 'sell_score').iterrows():
        result.append({
            'strike':       int(row['strikePrice']),
            'type':         row['type'],
            'premium':      round(float(row['buyPrice1']), 2),
            'iv':           round(float(row['impliedVolatility']), 2),
            'distance_otm': round(float(row['distance_pct']), 2),
            'score':        round(float(row['sell_score']), 4),
            'expiry':       expiry_str,
            'oi':           int(row['openInterest']),
        })
    return result


# ---------------------------------------------------------------------------
# BUY scorer  –  nearest weekly expiry
# ---------------------------------------------------------------------------

def rank_strikes_buy(chain, spot, top_n=5):
    """
    Rank liquid NIFTY OTM strikes for BUYING (momentum/directional trades).

    Expiry : nearest weekly expiry available in the chain.
    Scoring: iv_score×0.5  +  distance_score×0.3  +  oi_score×0.2
             LOW IV (cheap), CLOSE to ATM, and HIGH OI (liquid) is better.

    R/R assumption: stop-loss at 50% of premium, target at 2× premium → R/R = 4.0.
    Strikes with premium < ₹5 are skipped (not worth transaction cost).

    Returns list of dicts:
        strike, type, premium, iv, distance_otm, score, expiry, oi, rr
    """
    calls_df = chain.get('calls', pd.DataFrame()).copy()
    puts_df  = chain.get('puts',  pd.DataFrame()).copy()

    if calls_df.empty and puts_df.empty:
        return []

    available = _parse_expiries(calls_df, puts_df)
    if available.empty:
        return []

    # Nearest weekly expiry = minimum available date
    expiry_dt  = available.iloc[0]
    expiry_str = expiry_dt.strftime('%d-%m-%Y')

    calls_df = _filter_expiry(calls_df, expiry_dt)
    puts_df  = _filter_expiry(puts_df,  expiry_dt)

    calls_df = _liquidity_filter(calls_df)
    puts_df  = _liquidity_filter(puts_df)

    calls_df, puts_df = _otm_filter(calls_df, puts_df, spot)

    if calls_df.empty and puts_df.empty:
        return []

    combined = _build_combined(calls_df, puts_df, spot)
    combined = combined[combined['buyPrice1'] >= 5].copy().reset_index(drop=True)

    if combined.empty:
        return []

    # Scoring: LOW IV good (inverted), CLOSE to ATM good (inverted), HIGH OI good
    combined['iv_score']       = 1 - _maxnorm(combined['impliedVolatility'])
    combined['distance_score'] = 1 - _maxnorm(combined['distance_pct'])
    combined['oi_score']       = _maxnorm(combined['openInterest'])

    combined['buy_score'] = (
        combined['iv_score']       * 0.5 +
        combined['distance_score'] * 0.3 +
        combined['oi_score']       * 0.2
    )

    # R/R: fixed stop=50% of premium, target=2× premium → reward/risk = 4.0
    RR = round(2.0 / 0.5, 1)   # 4.0

    result = []
    for _, row in combined.nlargest(top_n, 'buy_score').iterrows():
        result.append({
            'strike':       int(row['strikePrice']),
            'type':         row['type'],
            'premium':      round(float(row['buyPrice1']), 2),
            'iv':           round(float(row['impliedVolatility']), 2),
            'distance_otm': round(float(row['distance_pct']), 2),
            'score':        round(float(row['buy_score']), 4),
            'expiry':       expiry_str,
            'oi':           int(row['openInterest']),
            'rr':           RR,
        })
    return result
