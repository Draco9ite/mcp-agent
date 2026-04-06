#!/usr/bin/env python3
"""
Comprehensive test suite for all DocuSign MCP server tools.

This script tests all 25 available tools from the DocuSign MCP server,
including agreements, envelopes, workflows, accounts, templates, and more.

Usage:
    python scripts/test_all_mcp_tools.py [--tool TOOL_NAME] [--verbose]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configuration
MCP_SERVER_URL = os.getenv(
    'DOCUSIGN_MCP_BASE_URL',
    'https://services.demo.docusign.net/docusign-mcp-server/v1.0/mcp'
)
TOKENS_FILE = PROJECT_ROOT / ".mcp_tokens.json"
ACCOUNT_ID = os.getenv('ACCOUNT_ID', '999fac92-647f-4471-a16f-51f38abf2d83')
WORKFLOW_ID = os.getenv('DOCUSIGN_WORKFLOW_ID', '1fc6d7e9-613b-4843-8d79-29bbb07c015d')


def load_access_token():
    """Load the current access token from file."""
    if not TOKENS_FILE.exists():
        print(f"❌ Tokens file not found: {TOKENS_FILE}")
        print("   Run OAuth flow first: http://localhost:5001/docusign/oauth/start")
        sys.exit(1)
    
    with open(TOKENS_FILE, 'r') as f:
        tokens = json.load(f)
    
    return tokens.get('access_token')


def call_mcp_tool(tool_name, params, token):
    """
    Call an MCP tool with the given parameters.
    
    Args:
        tool_name: Name of the tool to call
        params: Dictionary of parameters for the tool
        token: OAuth access token
    
    Returns:
        Tuple of (success: bool, result: dict, error: str)
    """
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
    
    try:
        response = requests.post(MCP_SERVER_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            # Parse SSE response
            text = response.text
            if 'data: ' in text:
                json_str = text.split('data: ', 1)[1].strip()
                data = json.loads(json_str)
                
                if 'result' in data:
                    return True, data['result'], None
                elif 'error' in data:
                    return False, None, data['error'].get('message', str(data['error']))
            
            return False, None, f"Unexpected response format: {text[:200]}"
        else:
            return False, None, f"HTTP {response.status_code}: {response.text[:200]}"
            
    except requests.exceptions.Timeout:
        return False, None, "Request timeout (30s)"
    except Exception as e:
        return False, None, str(e)


def extract_text_content(result):
    """Extract text content from MCP result."""
    if isinstance(result, dict):
        if 'content' in result and isinstance(result['content'], list):
            for item in result['content']:
                if item.get('type') == 'text':
                    return item.get('text', '')
        return json.dumps(result, indent=2)
    return str(result)


class MCPToolTester:
    """Test harness for all MCP tools."""
    
    def __init__(self, token, verbose=False):
        self.token = token
        self.verbose = verbose
        self.results = {}
        
    def test_tool(self, tool_name, params, description=""):
        """Test a single tool and record results."""
        print(f"\n{'='*80}")
        print(f"Testing: {tool_name}")
        if description:
            print(f"Description: {description}")
        print(f"Parameters: {json.dumps(params, indent=2)}")
        print(f"{'='*80}")
        
        success, result, error = call_mcp_tool(tool_name, params, self.token)
        
        self.results[tool_name] = {
            'success': success,
            'result': result,
            'error': error
        }
        
        if success:
            print(f"✅ SUCCESS")
            if self.verbose and result:
                text_content = extract_text_content(result)
                print(f"\nResponse (first 500 chars):")
                print(text_content[:500])
                if len(text_content) > 500:
                    print("... (truncated)")
        else:
            print(f"❌ FAILED: {error}")
        
        return success
    
    # Agreement Management Tools
    def test_getAllAgreements(self):
        return self.test_tool(
            "getAllAgreements",
            {"accountId": ACCOUNT_ID},
            "Retrieve all agreements with enhanced monitoring"
        )
    
    def test_getAgreementDetails(self, agreement_id=None):
        if not agreement_id:
            print("⏭️  Skipping: No agreement_id provided")
            return False
        return self.test_tool(
            "getAgreementDetails",
            {"accountId": ACCOUNT_ID, "agreementId": agreement_id},
            "Get detailed information about a specific agreement"
        )
    
    # Envelope Management Tools
    def test_getEnvelopes(self):
        return self.test_tool(
            "getEnvelopes",
            {
                "accountId": ACCOUNT_ID,
                "from_date": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
                "count": "10"
            },
            "Search for envelopes from the last 30 days"
        )
    
    def test_getEnvelope(self, envelope_id=None):
        if not envelope_id:
            print("⏭️  Skipping: No envelope_id provided")
            return False
        return self.test_tool(
            "getEnvelope",
            {"accountId": ACCOUNT_ID, "envelopeId": envelope_id},
            "Get status of a single envelope"
        )
    
    def test_listRecipients(self, envelope_id=None):
        if not envelope_id:
            print("⏭️  Skipping: No envelope_id provided")
            return False
        return self.test_tool(
            "listRecipients",
            {"accountId": ACCOUNT_ID, "envelopeId": envelope_id},
            "Get recipient status for an envelope"
        )
    
    def test_createEnvelope(self, skip=True):
        if skip:
            print("⏭️  Skipping: createEnvelope (would create actual envelope)")
            return False
        
        # Example envelope creation (minimal)
        return self.test_tool(
            "createEnvelope",
            {
                "accountId": ACCOUNT_ID,
                "envelopeDefinition": {
                    "status": "created",
                    "emailSubject": "Test Envelope from MCP",
                    "documents": [{
                        "documentId": "1",
                        "name": "test.txt",
                        "documentBase64": "VGVzdCBkb2N1bWVudA==",  # "Test document" in base64
                        "fileExtension": "txt"
                    }],
                    "recipients": {
                        "signers": [{
                            "email": "test@example.com",
                            "name": "Test Signer",
                            "recipientId": "1"
                        }]
                    }
                }
            },
            "Create a draft envelope (test mode)"
        )
    
    def test_updateEnvelope(self, envelope_id=None, skip=True):
        if skip or not envelope_id:
            print("⏭️  Skipping: updateEnvelope (would modify actual envelope)")
            return False
        
        return self.test_tool(
            "updateEnvelope",
            {
                "accountId": ACCOUNT_ID,
                "envelopeId": envelope_id,
                "envelopeUpdate": {
                    "status": "sent"
                }
            },
            "Update an envelope (test mode)"
        )
    
    # Maestro Workflow Tools
    def test_getWorkflowsList(self):
        return self.test_tool(
            "getWorkflowsList",
            {"accountId": ACCOUNT_ID},
            "Retrieve list of Maestro workflows"
        )
    
    def test_getWorkflowTriggerRequirements(self):
        return self.test_tool(
            "getWorkflowTriggerRequirements",
            {"accountId": ACCOUNT_ID, "workflowId": WORKFLOW_ID},
            "Get trigger requirements for Price Adjustment workflow"
        )
    
    def test_triggerWorkflow(self, skip=True):
        if skip:
            print("⏭️  Skipping: triggerWorkflow (would trigger actual workflow)")
            return False
        
        return self.test_tool(
            "triggerWorkflow",
            {
                "accountId": ACCOUNT_ID,
                "workflowId": WORKFLOW_ID,
                "instance_name": f"Test Trigger {datetime.now().isoformat()}",
                "trigger_inputs": {
                    "startDate": datetime.now().strftime("%Y-%m-%d"),
                    "Total Value": "100000",
                    "Start_Date": datetime.now().strftime("%Y-%m-%d"),
                    "Expiry date": (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
                }
            },
            "Trigger Price Adjustment workflow (test mode)"
        )
    
    def test_getWorkflowInstancesList(self):
        return self.test_tool(
            "getWorkflowInstancesList",
            {"accountId": ACCOUNT_ID, "workflowId": WORKFLOW_ID},
            "Get all instances of Price Adjustment workflow"
        )
    
    def test_getWorkflowInstance(self, instance_id=None):
        if not instance_id:
            print("⏭️  Skipping: No instance_id provided")
            return False
        return self.test_tool(
            "getWorkflowInstance",
            {"accountId": ACCOUNT_ID, "workflowId": WORKFLOW_ID, "instanceId": instance_id},
            "Get specific workflow instance details"
        )
    
    def test_pauseNewWorkflowInstances(self, skip=True):
        if skip:
            print("⏭️  Skipping: pauseNewWorkflowInstances (would pause workflow)")
            return False
        return self.test_tool(
            "pauseNewWorkflowInstances",
            {"accountId": ACCOUNT_ID, "workflowId": WORKFLOW_ID},
            "Pause new workflow instances (test mode)"
        )
    
    def test_resumeWorkflow(self, skip=True):
        if skip:
            print("⏭️  Skipping: resumeWorkflow (would resume workflow)")
            return False
        return self.test_tool(
            "resumeWorkflow",
            {"accountId": ACCOUNT_ID, "workflowId": WORKFLOW_ID},
            "Resume paused workflow (test mode)"
        )
    
    def test_cancelWorkflowInstance(self, instance_id=None, skip=True):
        if skip or not instance_id:
            print("⏭️  Skipping: cancelWorkflowInstance (would cancel instance)")
            return False
        return self.test_tool(
            "cancelWorkflowInstance",
            {"accountId": ACCOUNT_ID, "workflowId": WORKFLOW_ID, "instanceId": instance_id},
            "Cancel workflow instance (test mode)"
        )
    
    # Account & User Tools
    def test_getAccount(self):
        return self.test_tool(
            "getAccount",
            {"accountId": ACCOUNT_ID},
            "Get account information"
        )
    
    def test_getUsers(self):
        return self.test_tool(
            "getUsers",
            {"accountId": ACCOUNT_ID, "count": "10"},
            "Get list of account users"
        )
    
    def test_getUser(self, user_id=None):
        if not user_id:
            user_id = os.getenv('USER_ID', '6b4c76d9-47fd-48d1-a15a-6f7bc3dac82d')
        return self.test_tool(
            "getUser",
            {"accountId": ACCOUNT_ID, "userId": user_id},
            "Get specific user details"
        )
    
    # Branding Tools
    def test_getBrands(self):
        return self.test_tool(
            "getBrands",
            {"accountId": ACCOUNT_ID},
            "Get list of configured brands"
        )
    
    def test_getBrand(self, brand_id=None):
        if not brand_id:
            print("⏭️  Skipping: No brand_id provided")
            return False
        return self.test_tool(
            "getBrand",
            {"accountId": ACCOUNT_ID, "brandId": brand_id},
            "Get specific brand details"
        )
    
    # Template Tools
    def test_getTemplates(self):
        return self.test_tool(
            "getTemplates",
            {"accountId": ACCOUNT_ID, "count": "10"},
            "Get list of templates"
        )
    
    # Connected Fields Tools
    def test_getTabGroups(self):
        return self.test_tool(
            "getTabGroups",
            {"accountId": ACCOUNT_ID},
            "Get Connected Fields tab groups"
        )
    
    # Billing Tools
    def test_listBillingPlans(self):
        return self.test_tool(
            "listBillingPlans",
            {},
            "List billing plans for distributor"
        )
    
    def test_getBillingPlan(self, billing_plan_id=None):
        if not billing_plan_id:
            print("⏭️  Skipping: No billing_plan_id provided")
            return False
        return self.test_tool(
            "getBillingPlan",
            {"billingPlanId": billing_plan_id},
            "Get specific billing plan details"
        )
    
    # AI Assistant Tools
    def test_queryRAG(self):
        return self.test_tool(
            "queryRAG",
            {"prompt": "What are the best practices for triggering Maestro workflows?"},
            "Query DocuSign RAG AI assistant"
        )
    
    def run_all_tests(self, skip_destructive=True):
        """Run all tool tests."""
        print("\n" + "="*80)
        print("DOCUSIGN MCP SERVER - COMPREHENSIVE TOOL TEST SUITE")
        print("="*80)
        print(f"MCP Server: {MCP_SERVER_URL}")
        print(f"Account ID: {ACCOUNT_ID}")
        print(f"Workflow ID: {WORKFLOW_ID}")
        print(f"Skip Destructive Tests: {skip_destructive}")
        print("="*80)
        
        # Agreement Management
        print("\n\n📁 AGREEMENT MANAGEMENT TOOLS")
        self.test_getAllAgreements()
        
        # Envelope Management
        print("\n\n📄 ENVELOPE MANAGEMENT TOOLS")
        self.test_getEnvelopes()
        self.test_createEnvelope(skip=skip_destructive)
        
        # Maestro Workflows
        print("\n\n🔄 MAESTRO WORKFLOW TOOLS")
        self.test_getWorkflowsList()
        self.test_getWorkflowTriggerRequirements()
        self.test_getWorkflowInstancesList()
        self.test_triggerWorkflow(skip=skip_destructive)
        self.test_pauseNewWorkflowInstances(skip=skip_destructive)
        self.test_resumeWorkflow(skip=skip_destructive)
        
        # Account & Users
        print("\n\n👤 ACCOUNT & USER TOOLS")
        self.test_getAccount()
        self.test_getUsers()
        self.test_getUser()
        
        # Branding
        print("\n\n🎨 BRANDING TOOLS")
        self.test_getBrands()
        
        # Templates
        print("\n\n📝 TEMPLATE TOOLS")
        self.test_getTemplates()
        
        # Connected Fields
        print("\n\n🔗 CONNECTED FIELDS TOOLS")
        self.test_getTabGroups()
        
        # Billing
        print("\n\n💳 BILLING TOOLS")
        self.test_listBillingPlans()
        
        # AI Assistant
        print("\n\n🤖 AI ASSISTANT TOOLS")
        self.test_queryRAG()
        
        # Summary
        self.print_summary()
    
    def print_summary(self):
        """Print test results summary."""
        print("\n\n" + "="*80)
        print("TEST RESULTS SUMMARY")
        print("="*80)
        
        passed = sum(1 for r in self.results.values() if r['success'])
        failed = sum(1 for r in self.results.values() if not r['success'])
        total = len(self.results)
        
        print(f"\nTotal Tests: {total}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")
        print(f"Success Rate: {(passed/total*100):.1f}%")
        
        if failed > 0:
            print("\n❌ Failed Tests:")
            for tool, result in self.results.items():
                if not result['success']:
                    print(f"   - {tool}: {result['error']}")
        
        print("\n" + "="*80)


def main():
    """Main test execution."""
    parser = argparse.ArgumentParser(description='Test all DocuSign MCP tools')
    parser.add_argument(
        '--tool',
        help='Test specific tool only',
        choices=[
            'getAllAgreements', 'getAgreementDetails', 'getEnvelopes', 'getEnvelope',
            'listRecipients', 'createEnvelope', 'updateEnvelope', 'getWorkflowsList',
            'getWorkflowTriggerRequirements', 'triggerWorkflow', 'getWorkflowInstancesList',
            'getWorkflowInstance', 'pauseNewWorkflowInstances', 'resumeWorkflow',
            'cancelWorkflowInstance', 'getAccount', 'getUsers', 'getUser', 'getBrands',
            'getBrand', 'getTemplates', 'getTabGroups', 'listBillingPlans',
            'getBillingPlan', 'queryRAG'
        ]
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed response data'
    )
    parser.add_argument(
        '--include-destructive',
        action='store_true',
        help='Include destructive tests (create/update/trigger operations)'
    )
    
    args = parser.parse_args()
    
    # Load token
    token = load_access_token()
    print(f"✅ Access token loaded (length: {len(token)} chars)")
    
    # Create tester
    tester = MCPToolTester(token, verbose=args.verbose)
    
    # Run tests
    if args.tool:
        # Run single tool test
        method = getattr(tester, f'test_{args.tool}', None)
        if method:
            method(skip=not args.include_destructive)
            tester.print_summary()
        else:
            print(f"❌ Unknown tool: {args.tool}")
            return 1
    else:
        # Run all tests
        tester.run_all_tests(skip_destructive=not args.include_destructive)
    
    # Exit with appropriate code
    failed = sum(1 for r in tester.results.values() if not r['success'])
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
