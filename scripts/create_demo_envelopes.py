#!/usr/bin/env python3
"""
Create Demo Envelopes Script
Creates multiple sample envelopes with realistic data for demo purposes.
Uses DocuSign MCP server to create envelopes with various expiry dates.
"""

import os
import sys
import json
import base64
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from docusign_mcp_client import DocuSignMCPClient

# Load environment variables
load_dotenv()

# Sample envelope configurations with realistic data
DEMO_ENVELOPES = [
    {
        "pdf_path": "Sample Documents for Navigator/Sales/Distribution Agreement.pdf",
        "subject": "Distribution Agreement - Acme Corp",
        "recipient_name": "John Smith",
        "recipient_email": "john.smith@acmecorp.com",
        "days_until_expiry": 15,
        "contract_value": "$250,000",
        "customer": "Acme Corporation"
    },
    {
        "pdf_path": "Sample Documents for Navigator/Procurement/Consulting.pdf",
        "subject": "Consulting Services Agreement - TechStart Inc",
        "recipient_name": "Sarah Johnson",
        "recipient_email": "sarah.j@techstart.com",
        "days_until_expiry": 7,
        "contract_value": "$180,000",
        "customer": "TechStart Inc"
    },
    {
        "pdf_path": "Sample Documents for Navigator/Legal/MasterLicenseAgreement.pdf",
        "subject": "Master License Agreement - GlobalSoft Ltd",
        "recipient_name": "Michael Chen",
        "recipient_email": "m.chen@globalsoft.com",
        "days_until_expiry": 25,
        "contract_value": "$500,000",
        "customer": "GlobalSoft Ltd"
    },
    {
        "pdf_path": "Sample Documents for Navigator/CX/Acme Cloud Services.pdf",
        "subject": "Cloud Services Agreement - DataFlow Systems",
        "recipient_name": "Emily Rodriguez",
        "recipient_email": "e.rodriguez@dataflow.com",
        "days_until_expiry": 5,
        "contract_value": "$320,000",
        "customer": "DataFlow Systems"
    },
    {
        "pdf_path": "Sample Documents for Navigator/Sales/Sales MoU.pdf",
        "subject": "Sales MoU - Innovation Partners",
        "recipient_name": "David Park",
        "recipient_email": "d.park@innovationpartners.com",
        "days_until_expiry": 20,
        "contract_value": "$450,000",
        "customer": "Innovation Partners"
    },
    {
        "pdf_path": "Sample Documents for Navigator/Procurement/Standard Terms.pdf",
        "subject": "Standard Terms Agreement - Blue Ocean Tech",
        "recipient_name": "Lisa Wang",
        "recipient_email": "lisa.wang@blueocean.com",
        "days_until_expiry": 10,
        "contract_value": "$275,000",
        "customer": "Blue Ocean Tech"
    },
    {
        "pdf_path": "Sample Documents for Navigator/Legal/Confidentiality Agreement.pdf",
        "subject": "Confidentiality Agreement - Summit Ventures",
        "recipient_name": "Robert Taylor",
        "recipient_email": "r.taylor@summitventures.com",
        "days_until_expiry": 30,
        "contract_value": "$150,000",
        "customer": "Summit Ventures"
    },
    {
        "pdf_path": "Price Adjustment download/Purchasing Agreement Price adjustment.pdf",
        "subject": "Purchasing Agreement - Metro Industries",
        "recipient_name": "Jennifer Lee",
        "recipient_email": "j.lee@metroindustries.com",
        "days_until_expiry": 12,
        "contract_value": "$390,000",
        "customer": "Metro Industries"
    }
]


def read_pdf_as_base64(pdf_path):
    """Read a PDF file and return its base64 encoded content."""
    full_path = Path(__file__).parent.parent / pdf_path
    if not full_path.exists():
        raise FileNotFoundError(f"PDF file not found: {full_path}")
    
    with open(full_path, 'rb') as pdf_file:
        pdf_content = pdf_file.read()
        return base64.b64encode(pdf_content).decode('utf-8')


def create_envelope(mcp_client, account_id, envelope_config):
    """Create a single envelope using the MCP client."""
    
    # Calculate expiry date
    expiry_date = datetime.now() + timedelta(days=envelope_config['days_until_expiry'])
    expiry_str = expiry_date.strftime('%Y-%m-%d')
    
    # Read and encode PDF
    try:
        pdf_base64 = read_pdf_as_base64(envelope_config['pdf_path'])
    except FileNotFoundError as e:
        print(f"❌ Error: {e}")
        return None
    
    # Extract filename from path
    filename = Path(envelope_config['pdf_path']).name
    
    # Create envelope definition
    envelope_definition = {
        "status": "sent",
        "emailSubject": envelope_config['subject'],
        "emailBlurb": f"Please review and sign this agreement. Contract Value: {envelope_config['contract_value']}. Expires on {expiry_str}.",
        "documents": [
            {
                "documentId": "1",
                "name": filename,
                "fileExtension": "pdf",
                "documentBase64": pdf_base64
            }
        ],
        "recipients": {
            "signers": [
                {
                    "recipientId": "1",
                    "email": envelope_config['recipient_email'],
                    "name": envelope_config['recipient_name'],
                    "routingOrder": "1",
                    "tabs": {
                        "signHereTabs": [
                            {
                                "documentId": "1",
                                "recipientId": "1",
                                "pageNumber": "1",
                                "xPosition": "100",
                                "yPosition": "200"
                            }
                        ],
                        "dateSignedTabs": [
                            {
                                "documentId": "1",
                                "recipientId": "1",
                                "pageNumber": "1",
                                "xPosition": "100",
                                "yPosition": "250"
                            }
                        ]
                    }
                }
            ]
        },
        "notification": {
            "useAccountDefaults": "false",
            "reminders": {
                "reminderEnabled": "true",
                "reminderDelay": "2",
                "reminderFrequency": "2"
            },
            "expirations": {
                "expireEnabled": "true",
                "expireAfter": str(envelope_config['days_until_expiry']),
                "expireWarn": "2"
            }
        },
        "customFields": {
            "textCustomFields": [
                {
                    "name": "ContractValue",
                    "value": envelope_config['contract_value'],
                    "show": "true",
                    "required": "false"
                },
                {
                    "name": "Customer",
                    "value": envelope_config['customer'],
                    "show": "true",
                    "required": "false"
                },
                {
                    "name": "ExpiryDate",
                    "value": expiry_str,
                    "show": "true",
                    "required": "false"
                }
            ]
        }
    }
    
    # Create payload for MCP
    payload = {
        "accountId": account_id,
        "envelopeDefinition": envelope_definition
    }
    
    try:
        print(f"📤 Creating envelope: {envelope_config['subject']}")
        print(f"   📅 Expires in {envelope_config['days_until_expiry']} days ({expiry_str})")
        print(f"   💰 Contract Value: {envelope_config['contract_value']}")
        print(f"   👤 Recipient: {envelope_config['recipient_name']} ({envelope_config['recipient_email']})")
        
        result = mcp_client.call_mcp_tool('createEnvelope', payload)
        
        if result and 'envelopeId' in result:
            print(f"   ✅ Created! Envelope ID: {result['envelopeId']}\n")
            return result
        else:
            print(f"   ❌ Failed to create envelope. Response: {result}\n")
            return None
            
    except Exception as e:
        print(f"   ❌ Error creating envelope: {e}\n")
        return None


def main():
    """Main function to create all demo envelopes."""
    
    print("=" * 80)
    print("DocuSign Demo Envelopes Creator")
    print("=" * 80)
    print()
    
    # Initialize MCP client
    try:
        mcp_client = DocuSignMCPClient()
        if not mcp_client.access_token:
            print("❌ Failed to initialize MCP client. Check your OAuth token.")
            sys.exit(1)
        print("✅ MCP client initialized successfully")
    except Exception as e:
        print(f"❌ Error initializing MCP client: {e}")
        sys.exit(1)
    
    # Get account ID
    account_id = os.getenv('ACCOUNT_ID') or os.getenv('DOCUSIGN_ACCOUNT_ID')
    if not account_id:
        print("❌ ACCOUNT_ID not found in environment variables")
        sys.exit(1)
    
    print(f"📋 Account ID: {account_id}")
    print(f"📦 Creating {len(DEMO_ENVELOPES)} demo envelopes...")
    print()
    
    # Create each envelope
    results = []
    success_count = 0
    failed_count = 0
    
    for i, envelope_config in enumerate(DEMO_ENVELOPES, 1):
        print(f"[{i}/{len(DEMO_ENVELOPES)}]")
        result = create_envelope(mcp_client, account_id, envelope_config)
        results.append(result)
        
        if result:
            success_count += 1
        else:
            failed_count += 1
    
    # Print summary
    print("=" * 80)
    print("Summary")
    print("=" * 80)
    print(f"✅ Successfully created: {success_count} envelopes")
    print(f"❌ Failed: {failed_count} envelopes")
    print(f"📊 Total: {len(DEMO_ENVELOPES)} envelopes")
    print()
    
    if success_count > 0:
        print("🎉 Demo envelopes created successfully!")
        print("📍 You can now:")
        print("   - View them in DocuSign at https://demo.docusign.net")
        print("   - Test your agents with expiring agreements")
        print("   - Run the Streamlit app to see the envelopes")
        print("   - Use getAllAgreements MCP tool to list them")
    
    print()


if __name__ == "__main__":
    main()
