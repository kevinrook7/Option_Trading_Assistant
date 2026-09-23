"""
NSE option chain fetcher.

Set MOCK_NSE=true in .env to return synthetic data without hitting NSE.
Useful for tests and offline development.
"""

import os
import requests
import numpy as np
import pandas as pd
from pathlib import Path


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

def _mock_option_data(index: str = "NIFTY") -> tuple:
    """
    Return synthetic option chain data for offline testing.
    Spot fixed at 24500. Chain covers 11 strikes within ±5% of spot.
    """
    spot = 24500.0
    strikes = [round(spot * (1 + i * 0.01)) for i in range(-5, 6)]
    expiry = "25-09-2026"
    rng = np.random.default_rng(42)  # deterministic seed for tests

    calls, puts = [], []
    for k in strikes:
        dist = abs(k - spot) / spot
        iv = 15 + dist * 80 + float(rng.normal(0, 0.5))
        oi = max(100, int(60000 * (1 - dist * 8) + float(rng.normal(0, 500))))
        vol = max(10, oi // 20)
        prem_c = max(5.0, float(200 - (k - spot)) * rng.uniform(0.9, 1.1))
        prem_p = max(5.0, float(200 + (k - spot)) * rng.uniform(0.9, 1.1))

        calls.append({
            "strikePrice": float(k),
            "expiryDate": expiry,
            "impliedVolatility": round(iv, 2),
            "openInterest": oi,
            "totalTradedVolume": vol,
            "buyPrice1": round(prem_c, 2),
            "lastPrice": round(prem_c * 0.98, 2),
        })
        puts.append({
            "strikePrice": float(k),
            "expiryDate": expiry,
            "impliedVolatility": round(iv + 2, 2),
            "openInterest": int(oi * 1.1),
            "totalTradedVolume": vol,
            "buyPrice1": round(prem_p, 2),
            "lastPrice": round(prem_p * 0.98, 2),
        })

    chain = {
        "underlyingValue": spot,
        "calls": pd.DataFrame(calls),
        "puts": pd.DataFrame(puts),
    }
    print(f"[MOCK] Returning synthetic {index} data. Spot={spot}")
    return chain, spot


# ---------------------------------------------------------------------------
# Live fetch
# ---------------------------------------------------------------------------

def fetch_option_data(index="NIFTY"):
    """
    Fetch the option chain using the 'nse' library.
    Falls back to direct HTTP if the library fails.

    Set MOCK_NSE=true in .env or environment to use synthetic data instead.

    Returns: (chain_dict, spot_price)
    """
    if os.getenv("MOCK_NSE", "").lower() in ("true", "1", "yes"):
        return _mock_option_data(index)

    try:
        from nse import NSE
        from pathlib import Path
        
        nse = NSE(download_folder=Path("./data"), server=False)
        raw = nse.optionChain(symbol=index.lower())
        nse.exit()
        
        # Extract records
        records = raw.get('records') if 'records' in raw else None
        
        # Extract spot price
        spot = 0
        if records and 'underlyingValue' in records:
            spot = records.get('underlyingValue', 0)
        elif 'underlyingValue' in raw:
            spot = raw.get('underlyingValue', 0)
        
        if spot == 0:
            return None, 0
        
        # Extract data list (prefer records['data'], fallback to raw['data'])
        data_list = []
        if records and 'data' in records:
            data_list = records.get('data', [])
        elif 'data' in raw:
            data_list = raw.get('data', [])
        
        # Build calls and puts from 'CE'/'PE' keys
        calls_list = []
        puts_list = []
        for item in data_list:
            if 'CE' in item:
                calls_list.append(item['CE'])
            if 'PE' in item:
                puts_list.append(item['PE'])
        
        # If no data found via CE/PE, try alternative 'calls'/'puts' keys
        if not calls_list and not puts_list:
            if records:
                calls_list = records.get('calls', [])
                puts_list = records.get('puts', [])
            else:
                calls_list = raw.get('calls', [])
                puts_list = raw.get('puts', [])
        
        calls_df = pd.DataFrame(calls_list) if calls_list else pd.DataFrame()
        puts_df = pd.DataFrame(puts_list) if puts_list else pd.DataFrame()
        
        chain = {
            'underlyingValue': spot,
            'calls': calls_df,
            'puts': puts_df
        }
        return chain, spot
        
    except Exception as e:
        # Fallback to direct HTTP
        return fetch_option_data_direct(index)

def fetch_option_data_direct(index="NIFTY"):
    """
    Fallback: Fetch directly from NSE's public API.
    """
    try:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={index}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.nseindia.com/'
        }
        session = requests.Session()
        session.get('https://www.nseindia.com', headers=headers)
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        records = data.get('records', {})
        spot = records.get('underlyingValue', 0)
        if spot == 0:
            spot = data.get('underlyingValue', 0)
        
        if spot == 0:
            print(f"Warning: Could not extract spot price for {index}")
            return None, 0
        
        raw_data = records.get('data', [])
        if not raw_data:
            raw_data = data.get('data', [])
        
        calls_list = []
        puts_list = []
        for item in raw_data:
            if 'CE' in item:
                calls_list.append(item['CE'])
            if 'PE' in item:
                puts_list.append(item['PE'])
        
        if not calls_list and not puts_list:
            calls_list = records.get('calls', [])
            puts_list = records.get('puts', [])
        
        calls_df = pd.DataFrame(calls_list) if calls_list else pd.DataFrame()
        puts_df = pd.DataFrame(puts_list) if puts_list else pd.DataFrame()
        
        print(f"✅ {index}: Spot = {spot}, Calls: {len(calls_df)}, Puts: {len(puts_df)}")
        
        chain = {
            'underlyingValue': spot,
            'calls': calls_df,
            'puts': puts_df
        }
        return chain, spot
        
    except Exception as e:
        print(f"Direct fetch failed: {e}")
        return None, 0