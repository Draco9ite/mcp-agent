#!/usr/bin/env python3
"""
Test MCP Integration with DocuSign Price Adjustment Agents
"""

import os
from dotenv import load_dotenv
from docusign_mcp_client import DocuSignMCPClient

def test_mcp_call_tool():
    """Test the new call_mcp_tool method"""
    load_dotenv()
    
    print("=" * 60)
    print("Testing MCP Tool Integration")
    print("=" * 60)
    
    # Initialize MCP client
    client = DocuSignMCPClient()
    
    if not client.access_token:
        print("❌ No access token found. Please run OAuth flow first.")
        print("   Run: python app.py")
        print("   Then visit: http://localhost:5001/docusign/oauth/start")
        return False
    
    print("✅ Access token loaded")
    
    # Test 1: Get all agreements
    try:
        print("\n" + "-" * 60)
        print("Test 1: getAllAgreements")
        print("-" * 60)
        
        account_id = os.getenv('ACCOUNT_ID', os.getenv('DOCUSIGN_ACCOUNT_ID'))
        params = {'accountId': account_id}
        
        result = client.call_mcp_tool('getAllAgreements', params)
        
        agreements = result.get('agreements', [])
        print(f"✅ Success! Found {len(agreements)} agreements")
        print(f"   Total count: {result.get('totalCount', 0)}")
        print(f"   Has more: {result.get('hasMore', False)}")
        
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False
    
    # Test 2: Get workflows list
    try:
        print("\n" + "-" * 60)
        print("Test 2: getWorkflowsList")
        print("-" * 60)
        
        params = {'accountId': account_id}
        result = client.call_mcp_tool('getWorkflowsList', params)
        
        workflows = result.get('workflows', {}).get('workflows', [])
        print(f"✅ Success! Found {len(workflows)} workflows")
        
        for wf in workflows:
            print(f"   - {wf.get('name')}: {wf.get('status')} (ID: {wf.get('id')})")
        
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False
    
    # Test 3: Get account info
    try:
        print("\n" + "-" * 60)
        print("Test 3: getAccount")
        print("-" * 60)
        
        params = {'accountId': account_id}
        result = client.call_mcp_tool('getAccount', params)
        
        print(f"✅ Success!")
        print(f"   Account Name: {result.get('accountName')}")
        print(f"   Account ID: {result.get('accountId')}")
        print(f"   Plan: {result.get('planName')}")
        
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("✅ All MCP integration tests passed!")
    print("=" * 60)
    return True

if __name__ == "__main__":
    success = test_mcp_call_tool()
    exit(0 if success else 1)
