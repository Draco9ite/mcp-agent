"""
Master Orchestrator — now runs as an Agent in Microsoft Foundry.
This module is kept for backwards compatibility but the agents
are created in __init__.py via the Azure AI Agent Service.
"""

from . import get_agent_ids, get_foundry_client
