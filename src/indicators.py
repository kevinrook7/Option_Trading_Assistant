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

def calculate_max_pain(chain, spot):
    calls = ensure_df(chain.get('calls'))
    puts = ensure_df(chain.get('puts'))
    
    if calls.empty or puts.empty:
        print("DEBUG: Calls or puts DataFrame is empty")
        return spot
    
    # Find strike and OI columns
    strike_col = find_column(calls, ['strikePrice', 'strike', 'Strike'])
    oi_col = find_column(calls, ['openInterest', 'oi', 'Open_Interest', 'OPENINTEREST'])
    
    if strike_col is None:
        print(f"DEBUG: No strike column found in calls. Columns: {list(calls.columns)}")
        return spot
    if oi_col is None:
        print(f"DEBUG: No OI column found in calls. Columns: {list(calls.columns)}")
        return spot
    
    # Ensure OI columns in puts too
    if oi_col not in puts.columns:
        print(f"DEBUG: OI column '{oi_col}' not found in puts. Columns: {list(puts.columns)}")
        return spot
    
    # Align lengths
    min_len = min(len(calls), len(puts))
    strikes = calls[strike_col].values[:min_len]
    call_oi = calls[oi_col].values[:min_len]
    put_oi = puts[oi_col].values[:min_len]
    
    # Check if any OI > 0
    if np.sum(call_oi) == 0 and np.sum(put_oi) == 0:
        print("DEBUG: All OI values are zero")
        return spot
    
    pain = []
    for i, strike in enumerate(strikes):
        call_pain = max(0, strike - spot) * call_oi[i]
        put_pain = max(0, spot - strike) * put_oi[i]
        pain.append(call_pain + put_pain)
    
    if not pain:
        return spot
    max_pain_idx = np.argmin(pain)
    return strikes[max_pain_idx]

def calculate_pcr(chain):
    calls = ensure_df(chain.get('calls'))
    puts = ensure_df(chain.get('puts'))
    if calls.empty or puts.empty:
        return 0.0
    
    oi_col = find_column(calls, ['openInterest', 'oi', 'Open_Interest', 'OPENINTEREST'])
    if oi_col is None:
        print(f"DEBUG: No OI column in calls for PCR. Columns: {list(calls.columns)}")
        return 0.0
    
    if oi_col not in puts.columns:
        print(f"DEBUG: OI column '{oi_col}' not found in puts for PCR.")
        return 0.0
    
    total_call = calls[oi_col].sum()
    total_put = puts[oi_col].sum()
    if total_call == 0:
        return 0.0
    return total_put / total_call

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
    return np.mean(all_iv) * 100

def compute_metrics_for_index(chain, spot):
    if chain is None or spot == 0:
        return None
    
    max_pain = calculate_max_pain(chain, spot)
    distance = abs(spot - max_pain) / spot if spot > 0 else 0
    
    pcr = calculate_pcr(chain)
    iv = get_iv_percentile(chain)
    
    # If still zero IV, debug sample
    if iv == 0.0:
        calls = ensure_df(chain.get('calls'))
        if not calls.empty:
            print(f"DEBUG: Sample calls columns: {list(calls.columns)[:5]}")
            print(f"DEBUG: First call row: {calls.iloc[0].to_dict() if len(calls) > 0 else 'empty'}")
    
    max_pain_score = min(distance * 3, 1.0)
    if pcr < 0.8:
        pcr_score = 1.0
    elif pcr > 1.2:
        pcr_score = 0.0
    else:
        pcr_score = 0.5
    iv_score = min(iv / 30.0, 1.0)
    
    return {
        'spot': spot,
        'max_pain': max_pain,
        'max_pain_distance': round(distance * 100, 2),
        'max_pain_score': round(max_pain_score, 4),
        'pcr': round(pcr, 3),
        'pcr_score': round(pcr_score, 4),
        'iv': round(iv, 2),
        'iv_score': round(iv_score, 4),
    }