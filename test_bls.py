#!/usr/bin/env python3
"""
Simple BLS API Test Script
Test BLS API with start and end year parameters
"""

import requests
import json
import os
import argparse
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_bls_api(start_year, end_year):
    """Test BLS API with specified years"""
    print(f"🧪 BLS API TEST: {start_year} to {end_year}")
    print("=" * 40)
    
    # Get BLS API key
    bls_api_key = os.getenv('BLS_API_KEY')
    if bls_api_key:
        print("🔑 Using API key from environment")
    else:
        print("⚠️ No API key - using public API")
    
    # BLS API call
    url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    payload = {
        "seriesid": ["CUUR0000SA0"],
        "startyear": str(start_year),
        "endyear": str(end_year),
        "catalog": True,
        "calculations": True,
        "annualaverage": False
    }
    
    if bls_api_key:
        payload["registrationkey"] = bls_api_key
    
    try:
        response = requests.post(url, data=json.dumps(payload), 
                               headers={'Content-Type': 'application/json'}, timeout=30)
        
        print(f"📈 Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('status') == 'REQUEST_SUCCEEDED':
                series_data = data['Results']['series'][0]['data']
                
                # Sort by year and period
                sorted_data = sorted(series_data, key=lambda x: (int(x['year']), x['period']))
                
                print(f"� Data Points: {len(sorted_data)}")
                print(f"{'Year':<6} {'Month':<6} {'CPI Value':<10}")
                print("-" * 25)
                
                for point in sorted_data:
                    year = point['year']
                    period = point['period']
                    value = point['value']
                    month = period.replace('M', '') if period.startswith('M') else period
                    print(f"{year:<6} {month:<6} {value:<10}")
                
                # Calculate inflation if we have multiple data points
                if len(sorted_data) >= 2:
                    first_cpi = float(sorted_data[0]['value'])
                    last_cpi = float(sorted_data[-1]['value'])
                    inflation_rate = ((last_cpi - first_cpi) / first_cpi) * 100
                    
                    print(f"\n💰 Inflation Calculation:")
                    print(f"   Start CPI: {first_cpi}")
                    print(f"   End CPI: {last_cpi}")
                    print(f"   Inflation: {inflation_rate:.2f}%")
                    
                    # Example contract adjustment
                    contract_value = 100000
                    adjusted_value = contract_value * (1 + inflation_rate/100)
                    print(f"   $100K contract becomes: ${adjusted_value:,.2f}")
                
            else:
                print(f"❌ API Error: {data.get('status')}")
        else:
            print(f"❌ HTTP Error: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Test BLS API')
    parser.add_argument('--start-year', type=int, required=True, help='Start year')
    parser.add_argument('--end-year', type=int, required=True, help='End year')
    
    args = parser.parse_args()
    test_bls_api(args.start_year, args.end_year)
    
    print(f"\nUsage: python test_bls.py --start-year 2023 --end-year 2025")
