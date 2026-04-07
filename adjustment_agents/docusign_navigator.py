#!/usr/bin/env python3
"""
DocuSign Navigator Agent
Handles DocuSign API integration to find agreements expiring in the next 30 days.
This agent searches for agreements, filters by expiration date, and stores results in database.
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

# Import modules from parent package
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_database_instance
from docusign_mcp_client import DocuSignMCPClient

# Configure logging
logger = logging.getLogger(__name__)

class DocuSignNavigatorAgent:
    """
    DocuSign Navigator Agent for finding agreements expiring in next 30 days.
    Integrates with DocuSign API and stores results in database.
    """
    
    def __init__(self, db=None):
        """
        Initialize DocuSign Navigator Agent
        """
        # Use provided database or get singleton instance
        self.db = db or get_database_instance()
        self.base_url = "https://api-d.docusign.com"
    
    def navigate_docusign_documents(self, request_id: str) -> Dict[str, Any]:
        """
        Main function to find agreements expiring in next 30 days.
        This function is called by the AI agent framework.
        
        Args:
            request_id: The request ID for tracking this operation
            
        Returns:
            Dict containing the results of the DocuSign search operation
        """
        try:
            logger.info("🔍 Starting DocuSign Navigator - Finding agreements expiring in next 30 days")
            
            # Calculate date range (next 30 days) up-front (used by MCP or fallback)
            today = datetime.now()
            thirty_days_later = today + timedelta(days=30)
            today_str = today.strftime("%Y-%m-%d")
            cutoff_str = thirty_days_later.strftime("%Y-%m-%d")

            # Try using MCP server for agreement search (endpoint configurable via env)
            mcp_client = DocuSignMCPClient()
            account_id = os.getenv('ACCOUNT_ID')
            agreements = []
            try:
                if mcp_client.access_token:
                    logger.info('🔗 MCP client initialized successfully')
                    # Allow overriding the MCP tool path via env var for discovery/flexibility
                    load_dotenv()
                    mcp_tool_search = os.getenv('DOCUSIGN_MCP_TOOL_SEARCH', 'getAllAgreements')
                    
                    payload = {
                        'accountId': account_id
                    }
                    try:
                        logger.info(f'🔗 Using MCP server for DocuSign search (tool: {mcp_tool_search})')
                        mcp_result = mcp_client.call_mcp_tool(mcp_tool_search, payload)
                        agreements = mcp_result.get('agreements', [])
                        logger.info(f'🔗 ✅ MCP search successful - returned {len(agreements)} total agreements')
                    except Exception as me:
                        logger.warning(f'🔗 ⚠️ MCP search failed: {me}')
                        agreements = []
            except Exception as e:
                logger.warning(f'🔗 ⚠️ MCP client initialization failed: {e}')
                agreements = []
            
            logger.info(f"✅ Authentication successful, Account ID: {account_id}")
            
            logger.info(f"📅 Searching for agreements expiring between {today_str} and {cutoff_str}")
            
            # Fetch agreements expiring in next 30 days using server-side filtering
            expiring_agreements = agreements
            logger.info(f"🎯 Found {len(expiring_agreements)} agreements expiring in next 30 days")
            
            # Log agreements found for UI broadcasting
            try:
                self.db.log_agent_activity(request_id, "docusign_navigator", "AGREEMENTS_FOUND", {
                    "expiring_agreements_found": len(expiring_agreements)
                })
            except:
                pass
            
            # Get the actual user_id from the adjustment_requests table
            try:
                with self.db._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT user_id FROM adjustment_requests WHERE id = ?', (request_id,))
                    row = cursor.fetchone()
                    user_id = row['user_id'] if row else 'system'
            except Exception as e:
                logger.warning(f"🔍 ⚠️ Could not retrieve user_id from database: {e}")
                user_id = 'system'  # fallback on error
            
            # Store agreements in database and track reasons
            stored_count, skipped_reasons = self._store_agreements_in_database(request_id, expiring_agreements, user_id)
            
            # Log the run details with actual user_id
            skipped_count = len(expiring_agreements) - stored_count
            self.db.log_agreement_run(
                user_id=user_id,
                found=len(expiring_agreements),
                processed=stored_count,
                skipped=skipped_count
            )
            
            # Log detailed reasons for user clarity
            if skipped_count > 0:
                logger.info(f"📝 Processing Summary: {skipped_count} agreements were skipped")
                for reason, count in skipped_reasons.items():
                    logger.info(f"📝 - {count} agreements skipped: {reason}")
            else:
                logger.info(f"📝 All {len(expiring_agreements)} expiring agreements were successfully processed")
            
            # Prepare response with detailed skip reasons
            result = {
                "status": "success",
                "message": "DocuSign Navigator completed successfully",
                "details": {
                    "expiring_agreements_found": len(expiring_agreements),
                    "agreements_stored": stored_count,
                    "agreements_skipped": skipped_count,
                    "skip_reasons": skipped_reasons,
                    "date_range": {
                        "start": today_str,
                        "end": cutoff_str
                    }
                },
                "expiring_agreements": self._format_agreements_summary(expiring_agreements)
            }
            
            logger.info("✅ DocuSign Navigator completed successfully")
            
            # Log completion for UI broadcasting
            try:
                self.db.log_agent_activity(request_id, "docusign_navigator", "DOCUSIGN_COMPLETE", {
                    "expiring_agreements_found": len(expiring_agreements),
                    "agreements_stored": stored_count
                })
            except:
                pass
            
            return result
            
        except Exception as e:
            error_msg = f"DocuSign Navigator failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            return {
                "status": "error",
                "message": error_msg,
                "details": {}
            }
    
    def _store_agreements_in_database(self, request_id: str, agreements: List[Dict], user_id: str = "system") -> tuple[int, Dict[str, int]]:
        """
        Store agreements in the database with detailed skip tracking.
        
        Args:
            request_id: Request ID for tracking
            agreements: List of agreements to store
            user_id: User who initiated the request
            
        Returns:
            Tuple of (stored_count, skip_reasons_dict)
        """
        stored_count = 0
        skip_reasons = {
            "Already exists in database": 0,
            "Invalid agreement data": 0,
            "Database error": 0
        }
        
        for agreement in agreements:
            try:
                agreement_id = agreement.get('id', 'Unknown')
                
                # Validate agreement data
                if not agreement_id or agreement_id == 'Unknown':
                    skip_reasons["Invalid agreement data"] += 1
                    logger.debug(f"⚠️ Skipped agreement with missing/invalid ID")
                    continue
                
                # Store agreement using the database method with user_id
                if self.db.store_agreement(request_id, agreement, user_id):
                    stored_count += 1
                    logger.debug(f"✅ Stored new agreement: {agreement_id}")
                else:
                    skip_reasons["Already exists in database"] += 1
                    logger.debug(f"⚠️ Skipped existing agreement: {agreement_id}")
                    
            except Exception as e:
                skip_reasons["Database error"] += 1
                logger.error(f"❌ Failed to store agreement {agreement.get('id', 'Unknown')}: {e}")
        
        # Clean up empty skip reasons
        skip_reasons = {k: v for k, v in skip_reasons.items() if v > 0}
        
        logger.info(f"💾 Stored {stored_count} new agreements in database")
        return stored_count, skip_reasons
    
    def _format_agreements_summary(self, agreements: List[Dict]) -> List[Dict]:
        """
        Format agreements for summary output.
        
        Args:
            agreements: List of agreements
            
        Returns:
            List of formatted agreement summaries
        """
        summaries = []
        
        for agreement in agreements:
            summary = {
                "id": agreement.get('id', 'N/A'),
                "title": agreement.get('title', 'Unknown'),
                "status": agreement.get('status', 'N/A'),
                "expiration_date": agreement.get('provisions', {}).get('expiration_date', 'No date'),
                "created_date": agreement.get('created_date', 'N/A')
            }
            summaries.append(summary)
        
        return summaries
    
    def get_stored_agreements(self) -> List[str]:
        """
        Get list of stored agreement IDs from database.
        
        Returns:
            List of agreement IDs
        """
        try:
            return self.db.get_stored_agreement_ids()
        except Exception as e:
            logger.error(f"❌ Failed to get stored agreements: {e}")
            return []
    
    def get_agreement_details(self, agreement_id: str) -> Optional[Dict]:
        """
        Get full details of a specific agreement from database.
        
        Args:
            agreement_id: Agreement ID to retrieve
            
        Returns:
            Agreement data or None if not found
        """
        try:
            return self.db.get_agreement_details(agreement_id)
        except Exception as e:
            logger.error(f"❌ Failed to get agreement details for {agreement_id}: {e}")
            return None


# Agent tool function — called by the Assistants API run loop
def navigate_docusign_documents(request_id: str) -> str:
    """
    Find DocuSign agreements expiring in next 30 days.
    """
    try:
        # Get the singleton database instance
        db = get_database_instance()
        
        logger.info(f"🔍 ========================================")
        logger.info(f"🔍 DOCUSIGN NAVIGATOR AGENT TOOL CALLED")
        logger.info(f"🔍 ========================================")
        logger.info(f"🔍 FUNCTION CALLED WITH REQUEST_ID: {request_id}")

        # Log agent start activity for UI broadcasting
        try:
            db.log_agent_activity(request_id, "docusign_navigator", "DOCUSIGN_NAVIGATOR_STARTED", {})
        except:
            pass

        # Validate request_id format
        if not request_id or not isinstance(request_id, str):
            logger.error(f"🔍 ❌ Invalid request_id provided: {request_id}")
            return json.dumps({
                "status": "error",
                "message": "Invalid request_id provided",
                "request_id": request_id
            })
        
        # Clean the request_id (remove any whitespace/newlines)
        request_id = request_id.strip()
        
        # Validate request_id exists in database
        user_id = "system"  # default
        try:
            with db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT user_id FROM adjustment_requests WHERE id = ?', (request_id,))
                row = cursor.fetchone()
                if row:
                    user_id = row['user_id']
                    logger.info(f"🔍 ✅ Found request in database - User: {user_id}")
                else:
                    logger.error(f"🔍 ❌ Request {request_id} not found in database")
                    return json.dumps({
                        "status": "error",
                        "message": f"Request {request_id} not found in database",
                        "request_id": request_id
                    })
        except Exception as e:
            logger.error(f"🔍 ❌ Database error: {e}")
            return json.dumps({
                "status": "error",
                "message": f"Database error: {str(e)}",
                "request_id": request_id
            })
        
        logger.info(f"🔍 Processing request ID: {request_id}")
        logger.info(f"🔍 User ID: {user_id}")
        
        # Create navigator and run the DocuSign search (FIX: Use the instance directly, don't call twice)
        navigator = DocuSignNavigatorAgent(db)
        result = navigator.navigate_docusign_documents(request_id)
        
        # Update the result to include proper user_id tracking
        result['user_id'] = user_id
        result['request_id'] = request_id
        
        # Log the completion status
        if result.get('status') == 'success':
            logger.info(f"🔍 ✅ DocuSign Navigator completed successfully")
            logger.info(f"🔍 📊 Found {result['details']['expiring_agreements_found']} expiring agreements")
            logger.info(f"🔍 💾 Stored {result['details']['agreements_stored']} new agreements")
        else:
            logger.error(f"🔍 ❌ DocuSign Navigator failed: {result.get('message', 'Unknown error')}")
        
        # Return JSON as string (matching the pattern of other agents)
        return json.dumps(result)
        
    except Exception as e:
        error_msg = f"DocuSign Navigator tool failed: {str(e)}"
        logger.error(f"🔍 ❌ {error_msg}")
        error_result = {
            "status": "error", 
            "message": error_msg,
            "details": {},
            "request_id": request_id if 'request_id' in locals() else f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "user_id": user_id if 'user_id' in locals() else 'system'
        }
        return json.dumps(error_result)


# Tool function — called by the Assistants API run loop
def get_unprocessed_agreements(request_id: str) -> str:
    """
    Get agreements from database that haven't been processed yet
    """
    try:
        # Get the singleton database instance
        db = get_database_instance()
        
        logger.info(f"📋 ========================================")
        logger.info(f"📋 GET UNPROCESSED AGREEMENTS CALLED")
        logger.info(f"📋 ========================================")
        logger.info(f"📋 FUNCTION CALLED WITH REQUEST_ID: {request_id}")
        
        # Validate request_id format
        if not request_id or not isinstance(request_id, str):
            logger.error(f"📋 ❌ Invalid request_id provided: {request_id}")
            return json.dumps({
                "status": "error",
                "message": "Invalid request_id provided",
                "request_id": request_id
            })
        
        # Clean the request_id
        request_id = request_id.strip()
        
        # Validate request_id exists in database
        user_id = "system"  # default
        try:
            with db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT user_id FROM adjustment_requests WHERE id = ?', (request_id,))
                row = cursor.fetchone()
                if row:
                    user_id = row['user_id']
                    logger.info(f"📋 ✅ Found request in database - User: {user_id}")
                else:
                    logger.error(f"📋 ❌ Request {request_id} not found in database")
                    return json.dumps({
                        "status": "error",
                        "message": f"Request {request_id} not found in database",
                        "request_id": request_id
                    })
        except Exception as e:
            logger.error(f"📋 ❌ Database error: {e}")
            return json.dumps({
                "status": "error",
                "message": f"Database error: {str(e)}",
                "request_id": request_id
            })
        
        logger.info(f"📋 Processing request ID: {request_id}")
        logger.info(f"📋 User ID: {user_id}")
        
        # Get unprocessed agreements
        unprocessed_agreements = db.get_unprocessed_agreements(request_id)
        logger.info(f"📋 Found {len(unprocessed_agreements)} unprocessed agreements")
        
        # Broadcast status for UI - always send an update
        try:
            if len(unprocessed_agreements) == 0:
                db.log_agent_activity(request_id, "agreement_filter", "NO_UNPROCESSED_AGREEMENTS", {
                    "message": "All agreements have already been processed",
                    "unprocessed_count": 0
                })
            else:
                # Send update when agreements need processing
                db.log_agent_activity(request_id, "agreement_filter", "AGREEMENTS_FILTERED", {
                    "message": f"Found {len(unprocessed_agreements)} agreements needing price adjustment",
                    "unprocessed_count": len(unprocessed_agreements),
                    "agreement_ids": [a.get('id') for a in unprocessed_agreements]
                })
        except:
            pass
        
        # Format agreements for processing
        formatted_agreements = []
        for agreement in unprocessed_agreements:
            # Extract key information for price adjustment
            provisions = agreement.get('provisions', {})
            
            formatted_agreement = {
                "agreement_id": agreement.get('id'),
                "agreement_name": agreement.get('name', 'Unknown Agreement'),
                "effective_date": provisions.get('effective_date'),
                "expiration_date": provisions.get('expiration_date'),
                "contract_value": provisions.get('total_agreement_value', 0),
                "currency": provisions.get('total_agreement_value_currency_code', 'USD'),
                "request_id": request_id,
                "user_id": user_id,
                "db_agreement_id": agreement.get('_db_agreement_id')  # For database reference
            }
            
            formatted_agreements.append(formatted_agreement)
            
            logger.info(f"📋 Agreement: {formatted_agreement['agreement_id']}")
            logger.info(f"📅   Effective: {formatted_agreement['effective_date']}")
            logger.info(f"📅   Expires: {formatted_agreement['expiration_date']}")
            logger.info(f"💰   Value: ${formatted_agreement['contract_value']:,.2f} {formatted_agreement['currency']}")
        
        # Format the response
        result = {
            "status": "UNPROCESSED_AGREEMENTS_RETRIEVED",
            "message": f"Retrieved {len(unprocessed_agreements)} unprocessed agreements",
            "request_id": request_id,
            "user_id": user_id,
            "unprocessed_agreements": formatted_agreements,
            "total_count": len(formatted_agreements),
            "timestamp": datetime.utcnow().isoformat(),
            "agent": "docusign_navigator"
        }
        
        logger.info(f"📋 ✅ Retrieved {len(formatted_agreements)} unprocessed agreements")
        logger.info(f"📋 📊 STATUS: UNPROCESSED_AGREEMENTS_RETRIEVED")
        
        return json.dumps(result)
        
    except Exception as e:
        logger.error(f"📋 ❌ GET UNPROCESSED AGREEMENTS ERROR: {str(e)}")
        error_result = {
            "status": "GET_UNPROCESSED_ERROR",
            "request_id": request_id if 'request_id' in locals() else f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "user_id": user_id if 'user_id' in locals() else 'system',
            "unprocessed_agreements": [],
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
            "agent": "docusign_navigator"
        }
        return json.dumps(error_result)


if __name__ == "__main__":
    """
    Test the DocuSign Navigator when run directly.
    """
    import logging
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("🔍 DOCUSIGN NAVIGATOR TEST")
    print("=" * 60)
    
    try:
        # Create navigator
        navigator = DocuSignNavigatorAgent()
        
        # Test with a sample request ID
        test_request_id = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        print(f"\n🧪 Testing with request ID: {test_request_id}")
        
        # Run the navigation
        result = navigator.navigate_docusign_documents(test_request_id)
        
        print("\n📊 RESULTS:")
        print(json.dumps(result, indent=2))
        
        if result.get('status') == 'success':
            print("\n✅ DocuSign Navigator test PASSED!")
        else:
            print("\n❌ DocuSign Navigator test FAILED!")
            
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        
    print("=" * 60)
