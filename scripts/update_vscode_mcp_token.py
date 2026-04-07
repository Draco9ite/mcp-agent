#!/usr/bin/env python3
"""
Auto-update VS Code MCP configuration with fresh OAuth tokens.

This script reads the current access token from .mcp_tokens.json and updates
the Authorization header in .vscode/mcp.json to keep VS Code MCP authenticated.

Usage:
    python scripts/update_vscode_mcp_token.py

Can be run:
- Manually when needed
- Via cron job (every 7 hours recommended)
- As a pre-start hook in your workflow
"""

import json
import os
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
TOKENS_FILE = PROJECT_ROOT / ".mcp_tokens.json"
MCP_CONFIG_FILE = PROJECT_ROOT / ".vscode" / "mcp.json"


def read_current_token():
    """Read the current access token from .mcp_tokens.json."""
    if not TOKENS_FILE.exists():
        raise FileNotFoundError(f"Tokens file not found: {TOKENS_FILE}")
    
    with open(TOKENS_FILE, 'r') as f:
        tokens = json.load(f)
    
    access_token = tokens.get('access_token')
    if not access_token:
        raise ValueError("No access_token found in tokens file")
    
    return access_token


def update_mcp_config(new_token):
    """Update the Authorization header in .vscode/mcp.json with new token."""
    if not MCP_CONFIG_FILE.exists():
        raise FileNotFoundError(f"MCP config file not found: {MCP_CONFIG_FILE}")
    
    with open(MCP_CONFIG_FILE, 'r') as f:
        config = json.load(f)
    
    # Update the Authorization header for DOCUSIGN MCP server
    if 'servers' in config and 'DOCUSIGN MCP' in config['servers']:
        server_config = config['servers']['DOCUSIGN MCP']
        
        if 'headers' not in server_config:
            server_config['headers'] = {}
        
        # Update the bearer token
        server_config['headers']['Authorization'] = f"Bearer {new_token}"
        
        # Write back to file with nice formatting
        with open(MCP_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent='\t')
        
        print(f"✅ Updated VS Code MCP configuration with new token")
        print(f"   Config file: {MCP_CONFIG_FILE}")
        return True
    else:
        raise ValueError("DOCUSIGN MCP server not found in mcp.json configuration")


def main():
    """Main execution function."""
    try:
        print("Reading current OAuth token...")
        token = read_current_token()
        print(f"✅ Token found (length: {len(token)} chars)")
        
        print("\nUpdating VS Code MCP configuration...")
        update_mcp_config(token)
        
        print("\n✅ Done! VS Code MCP is now configured with the latest token.")
        print("   Note: Token expires in ~8 hours from when it was obtained.")
        print("   Run this script again before expiry to refresh.")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
