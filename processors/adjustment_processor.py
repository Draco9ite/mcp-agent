import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Dict, Any

from adjustment_agents import get_foundry_client, get_agent_ids
from adjustment_agents.docusign_navigator import navigate_docusign_documents, get_unprocessed_agreements
from adjustment_agents.bls_agent import fetch_cpi_data
from adjustment_agents.maestro_agent import trigger_maestro_workflow

logger = logging.getLogger(__name__)

# Map tool names to their local execution functions
TOOL_DISPATCH = {
    "navigate_docusign_documents": navigate_docusign_documents,
    "get_unprocessed_agreements": get_unprocessed_agreements,
    "fetch_cpi_data": fetch_cpi_data,
    "trigger_maestro_workflow": trigger_maestro_workflow,
}


class PriceAdjustmentProcessor:
    """Processes price adjustment requests through four Foundry Assistants"""
    
    def __init__(self, database):
        self.db = database
    
    async def _execute_tool(self, tool_name: str, arguments: str) -> str:
        """Execute a tool function locally and return the result string."""
        func = TOOL_DISPATCH.get(tool_name)
        if not func:
            return json.dumps({"status": "error", "message": f"Unknown tool: {tool_name}"})
        
        try:
            args = json.loads(arguments)
        except json.JSONDecodeError:
            args = {"request_id": arguments}
        
        if tool_name in ("navigate_docusign_documents", "get_unprocessed_agreements"):
            result = func(args.get("request_id", ""))
        else:
            input_data = args.get("input_data", arguments)
            if asyncio.iscoroutinefunction(func):
                result = await func(input_data)
            else:
                result = func(input_data)
        
        if asyncio.iscoroutine(result):
            result = await result
        return result if isinstance(result, str) else json.dumps(result)

    async def _run_agent(self, client, agent_id, agent_name, message_content):
        """Run a single Assistant to completion, handling tool calls."""
        logger.info(f"🤖 Running {agent_name} agent...")
        
        thread = await client.beta.threads.create()
        await client.beta.threads.messages.create(
            thread_id=thread.id, role="user", content=message_content
        )
        run = await client.beta.threads.runs.create(
            thread_id=thread.id, assistant_id=agent_id
        )
        
        while run.status in ("queued", "in_progress", "requires_action"):
            if run.status == "requires_action":
                tool_calls = run.required_action.submit_tool_outputs.tool_calls
                logger.info(f"🔧 {agent_name}: executing {len(tool_calls)} tool(s)")
                
                tool_outputs = []
                for tc in tool_calls:
                    logger.info(f"🔧 {agent_name}: calling {tc.function.name}")
                    output = await self._execute_tool(tc.function.name, tc.function.arguments)
                    tool_outputs.append({"tool_call_id": tc.id, "output": output})
                
                run = await client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread.id, run_id=run.id, tool_outputs=tool_outputs
                )
            else:
                await asyncio.sleep(1)
                run = await client.beta.threads.runs.retrieve(
                    thread_id=thread.id, run_id=run.id
                )
        
        # Extract final message
        final_text = ""
        if run.status == "completed":
            messages = await client.beta.threads.messages.list(thread_id=thread.id)
            for msg in messages.data:
                if msg.role == "assistant":
                    for block in msg.content:
                        if hasattr(block, 'text'):
                            final_text = block.text.value
                    break
            logger.info(f"✅ {agent_name} completed")
        else:
            error_msg = run.last_error.message if run.last_error else run.status
            logger.error(f"❌ {agent_name} failed: {error_msg}")
            final_text = json.dumps({"status": "error", "message": error_msg})
        
        # Clean up thread
        try:
            await client.beta.threads.delete(thread.id)
        except Exception:
            pass
        
        return final_text
    
    def _broadcast(self, request_id, agent, status, data=None):
        """Broadcast a status update to the dashboard via WebSocket."""
        if self.db.broadcast_callback:
            self.db.broadcast_callback(request_id, agent, status, data or {})

    async def process_price_adjustment_request(self, request_id: str):
        """Process a request through the multi-agent pipeline"""
        try:
            request_data = self.db.get_adjustment_request(request_id)
            if not request_data:
                logger.error(f"❌ Request not found: {request_id}")
                return
          
            trigger_data = request_data["request_data"]
            user_id = request_data.get('user_id', 'system')
            logger.info(f"🔄 Starting multi-agent price adjustment for {request_id}")
            
            self.db.update_adjustment_request_status(request_id, "processing")
            start_time = datetime.utcnow()
            
            try:
                client = get_foundry_client()
                agent_ids = await get_agent_ids()
            except Exception as e:
                logger.error(f"❌ Failed to initialize AI agents: {e}")
                self._broadcast(request_id, "workflow", "WORKFLOW_FAILED", {"error": str(e)})
                self.db.update_adjustment_request_status(request_id, "failed", {"error": str(e)})
                return
            
            try:
                # ── PHASE 1: Navigator Agent ────────────────────────────
                self._broadcast(request_id, "docusign_navigator", "DOCUSIGN_NAVIGATOR_STARTED")
                
                navigator_context = (
                    f"Find expiring agreements and get unprocessed ones.\n"
                    f"Request ID: {request_id}\n"
                    f"User ID: {user_id}\n"
                    f"First call navigate_docusign_documents, then call get_unprocessed_agreements."
                )
                navigator_result = await self._run_agent(
                    client, agent_ids["navigator"], "Navigator", navigator_context
                )
                
                # Parse navigator result for unprocessed agreements
                unprocessed = []
                try:
                    # The navigator may return multiple tool results; find the unprocessed agreements
                    json_match = re.search(r'\{.*"unprocessed_agreements".*\}', navigator_result, re.DOTALL)
                    if json_match:
                        nav_data = json.loads(json_match.group())
                        unprocessed = nav_data.get("unprocessed_agreements", [])
                except Exception:
                    pass
                
                # Broadcast navigator completion
                self._broadcast(request_id, "docusign_navigator", "DOCUSIGN_COMPLETE", {
                    "expiring_agreements_found": len(unprocessed),
                    "agreements_stored": len(unprocessed),
                })
                
                if not unprocessed:
                    # No agreements to process — report success
                    logger.info("✅ No unprocessed agreements found")
                    self._broadcast(request_id, "agreement_filter", "NO_UNPROCESSED_AGREEMENTS")
                    processing_time = (datetime.utcnow() - start_time).total_seconds()
                    
                    # Ask orchestrator for final summary
                    orchestrator_context = (
                        f"Request {request_id}: Navigator found no unprocessed agreements. "
                        f"All agreements have already been processed. Provide final status."
                    )
                    final_response = await self._run_agent(
                        client, agent_ids["orchestrator"], "Orchestrator", orchestrator_context
                    )
                    
                    final_results = self._parse_final_response(final_response, processing_time, request_id)
                    self.db.update_adjustment_request_status(request_id, final_results["status"], final_results)
                    self._broadcast_final(request_id, final_results)
                    return
                
                logger.info(f"📋 Found {len(unprocessed)} unprocessed agreements")
                self._broadcast(request_id, "agreement_filter", "AGREEMENTS_FILTERED", {
                    "unprocessed_count": len(unprocessed),
                    "agreement_ids": [a.get("agreement_id", "") for a in unprocessed],
                })
                
                # ── PHASE 2: BLS Agent (per agreement) ──────────────────
                self._broadcast(request_id, "bls_agent", "BLS_STARTED")
                bls_results = []
                for agreement in unprocessed:
                    bls_input = json.dumps({
                        "request_id": request_id,
                        "user_id": user_id,
                        "agreement_id": agreement.get("agreement_id"),
                        "effective_date": agreement.get("effective_date"),
                        "expiration_date": agreement.get("expiration_date"),
                        "contract_value": agreement.get("contract_value", 0),
                        "currency": agreement.get("currency", "USD")
                    })
                    
                    bls_context = f"Calculate CPI adjustment for this agreement:\n{bls_input}"
                    bls_result = await self._run_agent(
                        client, agent_ids["bls"], "BLS", bls_context
                    )
                    bls_results.append({"agreement": agreement, "bls_output": bls_result})
                    
                    # Broadcast BLS completion per agreement
                    try:
                        bls_parsed = re.search(r'\{.*"status".*\}', bls_result, re.DOTALL)
                        if bls_parsed:
                            bls_d = json.loads(bls_parsed.group())
                            calcs = bls_d.get("calculations", {})
                            self._broadcast(request_id, "bls_agent", "BLS_COMPLETE", {
                                "agreement_id": agreement.get("agreement_id", "unknown"),
                                "original_value": calcs.get("original_contract_value", 0),
                                "adjusted_value": calcs.get("adjusted_contract_value", 0),
                                "inflation_rate": calcs.get("cumulative_inflation_rate_percent", 0),
                            })
                    except Exception:
                        self._broadcast(request_id, "bls_agent", "BLS_COMPLETE", {
                            "agreement_id": agreement.get("agreement_id", "unknown"),
                        })
                
                # ── PHASE 3: Maestro Agent (per agreement) ──────────────
                self._broadcast(request_id, "maestro_agent", "MAESTRO_STARTED")
                maestro_results = []
                for br in bls_results:
                    # Extract key fields from BLS output for Maestro
                    try:
                        bls_json = re.search(r'\{.*"status".*\}', br["bls_output"], re.DOTALL)
                        if bls_json:
                            bls_data = json.loads(bls_json.group())
                            if bls_data.get("status") == "BLS_ERROR":
                                maestro_results.append({"status": "skipped", "reason": "BLS failed"})
                                continue
                            
                            maestro_input = json.dumps({
                                "request_id": request_id,
                                "user_id": user_id,
                                "agreement_id": bls_data.get("agreement_details", {}).get("agreement_id", br["agreement"].get("agreement_id")),
                                "original_value": bls_data.get("calculations", {}).get("effective_cpi", 0),
                                "adjusted_value": bls_data.get("calculations", {}).get("adjusted_contract_value", 0),
                                "inflation_rate": bls_data.get("calculations", {}).get("cumulative_inflation_rate_percent", 0),
                                "value_increase": bls_data.get("calculations", {}).get("value_increase", 0)
                            })
                        else:
                            maestro_input = br["bls_output"]
                    except Exception:
                        maestro_input = br["bls_output"]
                    
                    maestro_context = f"Trigger Maestro workflow with this data:\n{maestro_input}"
                    maestro_result = await self._run_agent(
                        client, agent_ids["maestro"], "Maestro", maestro_context
                    )
                    maestro_results.append({"output": maestro_result})
                    
                    # Broadcast Maestro trigger per agreement
                    self._broadcast(request_id, "maestro_agent", "MAESTRO_TRIGGERED", {
                        "agreement_id": br["agreement"].get("agreement_id", "unknown"),
                    })
                
                # ── PHASE 4: Orchestrator compiles final status ─────────
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                
                summary_context = (
                    f"Request {request_id} processing complete.\n"
                    f"Navigator found {len(unprocessed)} unprocessed agreements.\n"
                    f"BLS processed {len(bls_results)} agreements.\n"
                    f"Maestro triggered {len([m for m in maestro_results if m.get('status') != 'skipped'])} workflows.\n"
                    f"Provide the final JSON status summary."
                )
                final_response = await self._run_agent(
                    client, agent_ids["orchestrator"], "Orchestrator", summary_context
                )
                
                final_results = self._parse_final_response(final_response, processing_time, request_id)
                self.db.update_adjustment_request_status(request_id, final_results["status"], final_results)
                self._broadcast_final(request_id, final_results)
                
            except Exception as e:
                error_str = str(e).lower()
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                
                if 'content_filter' in error_str or 'responsibleaipolicyviolation' in error_str:
                    logger.warning(f"🚫 Content filter blocked: {e}")
                    self._broadcast(request_id, "workflow", "WORKFLOW_FAILED", {"error": str(e)})
                    self.db.update_adjustment_request_status(request_id, "blocked", {
                        "status": "blocked", "error": str(e), "processing_time_seconds": processing_time
                    })
                else:
                    logger.error(f"❌ Processing failed: {e}")
                    self._broadcast(request_id, "workflow", "WORKFLOW_FAILED", {"error": str(e)})
                    self.db.update_adjustment_request_status(request_id, "failed", {
                        "status": "failed", "error": str(e), "processing_time_seconds": processing_time
                    })
        
        except Exception as e:
            logger.error(f"❌ Fatal error processing request {request_id}: {e}")
            self._broadcast(request_id, "workflow", "WORKFLOW_FAILED", {"error": str(e)})
            self.db.update_adjustment_request_status(request_id, "fatal_error", {"error": str(e)})
    
    def _parse_final_response(self, response_text, processing_time, request_id):
        """Parse the orchestrator's final response into a results dict."""
        try:
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                response_data = json.loads(json_match.group())
            else:
                response_data = {"status": "completed", "summary": response_text}
        except json.JSONDecodeError:
            response_data = {"status": "completed", "summary": str(response_text)}
        
        return {
            "status": response_data.get("status", "completed"),
            "processing_time_seconds": processing_time,
            "final_response": response_data,
            "completed_at": datetime.utcnow().isoformat(),
            "agent_logs": self.db.get_agent_logs(request_id),
        }
    
    def _broadcast_final(self, request_id, final_results):
        """Broadcast the final workflow status to the dashboard."""
        if self.db.broadcast_callback:
            status = final_results.get("status", "completed")
            self.db.broadcast_callback(
                request_id, "workflow", f"WORKFLOW_{status.upper()}",
                final_results.get("final_response", {})
            )
