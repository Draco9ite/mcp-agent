#!/usr/bin/env python3
"""
The DocuSign IAM + CLM agent.

Exposes the Agreement Manager facade as tools an LLM can call, in the same
Azure OpenAI Assistants shape the other agents in this repository use (see
``adjustment_agents/__init__.py``). The agent itself holds no model client: it
publishes :data:`IAM_CLM_TOOLS` and :data:`IAM_CLM_INSTRUCTIONS` for whichever
runtime drives the loop, and :meth:`DocuSignIAMAgent.dispatch` executes a tool
call by name.

Writes are gated. Every tool that changes agreement metadata takes a
``dry_run`` argument that defaults to true, and the instructions tell the model
to show a dry run and get confirmation before applying. Bulk metadata changes
are hard to reverse, so the model is not given a way to apply one implicitly.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from .agreement_manager import CLM_WORKFLOWS, AgreementManager
from .models import supported_contract_types

logger = logging.getLogger(__name__)


IAM_CLM_INSTRUCTIONS = """You are the DocuSign Agreement Manager Agent. You work across two systems:

- DocuSign IAM: the Agreement Manager repository (AI-extracted agreement data) and Maestro workflows.
- DocuSign CLM: the document repository where attribute groups and the Party Management workflows live.

How to work:
1. Call docusign_whoami first in a new session to confirm which surfaces are reachable. If CLM reports not ok, say so and stay on the IAM side.
2. For questions about agreement data, read from the Agreement Manager repository. For questions about stored metadata values, read the CLM document.
3. When the two could disagree, call reconcile_agreement rather than guessing which is right.
4. NEVER apply a metadata change without first running it with dry_run=true, showing the caller which attributes would change, and getting an explicit go-ahead. Blank values clear attributes, so a wrong CSV silently erases data.
5. Report counts and the specific attributes affected. Do not claim a change was applied unless the tool returned status "applied".

Return the tool's JSON response. Say plainly when something failed and what the error was."""


