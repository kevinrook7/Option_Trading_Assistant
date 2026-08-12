import requests
import pandas as pd
from pathlib import Path
import json

def fetch_option_data(index="NIFTY"):
    """
    Fetches the option chain using the 'nse' library with debug prints.
    """
    try:
        from nse import NSE
        nse = NSE(download_folder=Path("./data"), server=False)
        
        # Try lowercase (as used internally by library)
        raw = nse.optionChain(symbol=index.lower())
        nse.exit()
        
        # Debug: Print top-level keys
        print(f"DEBUG: raw keys = {list(raw.keys())}")
        
        # Check if 'records' exists
        if 'records' in raw:
            records = raw['records']
            print(f"DEBUG: records keys = {list(records.keys())}")
            print(f"DEBUG: underlyingValue = {records.get('underlyingValue')}")
            print(f"DEBUG: data length = {len(records.get('data', []))}")
            # Print first data item if any
            if records.get('data'):
                print(f"DEBUG: first data item keys = {list(records['data'][0].keys())}")
        else:
            print("DEBUG: No 'records' key in raw response")
            # Maybe the data is directly in raw?
            print(f"DEBUG: raw has keys: {list(raw.keys())}")
            # Try to find underlyingValue elsewhere
            if 'underlyingValue' in raw:
                print(f"DEBUG: underlyingValue directly in raw = {raw['underlyingValue']}")
        
        # Now try to extract spot
        spot = 0
        if 'records' in raw:
            records = raw['records']
            spot = records.get('underlyingValue', 0)
        elif 'underlyingValue' in raw:
            spot = raw['underlyingValue']
        
        if spot == 0:
            print(f"Warning: Could not extract spot price for {index}")
            return None, 0
        
        # Extract calls and puts
        if 'records' in raw:
            data_list = raw['records'].get('data', [])
        else:
            data_list = raw.get('data', [])
        
        calls_list = []
        puts_list = []
        for item in data_list:
            if 'CE' in item:
                calls_list.append(item['CE'])
            if 'PE' in item:
                puts_list.append(item['PE'])
        
        if not calls_list and not puts_list:
            # try alternative
            if 'records' in raw:
                calls_list = raw['records'].get('calls', [])
                puts_list = raw['records'].get('puts', [])
            else:
                calls_list = raw.get('calls', [])
                puts_list = raw.get('puts', [])
        
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
        print(f"Library fetch failed ({e}), falling back to direct HTTP...")
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
        # Print debug for direct method too
        print(f"DEBUG direct: keys = {list(data.keys())}")
        if 'records' in data:
            print(f"DEBUG direct: records keys = {list(data['records'].keys())}")
            print(f"DEBUG direct: underlyingValue = {data['records'].get('underlyingValue')}")
        
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