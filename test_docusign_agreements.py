#!/usr/bin/env python3
"""
Test DocuSign Agreements API - Simple version
Get all agreements and filter by expiration date (current date + next 30 days)
"""

import logging
import requests
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from docusign_auth_manager import create_docusign_auth_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_agreements_api():
    """Test DocuSign Agreements API - Get all agreements and filter by expiration date"""
    try:
        # Load environment variables
        load_dotenv()
        
        logger.info("🔧 Testing DocuSign Agreements API...")
        
        # Create auth manager and get access token
        auth_manager = create_docusign_auth_manager()
        access_token = auth_manager.get_access_token()
        account_id = auth_manager.get_account_id()
        
        logger.info(f"✅ Authentication successful, Account ID: {account_id}")
        
        # Calculate date range (next 30 days)
        today = datetime.now().strftime("%Y-%m-%d")
        thirty_days_later = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        
        logger.info(f"📅 Date range: {today} to {thirty_days_later}")
        
        # API endpoint
        base_url = "https://api-d.docusign.com"
        endpoint = f"/v1/accounts/{account_id}/agreements"
        url = f"{base_url}{endpoint}"
        
        # Headers
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        # Simple params - get all agreements
        params = {'limit': 50}
        
        logger.info(f"🔗 Making API call to: {url}")
        
        # Make the API call
        response = requests.get(url, headers=headers, params=params)
        
        logger.info(f"📊 Response Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            all_agreements = data.get('data', [])
            logger.info(f"📋 Retrieved {len(all_agreements)} total agreements")
            
            # Client-side filtering for agreements expiring in next 30 days
            from datetime import datetime as dt
            today_dt = dt.strptime(today, "%Y-%m-%d")
            cutoff_dt = dt.strptime(thirty_days_later, "%Y-%m-%d")
            
            filtered_agreements = []
            for agreement in all_agreements:
                exp_date_str = agreement.get('provisions', {}).get('expiration_date')
                if exp_date_str:
                    try:
                        # Parse the expiration date (format: 2025-08-31T00:00:00)
                        exp_date = dt.strptime(exp_date_str.split('T')[0], "%Y-%m-%d")
                        
                        # Check if it expires between today and 30 days from now
                        if today_dt <= exp_date <= cutoff_dt:
                            filtered_agreements.append(agreement)
                    except:
                        pass  # Skip invalid dates
            
            logger.info(f"🎯 FILTERED RESULT: {len(filtered_agreements)} agreements expire in next 30 days")
            
            # Show the correctly filtered results
            print("\n" + "="*60)
            print("🎯 AGREEMENTS EXPIRING IN NEXT 30 DAYS")
            print("="*60)
            
            for i, agreement in enumerate(filtered_agreements):
                exp_date = agreement.get('provisions', {}).get('expiration_date', 'No date')
                title = agreement.get('title', 'Unknown')
                agreement_id = agreement.get('id', 'N/A')
                status = agreement.get('status', 'N/A')
                
                print(f"{i+1}. {title}")
                print(f"   ID: {agreement_id}")
                print(f"   Status: {status}")
                print(f"   Expires: {exp_date}")
                print()
            
            print("="*60)
            
            return True
            
        else:
            logger.error(f"❌ API call failed: {response.status_code}")
            logger.error(f"Response: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Test failed with error: {e}")
        return False

def test_agreements_api_filtered():
    """Test DocuSign Agreements API with server-side filtering for expiring agreements"""
    try:
        # Load environment variables
        load_dotenv()
        
        logger.info("🔧 Testing DocuSign Agreements API with filtered query...")
        
        # Create auth manager and get access token
        auth_manager = create_docusign_auth_manager()
        access_token = auth_manager.get_access_token()
        account_id = auth_manager.get_account_id()
        
        logger.info(f"✅ Authentication successful, Account ID: {account_id}")
        
        # Calculate date range (next 30 days)
        today = datetime.now().strftime("%Y-%m-%d")
        thirty_days_later = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        
        logger.info(f"📅 Filtering for agreements expiring by: {thirty_days_later}")
        
        # API endpoint with query parameters for server-side filtering
        base_url = "https://api-d.docusign.com"
        endpoint = f"/v1/accounts/{account_id}/agreements"
        
        # Try different parameter formatting approaches
        import urllib.parse
        
        # Build query parameters for expiration date filtering
        query_params = {
            "expiration_date[lte]": thirty_days_later
        }
        
        # Construct URL with proper URL encoding
        encoded_params = urllib.parse.urlencode(query_params)
        url = f"{base_url}{endpoint}?{encoded_params}"
        
        logger.info(f"📡 API URL (encoded): {url}")
        
        # Headers
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        # Make API call
        logger.info("📡 Making API call to get filtered agreements...")
        response = requests.get(url, headers=headers, timeout=30)
        
        logger.info(f"📊 Response Status: {response.status_code}")
        
        if response.status_code == 200:
            agreements_data = response.json()
            logger.info(f"🔍 Raw response data keys: {list(agreements_data.keys())}")
            
            # The API returns agreements in 'data'
            agreements = agreements_data.get('data', [])
            
            logger.info(f"🎯 Server-side filtering returned {len(agreements)} agreements")
            
            # Display filtered agreements
            if agreements:
                logger.info("\n📋 Agreements returned by server-side filter:")
                for i, agreement in enumerate(agreements, 1):
                    agreement_id = agreement.get('id', 'N/A')
                    title = agreement.get('title', 'No title')
                    
                    # Get expiration date from provisions
                    provisions = agreement.get('provisions', {})
                    expiration_date = provisions.get('expiration_date', 'N/A')
                    
                    logger.info(f"  {i}. Agreement ID: {agreement_id}")
                    logger.info(f"     Title: {title}")
                    logger.info(f"     Expiration Date: {expiration_date}")
                    logger.info("")
            else:
                logger.info("ℹ️  No agreements found by server-side filter")
            
            return True
            
        else:
            logger.error(f"❌ API call failed with status {response.status_code}")
            logger.error(f"Response: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error testing filtered agreements API: {e}")
        return False

def main():
    """Main function to run both tests"""
    logger.info("🚀 Starting DocuSign Agreements API Tests")
    
    # Test 1: Original method (get all and filter)
    logger.info("\n" + "="*60)
    logger.info("TEST 1: Get All Agreements and Filter Locally")
    logger.info("="*60)
    success_1 = test_agreements_api()
    
    # Test 2: New method (server-side filtering)
    logger.info("\n" + "="*60)
    logger.info("TEST 2: Server-Side Filtered Query")
    logger.info("="*60)
    success_2 = test_agreements_api_filtered()
    
    print("\n" + "=" * 60)
    print("📊 TEST RESULTS:")
    print(f"  Original method (get all + filter): {'✅ PASSED' if success_1 else '❌ FAILED'}")
    print(f"  Filtered method (server-side): {'✅ PASSED' if success_2 else '❌ FAILED'}")
    
    overall_success = success_1 and success_2
    if overall_success:
        print("✅ ALL TESTS PASSED! DocuSign Agreements API is working.")
    else:
        print("❌ SOME TESTS FAILED! Check your DocuSign configuration.")
    print("=" * 60)
    
    return overall_success

if __name__ == "__main__":
    success = main()
