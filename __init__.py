"""
Microsoft Foundry Multi-Agent Setup - Azure OpenAI Assistants API

Four assistants created via the openai SDK (AsyncAzureOpenAI).
Each assistant has its own instructions and tools.
The App Service coordinates the four assistants sequentially.
"""

import asyncio
import os
import json
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# ── Azure OpenAI setup (lazy) ──────────────────────────────────────────
foundry_endpoint = os.getenv("MICROSOFT_FOUNDRY_ENDPOINT", "")
foundry_model = os.getenv("MICROSOFT_FOUNDRY_MODEL", "gpt-4o")
foundry_api_version = os.getenv("MICROSOFT_FOUNDRY_API_VERSION", "2025-04-01-preview")

if foundry_endpoint:
    foundry_endpoint = foundry_endpoint.rstrip('/')

# Lazy-init: don't create the credential/client at import time so the
# Flask app can start even when Azure credentials aren't available yet.
_credential = None
_token_provider = None
_foundry_client = None

def _get_credential():
    global _credential
    if _credential is None:
        from azure.identity import DefaultAzureCredential
        _credential = DefaultAzureCredential()
    return _credential

def _get_token_provider():
    global _token_provider
    if _token_provider is None:
        from azure.identity import get_bearer_token_provider
        _token_provider = get_bearer_token_provider(
            _get_credential(), "https://cognitiveservices.azure.com/.default"
        )
    return _token_provider

def _get_foundry_client():
    """Return the AsyncAzureOpenAI client for the Assistants API."""
    global _foundry_client
    if _foundry_client is None:
        from openai import AsyncAzureOpenAI
        _foundry_client = AsyncAzureOpenAI(
            azure_endpoint=foundry_endpoint,
            azure_ad_token_provider=_get_token_provider(),
            api_version=foundry_api_version,
        )
        logger.info(f"🔧 Azure OpenAI Assistants API configured: endpoint={foundry_endpoint}, model={foundry_model}")
    return _foundry_client


# ── Tool schemas per agent ──────────────────────────────────────────────

NAVIGATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "navigate_docusign_documents",
            "description": "Find DocuSign agreements expiring in the next 30 days and store them in database",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "description": "The request ID for tracking"}
                },
                "required": ["request_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_unprocessed_agreements",
            "description": "Get agreements from database that haven't been processed yet for price adjustment",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "description": "The request ID to filter agreements for"}
                },
                "required": ["request_id"]
            }
        }
    }
]

BLS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_cpi_data",
            "description": "Fetch Consumer Price Index data for specific agreement price adjustment calculation",
            "parameters": {
                "type": "object",
                "properties": {
                    "input_data": {
                        "type": "string",
                        "description": "JSON string with agreement details: request_id, user_id, agreement_id, effective_date, expiration_date, contract_value, currency"
                    }
                },
                "required": ["input_data"]
            }
        }
    }
]

MAESTRO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "trigger_maestro_workflow",
            "description": "Trigger DocuSign Maestro workflow for agreement processing with updated contract values",
            "parameters": {
                "type": "object",
                "properties": {
                    "input_data": {
                        "type": "string",
                        "description": "JSON string with BLS results: request_id, user_id, agreement_id, original_value, adjusted_value, inflation_rate, etc."
                    }
                },
                "required": ["input_data"]
            }
        }
    }
]


# ── Agent instructions ──────────────────────────────────────────────────

NAVIGATOR_INSTRUCTIONS = """You are the DocuSign Navigator Agent. Your job is to find contracts expiring soon.

When given a request_id:
1. Call navigate_docusign_documents with the request_id to find agreements expiring in the next 30 days
2. Call get_unprocessed_agreements with the same request_id to get agreements needing price adjustment

Return the exact JSON response from each tool. Log your status as 'DOCUSIGN_COMPLETE' when finished."""

BLS_INSTRUCTIONS = """You are the BLS (Bureau of Labor Statistics) Agent. Your job is to calculate inflation-based price adjustments.

You will receive a JSON string with agreement details. Call fetch_cpi_data with that data to retrieve CPI data and calculate the adjusted contract value.

Return the exact JSON response. Log your status as 'BLS_COMPLETE' when finished."""

MAESTRO_INSTRUCTIONS = """You are the Maestro Agent responsible for triggering DocuSign Maestro workflows.

You will receive a JSON string with price adjustment results. Call trigger_maestro_workflow with that data to initiate the approval workflow.

Return the exact JSON response with instance_id. Log your status as 'MAESTRO_COMPLETE' when finished."""

ORCHESTRATOR_INSTRUCTIONS = """You are the Master Orchestrator Agent for the Price Adjustment workflow.

You coordinate three specialized agents. The App Service will present you with the output from each agent in sequence. Your job is to:

1. Review the Navigator Agent's output and determine which agreements need processing
2. For each unprocessed agreement, prepare the input JSON for the BLS Agent
3. Review the BLS Agent's output and prepare the input for the Maestro Agent
4. Review Maestro's output and compile the final status

RULES:
- If Navigator finds no expiring agreements, report SUCCESS with "nothing to process"
- If no unprocessed agreements remain, report SUCCESS with "all already processed"
- If BLS fails for one agreement, continue with others
- If Maestro fails for one agreement, continue with others

Return final JSON:
{
    "status": "SUCCESS/PARTIAL/FAILED",
    "summary": "brief explanation",
    "agreements_found": number,
    "agreements_processed_successfully": number,
    "agreements_failed": number,
    "maestro_workflows_triggered": number,
    "total_value_adjustments": "total dollar amount"
}"""


# ── Create assistants in Azure OpenAI ──────────────────────────────────

_agent_ids = {}

async def _create_agent(name, instructions, tools):
    """Create a single assistant in Azure OpenAI via the Assistants API."""
    try:
        client = _get_foundry_client()
        assistant = await client.beta.assistants.create(
            model=foundry_model,
            name=name,
            instructions=instructions,
            tools=tools,
        )
        logger.info(f"🤖 Created assistant '{name}': {assistant.id}")
        return assistant.id
    except Exception as e:
        logger.error(f"❌ Failed to create assistant '{name}': {e}")
        raise


async def get_agent_ids():
    """Create all four assistants if they don't exist yet. Returns dict of name→id."""
    global _agent_ids
    if _agent_ids:
        return _agent_ids

    _agent_ids["navigator"] = await _create_agent(
        "docusign_navigator_agent", NAVIGATOR_INSTRUCTIONS, NAVIGATOR_TOOLS
    )
    _agent_ids["bls"] = await _create_agent(
        "bls_agent", BLS_INSTRUCTIONS, BLS_TOOLS
    )
    _agent_ids["maestro"] = await _create_agent(
        "maestro_agent", MAESTRO_INSTRUCTIONS, MAESTRO_TOOLS
    )
    _agent_ids["orchestrator"] = await _create_agent(
        "master_orchestrator_agent", ORCHESTRATOR_INSTRUCTIONS, tools=[]
    )

    logger.info(f"🤖 All 4 assistants created: {list(_agent_ids.keys())}")
    return _agent_ids


def get_foundry_client():
    """Return the configured AsyncAzureOpenAI client."""
    return _get_foundry_client()
