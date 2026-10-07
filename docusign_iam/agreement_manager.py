#!/usr/bin/env python3
"""
The Agreement Manager facade: one API over DocuSign IAM and CLM.

What this adds on top of the two clients:

* **One agreement view.** Read an agreement from the Agreement Manager
  repository, find its CLM document, and return both plus their differences.
* **Reconciliation.** Report where the IAM repository and CLM disagree on
  contract type, status, effective date or expiration date, across a whole
  contract type if asked.
* **Bulk metadata runs.** Apply a Solution 2 style metadata CSV directly
  through the CLM API, with the same column-to-attribute mapping the CLM
  workflow uses, in dry-run or applied mode.
* **Workflow launching.** Start the Party Management CLM workflows
  (Solution 1 through 5) or a Maestro workflow, from the same object.

Every write path defaults to ``dry_run=True``: these operations change
production agreement metadata in bulk, so applying has to be asked for.
"""

import csv
import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from .auth import DocuSignAuth
from .clm_client import CLMClient
from .iam_client import MAX_PAGE_SIZE, DocuSignAPIError, IAMClient
from .models import (
    Agreement,
    build_attribute_groups,
    diff_agreements,
    supported_contract_types,
)

logger = logging.getLogger(__name__)

#: CLM workflow names for the Party Management solutions kept in
#: draco9ite/smarter-docusign. Override any of them with the matching
#: CLM_WORKFLOW_<KEY> environment variable when an account renamed one.
CLM_WORKFLOWS = {
    "party_management_migration": "Party Management - Document Migration Workflow",
    "bulk_metadata_update": "Bulk Metadata Update Workflow",
    "bulk_metadata_pass1_clear_nda_expiration": (
        "Bulk Metadata Update Workflow - Pass 1 Clear NDA Expiration Date"
    ),
    "mfc_amendment_ai_review": "MFC - Amendment",
    "merge_doc_combine_folder_pdfs": "Merge Doc - Combine Folder PDFs",
    "ai_agent_reference": "Agentic Fun - LZ Test",
}

#: Docusign AI agents invoked by a CLM `Use AI Agent` step (AIAgentActivity).
#: The `system_` prefix marks a Docusign-provided agent. See
#: smarter-docusign/docs/CLM_AI_Steps_Reference.md for the step's contract.
CLM_AI_AGENTS = {
    "counterparty_brief": "system_counterparty_brief_agent",
}

#: The column every metadata CSV uses to identify the target agreement.
DOCUMENT_ID_COLUMN = "Document_ID"
CONTRACT_TYPE_COLUMN = "Contract_Type"

#: Placeholder the shipped CSV templates carry in their example row.
_TEMPLATE_PLACEHOLDER = "<agreement id>"