IAM_CLM_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "docusign_whoami",
            "description": (
                "Verify DocuSign credentials and report which surfaces are reachable "
                "(IAM Agreement Manager and CLM), the account ID and the environment. "
                "Call this first in a new session."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_agreements",
            "description": (
                "List agreements from the DocuSign IAM Agreement Manager repository, "
                "optionally filtered by contract type, status or expiration date."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contract_type": {
                        "type": "string",
                        "description": "Agreement type to filter on, e.g. NDA or ISDA",
                    },
                    "status": {"type": "string", "description": "Agreement status to filter on"},
                    "expiring_before": {
                        "type": "string",
                        "description": "Only agreements expiring on or before this date (YYYY-MM-DD)",
                    },
                    "search": {"type": "string", "description": "Free-text search term"},
                    "limit": {
                        "type": "integer",
                        "description": "Maximum agreements to return (max 100)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agreement",
            "description": (
                "Get one agreement from the IAM Agreement Manager repository, including "
                "its AI-extracted provisions and parties."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agreement_id": {"type": "string", "description": "Agreement Manager agreement ID"}
                },
                "required": ["agreement_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clm_agreement",
            "description": (
                "Get a CLM document and its attribute group values. Use this to see what "
                "metadata is actually stored in CLM."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "CLM document ID"}
                },
                "required": ["document_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reconcile_agreement",
            "description": (
                "Compare one agreement between the IAM Agreement Manager repository and "
                "CLM, and report which of contract type, status, effective date and "
                "expiration date disagree."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agreement_id": {"type": "string", "description": "Agreement Manager agreement ID"},
                    "clm_document_id": {
                        "type": "string",
                        "description": "CLM document to compare against, if not linked on the record",
                    },
                },
                "required": ["agreement_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reconcile_contract_type",
            "description": (
                "Reconcile every agreement of one contract type between IAM and CLM and "
                "return a summary of how many are in sync, out of sync or unlinked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contract_type": {
                        "type": "string",
                        "description": f"One of: {', '.join(supported_contract_types())}",
                    },
                    "limit": {"type": "integer", "description": "Maximum agreements to examine"},
                },
                "required": ["contract_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_metadata_update",
            "description": (
                "Read a metadata CSV and show exactly which CLM attributes each row would "
                "write, without changing anything. Always do this before applying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "csv_path": {"type": "string", "description": "Path to the metadata CSV"},
                    "contract_type": {
                        "type": "string",
                        "description": "Force a contract type for every row",
                    },
                    "max_rows": {"type": "integer", "description": "Stop after this many rows"},
                },
                "required": ["csv_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_metadata_update",
            "description": (
                "Apply a metadata CSV to CLM documents. This writes to production "
                "agreement metadata and blank cells clear attributes. Only call this after "
                "preview_metadata_update has been shown to the caller and they have "
                "explicitly approved. Set confirm=true to acknowledge that."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "csv_path": {"type": "string", "description": "Path to the metadata CSV"},
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true; the caller approved the previewed changes",
                    },
                    "contract_type": {
                        "type": "string",
                        "description": "Force a contract type for every row",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Stop after this many rows; use 1 for a validation run",
                    },
                },
                "required": ["csv_path", "confirm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_clm_workflows",
            "description": (
                "List the Party Management CLM workflow keys this integration can start, "
                "with the CLM workflow name each resolves to."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_clm_workflow",
            "description": (
                "Start a Party Management CLM workflow by its solution key. This begins "
                "real work in CLM, so confirm with the caller first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_key": {
                        "type": "string",
                        "description": f"One of: {', '.join(sorted(CLM_WORKFLOWS))}",
                    },
                    "params": {
                        "type": "object",
                        "description": "Values bound to the workflow's /Params root",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true; the caller approved starting this workflow",
                    },
                },
                "required": ["workflow_key", "confirm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_maestro_workflows",
            "description": "List Maestro (Workflow Builder) workflows published in the IAM account.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_maestro_workflow",
            "description": (
                "Trigger a Maestro workflow instance. This starts real work, so confirm "
                "with the caller first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "Maestro workflow ID"},
                    "instance_name": {"type": "string", "description": "Name for the new instance"},
                    "trigger_inputs": {
                        "type": "object",
                        "description": "Values matching the workflow's trigger schema",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true; the caller approved triggering this workflow",
                    },
                },
                "required": ["workflow_id", "instance_name", "confirm"],
            },
        },
    },
]


class DocuSignIAMAgent:
    """Executes the IAM + CLM tool calls against the Agreement Manager facade."""

    def __init__(self, manager: Optional[AgreementManager] = None):
        self._manager = manager

    @property
    def manager(self) -> AgreementManager:
        # Built on first use so constructing the agent never needs credentials.
        if self._manager is None:
            self._manager = AgreementManager()
        return self._manager

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return IAM_CLM_TOOLS

    @property
    def instructions(self) -> str:
        return IAM_CLM_INSTRUCTIONS

    def dispatch(self, tool_name: str, arguments: Any = None) -> Dict[str, Any]:
        """Run one tool call and return a JSON-serializable result.

        Args:
            tool_name: The function name from the tool schema.
            arguments: Parsed dict, or the JSON string an Assistants run gives.

        Returns:
            The tool's result, or ``{"error": ...}`` on failure. Errors are
            returned rather than raised so a model loop can see what went wrong
            and tell the caller instead of the whole run failing.
        """
        args = _parse_arguments(arguments)
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return {"error": f"Unknown tool {tool_name!r}", "available": _tool_names()}

        try:
            return handler(**args)
        except TypeError as exc:
            return {"error": f"Bad arguments for {tool_name}: {exc}"}
        except Exception as exc:
            logger.exception("Tool %s failed", tool_name)
            return {"error": f"{type(exc).__name__}: {exc}", "tool": tool_name}

    # ── Tool implementations ────────────────────────────────────────────

    def _tool_docusign_whoami(self) -> Dict[str, Any]:
        return self.manager.whoami()

    def _tool_list_agreements(
        self,
        contract_type: Optional[str] = None,
        status: Optional[str] = None,
        expiring_before: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 25,
    ) -> Dict[str, Any]:
        agreements = self.manager.list_agreements(
            contract_type=contract_type,
            status=status,
            expiring_before=expiring_before,
            search=search,
            limit=limit,
        )
        return {
            "count": len(agreements),
            "agreements": [a.to_dict() for a in agreements],
        }

    def _tool_get_agreement(self, agreement_id: str) -> Dict[str, Any]:
        return self.manager.get_agreement(agreement_id).to_dict()

    def _tool_get_clm_agreement(self, document_id: str) -> Dict[str, Any]:
        agreement = self.manager.get_clm_agreement(document_id)
        result = agreement.to_dict()
        result["attribute_groups"] = agreement.raw.get("AttributeGroups", {})
        return result

    def _tool_reconcile_agreement(
        self, agreement_id: str, clm_document_id: Optional[str] = None
    ) -> Dict[str, Any]:
        return self.manager.reconcile_agreement(agreement_id, clm_document_id)

    def _tool_reconcile_contract_type(
        self, contract_type: str, limit: int = 25
    ) -> Dict[str, Any]:
        return self.manager.reconcile_contract_type(contract_type, limit=limit)

    def _tool_preview_metadata_update(
        self,
        csv_path: str,
        contract_type: Optional[str] = None,
        max_rows: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self.manager.run_bulk_metadata_update(
            csv_path, dry_run=True, contract_type=contract_type, max_rows=max_rows
        )

    def _tool_apply_metadata_update(
        self,
        csv_path: str,
        confirm: bool = False,
        contract_type: Optional[str] = None,
        max_rows: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not confirm:
            return {
                "error": (
                    "Refusing to apply without confirm=true. Run "
                    "preview_metadata_update, show the caller which attributes would "
                    "change, and apply only once they approve."
                )
            }
        return self.manager.run_bulk_metadata_update(
            csv_path, dry_run=False, contract_type=contract_type, max_rows=max_rows
        )

    def _tool_list_clm_workflows(self) -> Dict[str, Any]:
        return {
            "workflows": [
                {"key": key, "clm_workflow_name": self.manager.clm_workflow_name(key)}
                for key in sorted(CLM_WORKFLOWS)
            ]
        }

    def _tool_start_clm_workflow(
        self,
        workflow_key: str,
        confirm: bool = False,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not confirm:
            return {"error": "Refusing to start a CLM workflow without confirm=true."}
        return self.manager.start_clm_workflow(workflow_key, params)

    def _tool_list_maestro_workflows(self) -> Dict[str, Any]:
        return self.manager.list_maestro_workflows()

    def _tool_trigger_maestro_workflow(
        self,
        workflow_id: str,
        instance_name: str,
        confirm: bool = False,
        trigger_inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not confirm:
            return {"error": "Refusing to trigger a Maestro workflow without confirm=true."}
        return self.manager.trigger_maestro_workflow(
            workflow_id, instance_name, trigger_inputs or {}
        )


def _parse_arguments(arguments: Any) -> Dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        if not arguments.strip():
            return {}
        return json.loads(arguments)
    if isinstance(arguments, dict):
        return arguments
    raise TypeError(f"Unsupported arguments type: {type(arguments).__name__}")


def _tool_names() -> List[str]:
    return [tool["function"]["name"] for tool in IAM_CLM_TOOLS]
