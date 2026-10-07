#!/usr/bin/env python3
"""
Simple Python script to call DocuSign MCP tools.
Equivalent to the curl command but in Python.

Usage:
    python scripts/call_mcp_tool.py getWorkflowsList
    python scripts/call_mcp_tool.py getAllAgreements
    python scripts/call_mcp_tool.py queryRAG "How do I trigger workflows?"
"""

import json
import os
import sys
from pathlib import Path

import requests

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
TOKENS_FILE = PROJECT_ROOT / ".mcp_tokens.json"
# One source of truth for the endpoint; see docusign_mcp_client.
from docusign_mcp_client import default_mcp_server_url

MCP_SERVER_URL = os.getenv('DOCUSIGN_MCP_BASE_URL') or default_mcp_server_url()
ACCOUNT_ID = "999fac92-647f-4471-a16f-51f38abf2d83"
WORKFLOW_ID = "1fc6d7e9-613b-4843-8d79-29bbb07c015d"


def load_token():
    """Load access token from .mcp_tokens.json"""
    with open(TOKENS_FILE, 'r') as f:
        tokens = json.load(f)
    return tokens['access_token']


def call_mcp_tool(tool_name, params):
    """Call an MCP tool with given parameters."""
    token = load_token()
    
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream'
    }
    
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {
                "params": params
            }
        },
        "id": 1
    }
    
    response = requests.post(MCP_SERVER_URL, headers=headers, json=payload)
    
    # Parse SSE response
    text = response.text
    if 'data: ' in text:
        json_str = text.split('data: ', 1)[1].strip()
        data = json.loads(json_str)
        
        # Extract text content if available
        if 'result' in data and 'content' in data['result']:
            for item in data['result']['content']:
                if item.get('type') == 'text':
                    result = json.loads(item['text'])
                    print(json.dumps(result, indent=2))
                    return result
        
        print(json.dumps(data, indent=2))
        return data
    
    print(text)
    return None


def main():
    """Main execution."""
    if len(sys.argv) < 2:
        print("Usage: python call_mcp_tool.py <tool_name> [additional_params...]")
        print("\nExamples:")
        print("  python call_mcp_tool.py getWorkflowsList")
        print("  python call_mcp_tool.py getAllAgreements")
        print("  python call_mcp_tool.py getAccount")
        print('  python call_mcp_tool.py queryRAG "How do I trigger workflows?"')
        sys.exit(1)
    
    tool_name = sys.argv[1]
    
    # Build parameters based on tool
    params = {"accountId": ACCOUNT_ID}
    
    # Tool-specific parameters
    if tool_name in ['getWorkflowsList', 'getWorkflowTriggerRequirements', 
                      'getWorkflowInstancesList', 'pauseNewWorkflowInstances',
                      'resumeWorkflow']:
        if tool_name != 'getWorkflowsList':
            params["workflowId"] = WORKFLOW_ID
    
    elif tool_name == 'queryRAG':
        if len(sys.argv) > 2:
            params = {"prompt": sys.argv[2]}
        else:
            params = {"prompt": "What are the best practices for triggering Maestro workflows?"}
    
    elif tool_name == 'listBillingPlans':
        params = {}
    
    # Call the tool
    print(f"🔧 Calling MCP tool: {tool_name}")
    print(f"📋 Parameters: {json.dumps(params, indent=2)}")
    print(f"\n{'='*80}\n")
    
    call_mcp_tool(tool_name, params)


if __name__ == "__main__":
    main()