class AgreementManager:
    """Unified read/write surface over DocuSign IAM and CLM."""

    def __init__(
        self,
        auth: Optional[DocuSignAuth] = None,
        iam: Optional[IAMClient] = None,
        clm: Optional[CLMClient] = None,
    ):
        self.auth = auth or DocuSignAuth()
        self.iam = iam or IAMClient(auth=self.auth)
        self.clm = clm or CLMClient(auth=self.auth)

    # ── Connectivity ────────────────────────────────────────────────────

    def whoami(self) -> Dict[str, Any]:
        """Verify credentials and report what this integration can reach.

        Each surface is probed independently so a partial setup — IAM working,
        CLM not entitled, say — is reported field by field instead of failing
        the whole call.
        """
        report: Dict[str, Any] = {
            "environment": "demo" if self.auth.env.demo else "production",
            "grant": "jwt" if self.auth.use_jwt else "authorization_code",
            "scopes": self.auth.scopes,
            "iam": {"ok": False},
            "clm": {"ok": False},
        }

        try:
            info = self.auth.userinfo()
            report["user"] = {
                "name": info.get("name"),
                "email": info.get("email"),
                "user_id": info.get("sub"),
            }
            report["account_id"] = self.auth.account_id
            report["esign_base_uri"] = self.auth.esign_base_uri
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            return report

        try:
            page = self.iam.list_agreements(limit=1)
            rows = page.get("data") or page.get("agreements") or []
            report["iam"] = {"ok": True, "agreements_readable": True, "sample_count": len(rows)}
        except Exception as exc:
            report["iam"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        try:
            account = self.clm.account
            report["clm"] = {
                "ok": True,
                "clm_account_id": account["clm_account_id"],
                "api_base_url": account["api_base_url"],
                "discovered_via": account.get("source"),
            }
        except Exception as exc:
            report["clm"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        return report

    # ── Reads ───────────────────────────────────────────────────────────

    def list_agreements(
        self,
        contract_type: Optional[str] = None,
        status: Optional[str] = None,
        expiring_before: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = MAX_PAGE_SIZE,
    ) -> List[Agreement]:
        """Agreements from the IAM repository, as normalized records."""
        clauses = []
        if contract_type:
            clauses.append(f"type eq '{contract_type}'")
        if status:
            clauses.append(f"status eq '{status}'")
        if expiring_before:
            clauses.append(f"provisions/expiration_date le {expiring_before}")

        page = self.iam.list_agreements(
            odata_filter=" and ".join(clauses) if clauses else None,
            search=search,
            limit=limit,
        )
        rows = page.get("data") or page.get("agreements") or []
        return [Agreement.from_agreement_manager(row) for row in rows]

    def get_agreement(self, agreement_id: str) -> Agreement:
        return Agreement.from_agreement_manager(self.iam.get_agreement(agreement_id))

    def get_clm_agreement(self, document_id: str) -> Agreement:
        return Agreement.from_clm_document(
            self.clm.get_document(document_id, expand=["AttributeGroups"])
        )

    # ── Reconciliation ──────────────────────────────────────────────────

    def reconcile_agreement(
        self, agreement_id: str, clm_document_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Compare one agreement across the IAM repository and CLM.

        Args:
            agreement_id: Agreement Manager agreement ID.
            clm_document_id: CLM document to compare against. Defaults to the
                ``source_id`` on the agreement record, which is set when the
                agreement was ingested from CLM.
        """
        iam_agreement = self.get_agreement(agreement_id)
        document_id = clm_document_id or iam_agreement.clm_document_id

        if not document_id:
            return {
                "agreement_id": agreement_id,
                "linked": False,
                "reason": (
                    "No CLM document is linked to this agreement. Pass "
                    "clm_document_id explicitly to compare against one."
                ),
                "agreement_manager": iam_agreement.to_dict(),
            }

        try:
            clm_agreement = self.get_clm_agreement(document_id)
        except DocuSignAPIError as exc:
            return {
                "agreement_id": agreement_id,
                "clm_document_id": document_id,
                "linked": False,
                "reason": f"CLM document could not be read: {exc}",
                "agreement_manager": iam_agreement.to_dict(),
            }

        differences = diff_agreements(iam_agreement, clm_agreement)
        return {
            "agreement_id": agreement_id,
            "clm_document_id": document_id,
            "linked": True,
            "in_sync": not differences,
            "differences": [d.to_dict() for d in differences],
            "agreement_manager": iam_agreement.to_dict(),
            "clm": clm_agreement.to_dict(),
        }

    def reconcile_contract_type(
        self, contract_type: str, limit: int = MAX_PAGE_SIZE
    ) -> Dict[str, Any]:
        """Reconcile every agreement of one contract type.

        Returns a summary plus the per-agreement results, so the output can be
        handed straight to a reviewer deciding which rows need a metadata run.
        """
        results = []
        for agreement in self.list_agreements(contract_type=contract_type, limit=limit):
            results.append(self.reconcile_agreement(agreement.id))

        linked = [r for r in results if r.get("linked")]
        return {
            "contract_type": contract_type,
            "examined": len(results),
            "linked": len(linked),
            "unlinked": len(results) - len(linked),
            "in_sync": sum(1 for r in linked if r.get("in_sync")),
            "out_of_sync": sum(1 for r in linked if not r.get("in_sync")),
            "results": results,
        }

    # ── Bulk metadata ───────────────────────────────────────────────────

    def apply_metadata_row(
        self,
        row: Dict[str, Any],
        dry_run: bool = True,
        contract_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply one metadata CSV row to its CLM document.

        Blank cells clear the attribute, matching the CLM workflow's behaviour,
        so a column must be left out of the CSV entirely to preserve its value.

        Args:
            row: Column name to value, including ``Document_ID``.
            dry_run: When true, build and return the PATCH body without sending it.
            contract_type: Overrides the row's ``Contract_Type`` cell.
        """
        document_id = (row.get(DOCUMENT_ID_COLUMN) or "").strip()
        row_type = (contract_type or row.get(CONTRACT_TYPE_COLUMN) or "").strip()

        if not document_id or document_id.lower() == _TEMPLATE_PLACEHOLDER:
            return {
                "status": "skipped",
                "reason": f"{DOCUMENT_ID_COLUMN} is empty or still the template placeholder",
                "document_id": document_id,
            }
        if not row_type:
            return {
                "status": "skipped",
                "reason": f"{CONTRACT_TYPE_COLUMN} is required to resolve attribute targets",
                "document_id": document_id,
            }
        if row_type not in supported_contract_types():
            return {
                "status": "error",
                "reason": (
                    f"Unknown contract type {row_type!r}; expected one of "
                    f"{', '.join(supported_contract_types())}"
                ),
                "document_id": document_id,
            }

        values = {
            column: value
            for column, value in row.items()
            if column != DOCUMENT_ID_COLUMN
        }
        attribute_groups, unmapped = build_attribute_groups(row_type, values)

        if not attribute_groups:
            return {
                "status": "skipped",
                "reason": "No mapped columns carried a value",
                "document_id": document_id,
                "contract_type": row_type,
                "unmapped_columns": unmapped,
            }

        result = {
            "status": "dry_run" if dry_run else "applied",
            "document_id": document_id,
            "contract_type": row_type,
            "attribute_groups": attribute_groups,
            "attributes_written": sum(len(a) for a in attribute_groups.values()),
            "unmapped_columns": unmapped,
        }

        if dry_run:
            return result

        try:
            self.clm.update_document_attributes(document_id, attribute_groups)
        except DocuSignAPIError as exc:
            result["status"] = "error"
            result["reason"] = str(exc)
        return result

    def run_bulk_metadata_update(
        self,
        csv_path: str,
        dry_run: bool = True,
        contract_type: Optional[str] = None,
        max_rows: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Apply a Solution 2 metadata CSV through the CLM API.

        This is the API-side equivalent of running the Bulk Metadata Update
        workflow: the same columns, the same attribute targets, no Workflow
        Designer import needed. Use it to validate a CSV, or to apply a small
        batch; the CLM workflow remains the right tool for a full ~900-row run
        because of its built-in pausing.

        Args:
            csv_path: Path to the metadata CSV.
            dry_run: Build the payloads without writing. Defaults to true.
            contract_type: Force a contract type for every row.
            max_rows: Stop after this many data rows. Useful for the one-row
                validation run each solution's runbook asks for first.
        """
        if not os.path.exists(csv_path):
            raise FileNotFoundError(csv_path)

        results: List[Dict[str, Any]] = []
        with open(csv_path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames and DOCUMENT_ID_COLUMN not in reader.fieldnames:
                raise ValueError(
                    f"{csv_path} has no {DOCUMENT_ID_COLUMN} column; got "
                    f"{reader.fieldnames}"
                )
            for index, row in enumerate(reader):
                if max_rows is not None and index >= max_rows:
                    break
                outcome = self.apply_metadata_row(
                    row, dry_run=dry_run, contract_type=contract_type
                )
                outcome["row_number"] = index + 2  # +2: header row is line 1
                results.append(outcome)

        return {
            "csv_path": csv_path,
            "dry_run": dry_run,
            "rows_read": len(results),
            "applied": sum(1 for r in results if r["status"] == "applied"),
            "planned": sum(1 for r in results if r["status"] == "dry_run"),
            "skipped": sum(1 for r in results if r["status"] == "skipped"),
            "errors": sum(1 for r in results if r["status"] == "error"),
            "unmapped_columns": sorted(
                {c for r in results for c in r.get("unmapped_columns", [])}
            ),
            "results": results,
        }

    def clear_nda_expiration_dates(
        self, document_ids: Iterable[str], dry_run: bool = True
    ) -> Dict[str, Any]:
        """Blank the NDA Expiration Date on each document (Solution 2 Pass 1).

        Migrated NDAs carry the effective date in the Expiration Date
        attribute, which makes CLM reject the real Effective Date. Clearing it
        first is what Pass 1 exists to do.
        """
        results = []
        for document_id in document_ids:
            entry = {"document_id": document_id, "status": "dry_run" if dry_run else "applied"}
            if not dry_run:
                try:
                    self.clm.clear_document_attribute(
                        document_id, "CLM Agreement Details", "Expiration Date"
                    )
                except DocuSignAPIError as exc:
                    entry["status"] = "error"
                    entry["reason"] = str(exc)
            results.append(entry)

        return {
            "dry_run": dry_run,
            "documents": len(results),
            "errors": sum(1 for r in results if r["status"] == "error"),
            "results": results,
        }

    # ── Workflows ───────────────────────────────────────────────────────

    def clm_workflow_name(self, key: str) -> str:
        """Resolve a solution key to its CLM workflow name.

        ``CLM_WORKFLOW_<KEY>`` in the environment overrides the default, for
        accounts that renamed a workflow on import.
        """
        if key not in CLM_WORKFLOWS:
            raise ValueError(
                f"Unknown CLM workflow key {key!r}; expected one of "
                f"{', '.join(sorted(CLM_WORKFLOWS))}"
            )
        return os.getenv(f"CLM_WORKFLOW_{key.upper()}", CLM_WORKFLOWS[key])

    def start_clm_workflow(
        self, key: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Start one of the Party Management CLM workflows by solution key."""
        name = self.clm_workflow_name(key)
        instance = self.clm.start_workflow(name, params or {})
        return {"workflow_key": key, "workflow_name": name, "instance": instance}

    def start_bulk_metadata_workflow(
        self, csv_folder_uid: str, csv_path: str, pass1: bool = False
    ) -> Dict[str, Any]:
        """Upload a metadata CSV to CLM and start the workflow that reads it.

        Args:
            csv_folder_uid: CLM folder the workflow watches for its manifest.
            csv_path: Local CSV to upload.
            pass1: Start the NDA expiration-clearing Pass 1 workflow instead of
                the V1.4 bulk update.
        """
        document = self.clm.upload_csv(csv_folder_uid, csv_path)
        document_id = document.get("Id") or document.get("Uid")
        key = (
            "bulk_metadata_pass1_clear_nda_expiration"
            if pass1
            else "bulk_metadata_update"
        )
        started = self.start_clm_workflow(key, {"CsvDocumentId": document_id})
        started["csv_document"] = document
        return started

    def clm_agent_name(self, key: str) -> str:
        """Resolve a short agent key to the identifier a CLM step uses."""
        if key not in CLM_AI_AGENTS:
            raise ValueError(
                f"Unknown CLM AI agent key {key!r}; expected one of "
                f"{', '.join(sorted(CLM_AI_AGENTS))}"
            )
        return os.getenv(f"CLM_AI_AGENT_{key.upper()}", CLM_AI_AGENTS[key])

    def read_agent_output(
        self, instance_id: str, variable_name: str = "MFC_x_AgentOutput"
    ) -> Dict[str, Any]:
        """Read a CLM `Use AI Agent` step's output variable from an instance.

        The step writes its result into an XML workflow variable. This fetches
        the instance and returns that variable's XML along with the parsed
        element paths, so a caller can see what the agent actually returned
        before committing to an XPath against it.

        The output variable is often declared with a bare unnamed root, which
        means a later workflow step addressing it by XPath silently reads an
        empty value. ``paths`` is here to make the real shape visible: shape the
        variable to match before wiring the output into attributes.
        """
        instance = self.clm.get_workflow_instance(instance_id)
        raw = _find_workflow_variable(instance, variable_name)

        result: Dict[str, Any] = {
            "instance_id": instance_id,
            "variable": variable_name,
            "status": instance.get("Status") or instance.get("status"),
            "found": raw is not None,
        }
        if raw is None:
            result["reason"] = (
                f"No variable named {variable_name!r} on the instance. Confirm the "
                "step's Output configuration names it."
            )
            return result

        result["xml"] = raw
        result["paths"] = _xml_element_paths(raw)
        # No paths means the XML did not parse; a single path at depth one means
        # the root has no children. Either way there is nothing for a later
        # step's XPath to address, which is the bare-root case that reads as
        # empty rather than failing. A single child path like /root/Name is fine.
        paths = result["paths"]
        if not paths or (len(paths) == 1 and paths[0].count("/") == 1):
            result["warning"] = (
                "The output XML has no addressable child elements, so an XPath "
                "against it will resolve to nothing. Run the agent once, inspect "
                "this XML, and define the variable's schema to match."
            )
        return result

    def trigger_maestro_workflow(
        self, workflow_id: str, instance_name: str, trigger_inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Start a Maestro workflow on the IAM side."""
        return self.iam.trigger_workflow(workflow_id, instance_name, trigger_inputs)

    def list_maestro_workflows(self) -> Dict[str, Any]:
        return self.iam.list_workflows()


# ── Helpers ─────────────────────────────────────────────────────────────


def _find_workflow_variable(instance: Dict[str, Any], name: str) -> Optional[str]:
    """Pull one named variable's value out of a CLM workflow instance.

    CLM has returned instance variables under several shapes over the years
    (a dict keyed by name, or a list of name/value pairs under either
    PascalCase or camelCase), so each is handled rather than assumed.
    """
    containers = [
        instance.get("Variables"),
        instance.get("variables"),
        instance.get("Params"),
    ]
    for container in containers:
        if isinstance(container, dict):
            if name in container:
                value = container[name]
                if isinstance(value, dict):
                    return value.get("Value") or value.get("value")
                return value
        elif isinstance(container, list):
            for entry in container:
                if not isinstance(entry, dict):
                    continue
                if (entry.get("Name") or entry.get("name")) == name:
                    return entry.get("Value") or entry.get("value")
    return None


def _xml_element_paths(xml_text: str) -> List[str]:
    """Slash-separated paths of every element in an XML fragment.

    Returned so a caller can see the agent output's real shape before writing
    an XPath against it. Malformed XML yields an empty list rather than raising:
    an agent returning something unparseable is a reportable result, not a crash.
    """
    import xml.etree.ElementTree as ElementTree

    if not xml_text or not isinstance(xml_text, str):
        return []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    paths: List[str] = []

    def walk(element, prefix: str) -> None:
        path = f"{prefix}/{element.tag}"
        children = list(element)
        if not children:
            paths.append(path)
            return
        for child in children:
            walk(child, path)

    walk(root, "")
    # Deduplicate repeated siblings while keeping document order.
    seen = set()
    unique = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique
