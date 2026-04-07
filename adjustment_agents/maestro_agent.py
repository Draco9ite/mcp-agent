import logging
import json
import os
import sys
import requests
from datetime import datetime
from dotenv import load_dotenv
from typing import Dict, Any

# Add the parent directory to the path to import database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_database_instance
from config import Config
from docusign_mcp_client import DocuSignMCPClient

logger = logging.getLogger(__name__)

async def trigger_maestro_workflow(input_data: str) -> str:
    """
    Trigger DocuSign Maestro workflow for agreement processing
    REAL IMPLEMENTATION - Calls DocuSign Maestro API to trigger workflows
    """
    try:
        logger.info(f"🎼 ========================================")
        logger.info(f"🎼 MAESTRO AGENT TOOL CALLED") 
        logger.info(f"🎼 ========================================")
        logger.info(f"🎼 FUNCTION CALLED WITH INPUT: {input_data}")

        # Get the singleton database instance
        db = get_database_instance()
        
        # Extract request_id quickly for immediate broadcast
        try:
            bls_results = json.loads(input_data)
            request_id = bls_results.get('request_id')
            if request_id:
                db.log_agent_activity(request_id, "maestro_agent", "MAESTRO_STARTED", {})
        except:
            pass
        
        # Load environment variables
        load_dotenv()
        
        # Parse input data - expecting JSON with BLS results
        try:
            bls_results = json.loads(input_data)
            
            # Extract data with multiple fallback patterns
            agreement_id = (
                bls_results.get('agreement_id') or 
                bls_results.get('agreement', {}).get('id') or
                bls_results.get('agreement_details', {}).get('agreement_id')
            )
            
            request_id = (
                bls_results.get('request_id') or 
                bls_results.get('agreement_details', {}).get('request_id')
            )
            
            user_id = (
                bls_results.get('user_id') or 
                bls_results.get('agreement_details', {}).get('user_id') or 
                'system'
            )
            
            logger.info(f"🎼 Processing Maestro workflow for agreement: {agreement_id}")
            logger.info(f"🎼 Request ID: {request_id}")
            logger.info(f"🎼 User ID: {user_id}")
            
            if not agreement_id:
                logger.error(f"🎼 ❌ Agreement ID not found in input data")
                return json.dumps({
                    "status": "MAESTRO_ERROR",
                    "message": "Agreement ID not found in input data",
                    "timestamp": datetime.utcnow().isoformat(),
                    "agent": "maestro_agent"
                })
            
            if not request_id:
                logger.error(f"🎼 ❌ Request ID not found in input data")
                return json.dumps({
                    "status": "MAESTRO_ERROR",
                    "message": "Request ID not found in input data",
                    "timestamp": datetime.utcnow().isoformat(),
                    "agent": "maestro_agent"
                })
            
            # Prefer calling Maestro via MCP server if available
            mcp_client = DocuSignMCPClient()
            account_id = os.getenv('ACCOUNT_ID')
            workflow_id = os.getenv('DOCUSIGN_WORKFLOW_ID')
            access_token = None
            try:
                if mcp_client.access_token and workflow_id:
                    logger.info('🔗 MCP client initialized successfully')
                    # Call MCP tool for Maestro trigger (using triggerWorkflow tool)
                    load_dotenv()
                    mcp_tool_maestro = os.getenv('DOCUSIGN_MCP_TOOL_MAESTRO_TRIGGER', 'triggerWorkflow')
                    
                    # Prepare trigger inputs for the workflow
                    trigger_inputs = {
                        'startDate': bls_results.get('effective_date', ''),
                        'Start_Date': bls_results.get('effective_date', ''),
                        'Total Value': str(bls_results.get('adjustment_amount', 0)),
                        'Expiry date': bls_results.get('expiry_date', '')
                    }
                    
                    payload = {
                        'accountId': account_id,
                        'workflowId': workflow_id,
                        'trigger_inputs': trigger_inputs,
                        'instance_name': f"Price Adjustment - Request {request_id}"
                    }
                    try:
                        logger.info(f'🔗 Using MCP server for Maestro workflow trigger (tool: {mcp_tool_maestro})')
                        mcp_response = mcp_client.call_mcp_tool(mcp_tool_maestro, payload)
                        logger.info(f'🎼 ✅ Maestro workflow triggered successfully via MCP server')
                        maestro_response = mcp_response
                        maestro_success = True
                    except Exception as mcp_err:
                        logger.warning(f'🎼 ⚠️ MCP Maestro trigger failed: {mcp_err} - falling back to direct API')
                        maestro_response = None
                        maestro_success = False

            except Exception as e:
                logger.warning(f'🔗 ⚠️ MCP client initialization failed: {e}')
                mcp_client = None
                maestro_response = None
                maestro_success = False

            if not maestro_response:
                # Use MCP client token for direct API fallback
                logger.info('📡 Using direct DocuSign API for Maestro workflow trigger')
                if mcp_client and mcp_client.access_token:
                    access_token = mcp_client.access_token
                else:
                    logger.error('🎼 ❌ No valid access token available for Maestro API call')
                    return json.dumps({
                        "status": "MAESTRO_ERROR",
                        "message": "No valid access token available. Please re-authenticate via OAuth.",
                        "timestamp": datetime.utcnow().isoformat(),
                        "agent": "maestro_agent"
                    })
            
            # Get workflow ID from environment (if not already set)
            if not workflow_id:
                workflow_id = os.getenv('DOCUSIGN_WORKFLOW_ID')
            if not workflow_id:
                logger.error(f"🎼 ❌ DOCUSIGN_WORKFLOW_ID not found in environment")
                return json.dumps({
                    "status": "MAESTRO_ERROR",
                    "message": "DOCUSIGN_WORKFLOW_ID not configured",
                    "timestamp": datetime.utcnow().isoformat(),
                    "agent": "maestro_agent"
                })
            
            # Prepare Maestro API request
            base_url = "https://api-d.docusign.com"
            endpoint = f"/v1/accounts/{account_id}/workflows/{workflow_id}/actions/trigger"
            url = f"{base_url}{endpoint}"
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            # Create payload for Maestro workflow (direct payload without "body" wrapper)
            instance_name = f"{agreement_id}_{request_id}"  # request_id is now required
            maestro_payload = {
                "instance_name": instance_name,  # Use consistent naming with request_id
                "trigger_inputs": {
                    "env_id": agreement_id,     # Agreement ID as env_id
                    "payLoad": json.dumps(bls_results)  # Complete BLS data as JSON string
                }
            }
            
            logger.info(f"🎼 🔗 Making Maestro API call to: {url}")
            logger.info(f"🎼 📊 Instance name: {instance_name}")
            
            # Make the API call
            response = requests.post(url, headers=headers, json=maestro_payload)
            
            logger.info(f"🎼 📊 Response Status: {response.status_code}")
            
            if response.status_code in [200, 201]:
                # Success case
                maestro_response = response.json()
                maestro_success = True
                
                logger.info(f"🎼 ✅ Maestro workflow triggered successfully")
                logger.info(f"🎼 📋 Instance ID: {maestro_response.get('instance_id', 'N/A')}")
                logger.info(f"🎼 🔗 Instance URL: {maestro_response.get('instance_url', 'N/A')}")
                
            else:
                # Error case
                try:
                    error_response = response.json()
                except:
                    error_response = {"error": response.text}
                
                maestro_response = {
                    "error": True,
                    "status_code": response.status_code,
                    "response": error_response
                }
                maestro_success = False
                
                logger.error(f"🎼 ❌ Maestro API call failed: {response.status_code}")
                logger.error(f"🎼 📋 Response: {response.text}")
            
            # Update database with Maestro response (always update by agreement_id)
            try:
                db.update_maestro_workflow_status(
                    agreement_id=agreement_id,
                    maestro_response=json.dumps(maestro_response),
                    maestro_success=maestro_success,
                    request_id=request_id
                )
                logger.info(f"🎼 ✅ Updated database with Maestro status for agreement: {agreement_id}")
                
            except Exception as db_error:
                logger.error(f"🎼 ❌ Database update failed: {db_error}")
                # Continue processing even if DB update fails
            
            # Prepare final response based on API call result
            if maestro_success:
                result = {
                    "status": "MAESTRO_TRIGGERED",
                    "request_id": request_id,
                    "user_id": user_id,
                    "agreement_id": agreement_id,
                    "maestro_triggered": maestro_success,
                    "maestro_response": maestro_response,
                    "timestamp": datetime.utcnow().isoformat(),
                    "agent": "maestro_agent"
                }
                
                logger.info(f"🎼 ✅ MAESTRO AGENT COMPLETED: Agreement {agreement_id}")
                logger.info(f"🎼 📊 STATUS: MAESTRO_TRIGGERED")
                
                # Log completion for UI broadcasting
                try:
                    db.log_agent_activity(request_id, "maestro_agent", "MAESTRO_TRIGGERED", {
                        "agreement_id": agreement_id,
                        "maestro_triggered": maestro_success
                    })
                except:
                    pass
                
            else:
                result = {
                    "status": "MAESTRO_ERROR",
                    "request_id": request_id,
                    "user_id": user_id,
                    "agreement_id": agreement_id,
                    "maestro_triggered": maestro_success,
                    "error": maestro_response,
                    "timestamp": datetime.utcnow().isoformat(),
                    "agent": "maestro_agent"
                }
                
                logger.error(f"🎼 ❌ MAESTRO AGENT ERROR: Agreement {agreement_id}")
                logger.error(f"🎼 📊 STATUS: MAESTRO_ERROR")
            
            return json.dumps(result)
            
        except json.JSONDecodeError as e:
            logger.error(f"🎼 ❌ Invalid JSON input: {e}")
            return json.dumps({
                "status": "MAESTRO_ERROR",
                "message": f"Invalid JSON input: {str(e)}",
                "timestamp": datetime.utcnow().isoformat(),
                "agent": "maestro_agent"
            })
        except Exception as api_error:
            logger.error(f"🎼 ❌ Maestro API error: {api_error}")
            return json.dumps({
                "status": "MAESTRO_ERROR",
                "message": f"API call failed: {api_error}",
                "timestamp": datetime.utcnow().isoformat(),
                "agent": "maestro_agent"
            })
            
    except Exception as e:
        logger.error(f"🎼 ❌ Error in trigger_maestro_workflow: {e}")
        return json.dumps({
            "status": "MAESTRO_ERROR",
            "message": f"Tool execution failed: {e}",
            "timestamp": datetime.utcnow().isoformat(),
            "agent": "maestro_agent"
        })
