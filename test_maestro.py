#!/usr/bin/env python3
"""
Test script for DocuSign Maestro API
Simple direct API call to test Maestro workflow trigger
"""

import json
import os
import requests
from datetime import datetime
from dotenv import load_dotenv

def test_maestro_api():
    """Test direct call to DocuSign Maestro API"""
    print("🎼 ========================================")
    print("🎼 TESTING MAESTRO API DIRECT CALL")
    print("🎼 ========================================")
    
    # Load environment variables
    load_dotenv()
    
    # Get required environment variables
    account_id = os.getenv('ACCOUNT_ID')  # Fixed variable name
    workflow_id = os.getenv('DOCUSIGN_WORKFLOW_ID')
    
    if not account_id:
        print("❌ ACCOUNT_ID not found in environment")
        return
    
    if not workflow_id:
        print("❌ DOCUSIGN_WORKFLOW_ID not found in environment")
        return
    
    print(f"🔑 Account ID: {account_id}")
    print(f"🔧 Workflow ID: {workflow_id}")
    
    # Get access token (you'd need to implement this part)
    try:
        from docusign_auth_manager import create_docusign_auth_manager
        auth_manager = create_docusign_auth_manager()
        access_token = auth_manager.get_access_token()
        print(f"✅ Access token obtained")
    except Exception as e:
        print(f"❌ Failed to get access token: {e}")
        return
    
    # Sample test data
    test_agreement_id = "test-agreement-maestro-123"
    test_bls_data = {
        "request_id": "test_maestro_20250815",
        "user_id": "test_user",
        "agreement_id": test_agreement_id,
        "agreement_details": {
            "agreement_id": test_agreement_id,
            "effective_date": "2024-01-01T00:00:00",
            "expiration_date": "2025-12-31T00:00:00",
            "original_contract_value": 1000.0,
            "currency": "USD"
        },
        "bls_data": {
            "original_contract_value": 1000.0,
            "adjusted_contract_value": 1050.0,
            "inflation_rate_applied": 0.05,
            "adjustment_date": "2025-08-15",
            "value_increase": 50.0
        }
    }
    
    print(f"📊 Test Agreement ID: {test_agreement_id}")
    print(f"💰 Original Value: ${test_bls_data['bls_data']['original_contract_value']}")
    print(f"💰 Adjusted Value: ${test_bls_data['bls_data']['adjusted_contract_value']}")
    print()
    
    # Prepare Maestro API request
    base_url = "https://api-d.docusign.com"
    endpoint = f"/v1/accounts/{account_id}/workflows/{workflow_id}/actions/trigger"
    url = f"{base_url}{endpoint}"
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    # Create payload for Maestro workflow (without "body" wrapper)
    test_request_id = "test_request_456" 
    instance_name = f"{test_agreement_id}_{test_request_id}"
    maestro_payload = {
        "instance_name": instance_name,
        "trigger_inputs": {
            "env_id": test_agreement_id,
            "payLoad": json.dumps(test_bls_data)
        }
    }
    
    print(f"🔗 Making API call to: {url}")
    print(f"📦 Payload:")
    print(json.dumps(maestro_payload, indent=2))
    print()
    
    try:
        # Make the API call
        response = requests.post(url, headers=headers, json=maestro_payload, timeout=30)
        
        print("🎼 ========================================")
        print("🎼 MAESTRO API RESPONSE")
        print("🎼 ========================================")
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        print()
        
        if response.status_code in [200, 201]:
            print("✅ SUCCESS!")
            try:
                response_data = response.json()
                print(f"Instance ID: {response_data.get('instance_id', 'N/A')}")
                print(f"Instance URL: {response_data.get('instance_url', 'N/A')}")
                print()
                print("📋 Full Response:")
                print(json.dumps(response_data, indent=2))
            except json.JSONDecodeError:
                print("Response is not JSON:")
                print(response.text)
        else:
            print("❌ ERROR!")
            try:
                error_data = response.json()
                print("Error Response:")
                print(json.dumps(error_data, indent=2))
            except json.JSONDecodeError:
                print("Error Response (not JSON):")
                print(response.text)
                
    except requests.exceptions.Timeout:
        print("❌ Request timed out")
    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

def main():
    """Main test function"""
    print("Starting Maestro API Test...")
    print()
    
    test_maestro_api()
    
    print()
    print("🎼 Test completed!")

if __name__ == "__main__":
    main()
