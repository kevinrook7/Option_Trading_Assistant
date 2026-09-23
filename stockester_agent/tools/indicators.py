import numpy as np
import pandas as pd

def ensure_df(data):
    if data is None:
        return pd.DataFrame()
    if isinstance(data, pd.DataFrame):
        return data
    if isinstance(data, list):
        return pd.DataFrame(data)
    return pd.DataFrame()

def find_column(df, possible_names):
    """Find the first column in df that matches any name in possible_names (case-insensitive)."""
    if df.empty:
        return None
    # Get list of column names as strings (handle MultiIndex)
    cols = [str(c) for c in df.columns]
    for name in possible_names:
        # Exact match
        if name in cols:
            return name
        # Case-insensitive
        for col in cols:
            if col.lower() == name.lower():
                return col
    return None

STRIKE_COLS = ['strikePrice', 'strike', 'Strike']
OI_COLS = ['openInterest', 'oi', 'Open_Interest', 'OPENINTEREST']
EXPIRY_COLS = ['expiryDate', 'expiry', 'EXPIRY_DT']

def parse_expiry_series(series):
    """Parse an expiry column to Timestamps, tolerating 31-07-2026 and 31-Jul-2026."""
    parsed = pd.to_datetime(series, format='%d-%m-%Y', errors='coerce')
    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(series[missing], dayfirst=True, errors='coerce')
    return parsed

def coerce_expiry(expiry):
    """Normalise an expiry given as str/date/Timestamp to a Timestamp, or None."""
    if expiry is None:
        return None
    if isinstance(expiry, str):
        parsed = pd.to_datetime(expiry, format='%d-%m-%Y', errors='coerce')
        if pd.isna(parsed):
            parsed = pd.to_datetime(expiry, dayfirst=True, errors='coerce')
    else:
        parsed = pd.to_datetime(expiry, errors='coerce')
    return None if pd.isna(parsed) else parsed

def get_expiries(chain):
    """Sorted unique expiry Timestamps present in the chain's calls and puts."""
    frames = []
    for key in ('calls', 'puts'):
        df = ensure_df(chain.get(key))
        col = find_column(df, EXPIRY_COLS)
        if col is not None:
            frames.append(parse_expiry_series(df[col]))
    if not frames:
        return pd.Series(dtype='datetime64[ns]')
    combined = pd.concat(frames, ignore_index=True).dropna()
    return pd.Series(combined.unique()).sort_values().reset_index(drop=True)

def get_nearest_expiry(chain):
    """Earliest expiry available in the chain, or None if there are none."""
    expiries = get_expiries(chain)
    return None if expiries.empty else expiries.iloc[0]

def filter_by_expiry(df, expiry):
    """Return only the rows of df belonging to a single expiry."""
    if df.empty:
        return df
    col = find_column(df, EXPIRY_COLS)
    if col is None:
        print(f"DEBUG: No expiry column found. Columns: {list(df.columns)}")
        return pd.DataFrame()
    return df[parse_expiry_series(df[col]) == expiry].copy()

def _oi_by_strike(chain, expiry):
    """
    OI for a single expiry, indexed by strike and aligned across calls and puts.

    Returns a DataFrame with 'call_oi' and 'put_oi' columns, or None if the
    chain lacks the required columns / has no rows for this expiry.
    """
    calls = ensure_df(chain.get('calls'))
    puts = ensure_df(chain.get('puts'))

    if calls.empty or puts.empty:
        print("DEBUG: Calls or puts DataFrame is empty")
        return None

    strike_col = find_column(calls, STRIKE_COLS)
    oi_col = find_column(calls, OI_COLS)

    if strike_col is None:
        print(f"DEBUG: No strike column found in calls. Columns: {list(calls.columns)}")
        return None
    if oi_col is None:
        print(f"DEBUG: No OI column found in calls. Columns: {list(calls.columns)}")
        return None
    if strike_col not in puts.columns or oi_col not in puts.columns:
        print(f"DEBUG: Strike/OI column missing in puts. Columns: {list(puts.columns)}")
        return None

    calls = filter_by_expiry(calls, expiry)
    puts = filter_by_expiry(puts, expiry)

    if calls.empty or puts.empty:
        print(f"DEBUG: No rows for expiry {expiry.date()} "
              f"(calls: {len(calls)}, puts: {len(puts)})")
        return None

    # Group by strike so duplicate rows collapse, then align on the strike index
    # instead of assuming calls and puts share a row order.
    oi = pd.concat(
        [
            calls.groupby(strike_col)[oi_col].sum().rename('call_oi'),
            puts.groupby(strike_col)[oi_col].sum().rename('put_oi'),
        ],
        axis=1,
    ).fillna(0).sort_index()

    if oi.empty or (oi['call_oi'].sum() == 0 and oi['put_oi'].sum() == 0):
        print("DEBUG: All OI values are zero")
        return None

    return oi

def calculate_max_pain(chain, spot, expiry=None):
    """
    Strike at which total option payout is smallest for a single expiry.

    expiry defaults to the nearest expiry in the chain. Returns spot when the
    chain cannot be read.
    """
    expiry = coerce_expiry(expiry) or get_nearest_expiry(chain)
    if expiry is None:
        print("DEBUG: No usable expiry in chain for max pain")
        return spot

    oi = _oi_by_strike(chain, expiry)
    if oi is None:
        return spot

    strikes = oi.index.to_numpy(dtype=float)
    call_oi = oi['call_oi'].to_numpy(dtype=float)
    put_oi = oi['put_oi'].to_numpy(dtype=float)

    # Payout if the index settled at each candidate strike S:
    #   sum(call_oi * max(0, S - K)) + sum(put_oi * max(0, K - S))
    moneyness = strikes[:, None] - strikes[None, :]   # S on rows, K on columns
    payout = (
        np.clip(moneyness, 0, None) @ call_oi +
        np.clip(-moneyness, 0, None) @ put_oi
    )

    return float(strikes[np.argmin(payout)])

def calculate_pcr(chain, expiry=None):
    """
    Put-Call open interest ratio for a single expiry.

    expiry defaults to the nearest expiry in the chain.
    """
    expiry = coerce_expiry(expiry) or get_nearest_expiry(chain)
    if expiry is None:
        print("DEBUG: No usable expiry in chain for PCR")
        return 0.0

    oi = _oi_by_strike(chain, expiry)
    if oi is None:
        return 0.0

    total_call = oi['call_oi'].sum()
    if total_call == 0:
        return 0.0
    return float(oi['put_oi'].sum() / total_call)

def get_iv_percentile(chain):
    calls = ensure_df(chain.get('calls'))
    puts = ensure_df(chain.get('puts'))
    
    iv_col = find_column(calls, ['impliedVolatility', 'iv', 'implied_volatility'])
    if iv_col is None:
        iv_col = find_column(puts, ['impliedVolatility', 'iv', 'implied_volatility'])
    
    if iv_col is None:
        print(f"DEBUG: No IV column found. Calls columns: {list(calls.columns)}, Puts columns: {list(puts.columns)}")
        return 0.0
    
    all_iv = []
    if not calls.empty and iv_col in calls.columns:
        all_iv.extend(calls[iv_col].dropna().values)
    if not puts.empty and iv_col in puts.columns:
        all_iv.extend(puts[iv_col].dropna().values)
    
    if not all_iv:
        return 0.0
    return np.mean(all_iv) 
    