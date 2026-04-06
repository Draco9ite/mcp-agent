#!/usr/bin/env python3
"""
Test MCP client with both authentication methods.

This script tests DocuSign MCP server connectivity using:
1. Bearer token authentication (current working method)
2. OAuth2 flow authentication (if supported by MCP client)

Usage:
    python scripts/test_mcp_auth.py [--method bearer|oauth|both]
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from docusign_mcp_client import DocuSignMCPClient

# Configuration
MCP_SERVER_URL = os.getenv(
    'DOCUSIGN_MCP_BASE_URL',
    'https://services.demo.docusign.net/docusign-mcp-server/v1'
)
TOKENS_FILE = PROJECT_ROOT / ".mcp_tokens.json"


def load_tokens():
    """Load OAuth tokens from file."""
    if not TOKENS_FILE.exists():
        print(f"❌ Tokens file not found: {TOKENS_FILE}")
        print("   Run the OAuth flow first: http://localhost:5001/docusign/oauth/start")
        sys.exit(1)
    
    with open(TOKENS_FILE, 'r') as f:
        return json.load(f)


def test_bearer_auth():
    """Test MCP server with Bearer token authentication."""
    print("\n" + "="*60)
    print("TEST 1: Bearer Token Authentication")
    print("="*60)
    
    tokens = load_tokens()
    access_token = tokens.get('access_token')
    
    if not access_token:
        print("❌ No access token found in tokens file")
        return False
    
    print(f"✓ Token loaded (length: {len(access_token)} chars)")
    print(f"✓ Token scope: {tokens.get('scope', 'unknown')}")
    print(f"\nTesting MCP server URL: {MCP_SERVER_URL}")
    
    # Test 1: Server info/discovery
    print("\n--- Test 1a: Server Discovery ---")
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.get(MCP_SERVER_URL, headers=headers, timeout=10)
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ SUCCESS: Server responded")
            try:
                data = response.json()
                print(f"Response: {json.dumps(data, indent=2)}")
            except:
                print(f"Response (text): {response.text[:500]}")
            return True
        elif response.status_code == 404:
            print("❌ FAIL: Server not found (404)")
            print("   This means the MCP server hasn't been provisioned yet")
        elif response.status_code == 401:
            print("❌ FAIL: Unauthorized (401)")
            print("   Token may be invalid or expired")
        elif response.status_code == 403:
            print("❌ FAIL: Forbidden (403)")
            print("   Token valid but lacks necessary permissions")
        else:
            print(f"❌ FAIL: Unexpected status code")
            print(f"Response: {response.text[:500]}")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ FAIL: Connection error: {e}")
        return False
    
    # Test 2: Using DocuSignMCPClient
    print("\n--- Test 1b: Using DocuSignMCPClient ---")
    try:
        client = DocuSignMCPClient()
        server_info = client.get_server_info()
        print("✅ SUCCESS: MCP client retrieved server info")
        print(f"Response: {json.dumps(server_info, indent=2)}")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def test_oauth_flow():
    """Test MCP server with OAuth2 flow authentication."""
    print("\n" + "="*60)
    print("TEST 2: OAuth2 Flow Authentication")
    print("="*60)
    
    tokens = load_tokens()
    
    print(f"✓ OAuth tokens loaded")
    print(f"✓ Access token: {tokens.get('access_token', 'N/A')[:50]}...")
    print(f"✓ Refresh token: {'Present' if tokens.get('refresh_token') else 'Missing'}")
    print(f"✓ Expires in: {tokens.get('expires_in', 'unknown')} seconds")
    print(f"✓ Token scope: {tokens.get('scope', 'unknown')}")
    
    # Test token refresh capability
    print("\n--- Test 2a: Token Refresh ---")
    try:
        client = DocuSignMCPClient()
        
        # Force token refresh by calling private method (for testing)
        print("Attempting token refresh...")
        new_tokens = client._refresh_access_token()
        
        if new_tokens and new_tokens.get('access_token'):
            print("✅ SUCCESS: Token refresh works")
            print(f"New token (first 50 chars): {new_tokens['access_token'][:50]}...")
            return True
        else:
            print("❌ FAIL: Token refresh returned no data")
            return False
            
    except Exception as e:
        print(f"❌ FAIL: Token refresh error: {e}")
        return False


def test_mcp_tools():
    """Test calling specific MCP tools."""
    print("\n" + "="*60)
    print("TEST 3: MCP Tool Invocation")
    print("="*60)
    
    print("\nNote: This test will fail until MCP server is provisioned")
    print("      and we have the correct tool paths from discovery.\n")
    
    client = DocuSignMCPClient()
    
    # Test 1: Search tool (if we had the correct path)
    tool_path = os.getenv('DOCUSIGN_MCP_TOOL_SEARCH', 'tools/docusign/search')
    print(f"--- Test 3a: Search Tool ({tool_path}) ---")
    
    try:
        result = client.call_mcp_server(
            tool_path,
            method='POST',
            data={'query': 'expiring agreements'}
        )
        print("✅ SUCCESS: Search tool responded")
        print(f"Response: {json.dumps(result, indent=2)}")
    except Exception as e:
        print(f"❌ FAIL: {e}")
    
    # Test 2: Maestro tool (if we had the correct path)
    tool_path = os.getenv('DOCUSIGN_MCP_TOOL_MAESTRO_TRIGGER', 'tools/maestro/trigger')
    print(f"\n--- Test 3b: Maestro Tool ({tool_path}) ---")
    
    try:
        result = client.call_mcp_server(
            tool_path,
            method='POST',
            data={'workflow_id': 'test'}
        )
        print("✅ SUCCESS: Maestro tool responded")
        print(f"Response: {json.dumps(result, indent=2)}")
    except Exception as e:
        print(f"❌ FAIL: {e}")


def main():
    """Main test execution."""
    parser = argparse.ArgumentParser(description='Test MCP authentication methods')
    parser.add_argument(
        '--method',
        choices=['bearer', 'oauth', 'tools', 'all'],
        default='all',
        help='Which authentication method to test'
    )
    args = parser.parse_args()
    
    print("="*60)
    print("DocuSign MCP Authentication Test Suite")
    print("="*60)
    print(f"MCP Server: {MCP_SERVER_URL}")
    print(f"Tokens File: {TOKENS_FILE}")
    
    results = {}
    
    if args.method in ['bearer', 'all']:
        results['bearer'] = test_bearer_auth()
    
    if args.method in ['oauth', 'all']:
        results['oauth'] = test_oauth_flow()
    
    if args.method in ['tools', 'all']:
        test_mcp_tools()
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    for method, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{method.upper()}: {status}")
    
    print("\n" + "="*60)
    print("NEXT STEPS:")
    print("="*60)
    
    if not any(results.values()):
        print("1. Verify OAuth tokens are valid:")
        print("   curl http://localhost:5001/docusign/token_status")
        print("\n2. Contact DocuSign to provision MCP server at:")
        print(f"   {MCP_SERVER_URL}")
        print("\n3. Once server is live, run discovery:")
        print("   curl http://localhost:5001/docusign/discover")
    else:
        print("✅ Authentication working!")
        print("\n1. Run discovery to get tool paths:")
        print("   curl http://localhost:5001/docusign/discover")
        print("\n2. Update .env with exact tool paths")
        print("\n3. Test agents with real MCP calls")
    
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
