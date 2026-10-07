#!/usr/bin/env python3
"""
DocuSign IAM client: Agreement Manager, Maestro and eSignature.

Three IAM surfaces, one session:

* **Agreement Manager API** (formerly Navigator) — ``/v1/accounts/{accountId}/agreements``
  on ``api-d.docusign.com``, the AI-extracted agreement repository. Supports the
  OData ``$filter``, ``$select`` and ``$search`` query parameters.
* **Maestro / Workflow Builder API** — ``/v1/accounts/{accountId}/workflows`` on the
  same host, for triggering and observing orchestration workflows.
* **eSignature API** — ``{baseUri}/restapi/v2.1/accounts/{accountId}`` for envelopes.

The MCP server exposed by ``docusign_mcp_client`` covers much of the same ground
through a JSON-RPC tool interface. This client talks to the REST APIs directly so
that paging, OData filtering and error handling are explicit, which the bulk CLM
reconciliation work depends on.
"""

import logging
from typing import Any, Dict, Iterator, List, Optional

import requests

from .auth import DocuSignAuth

logger = logging.getLogger(__name__)

#: Agreement Manager caps page size; requests above this are rejected.
MAX_PAGE_SIZE = 100


class DocuSignAPIError(RuntimeError):
    """A DocuSign REST call returned a non-success status."""

    def __init__(self, message: str, status_code: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class IAMClient:
    """REST client for the DocuSign IAM platform APIs."""

    def __init__(self, auth: Optional[DocuSignAuth] = None, timeout: int = 60):
        self.auth = auth or DocuSignAuth()
        self.timeout = timeout
        self.session = requests.Session()

    # ── Low-level request ───────────────────────────────────────────────

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
    ) -> Any:
        response = self.session.request(
            method,
            url,
            headers=self.auth.json_headers(),
            params=params,
            json=json_body,
            timeout=self.timeout,
        )

        # A 401 here usually means the cached token aged out mid-run; mint a new
        # one and retry exactly once so a long bulk job does not die halfway.
        if response.status_code == 401:
            self.auth.access_token = None
            response = self.session.request(
                method,
                url,
                headers=self.auth.json_headers(),
                params=params,
                json=json_body,
                timeout=self.timeout,
            )

        if not response.ok:
            raise DocuSignAPIError(
                f"{method} {url} failed with {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                body=_safe_json(response),
            )
        if not response.content:
            return {}
        return _safe_json(response)

    @property
    def _iam_account_base(self) -> str:
        return f"{self.auth.env.iam_base}/v1/accounts/{self.auth.account_id}"

    # ── Agreement Manager API ───────────────────────────────────────────

    def list_agreements(
        self,
        odata_filter: Optional[str] = None,
        select: Optional[List[str]] = None,
        search: Optional[str] = None,
        limit: int = MAX_PAGE_SIZE,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """One page of agreements from the Agreement Manager repository.

        Args:
            odata_filter: OData ``$filter`` expression, e.g.
                ``"provisions/expiration_date lt 2026-12-31"``.
            select: Field names for ``$select``, to keep responses small.
            search: Free-text ``$search`` term.
            limit: Page size, capped at ``MAX_PAGE_SIZE``.
            page_token: Continuation token from a previous response.

        Returns:
            The raw response, whose ``data`` key holds the agreements and whose
            ``response_metadata`` key holds the next page token.
        """
        params: Dict[str, Any] = {"limit": min(limit, MAX_PAGE_SIZE)}
        if odata_filter:
            params["$filter"] = odata_filter
        if select:
            params["$select"] = ",".join(select)
        if search:
            params["$search"] = search
        if page_token:
            params["page_token"] = page_token
        return self._request("GET", f"{self._iam_account_base}/agreements", params=params)

    def iter_agreements(
        self,
        odata_filter: Optional[str] = None,
        select: Optional[List[str]] = None,
        search: Optional[str] = None,
        page_size: int = MAX_PAGE_SIZE,
        max_pages: int = 100,
    ) -> Iterator[Dict[str, Any]]:
        """Yield agreements across pages, following the continuation token.

        ``max_pages`` bounds the walk so a filter that matches the whole
        repository cannot turn into an unbounded crawl.
        """
        page_token: Optional[str] = None
        for page_number in range(max_pages):
            page = self.list_agreements(
                odata_filter=odata_filter,
                select=select,
                search=search,
                limit=page_size,
                page_token=page_token,
            )
            rows = page.get("data") or page.get("agreements") or []
            for row in rows:
                yield row

            metadata = page.get("response_metadata") or {}
            page_token = metadata.get("page_token_next") or metadata.get("pageTokenNext")
            if not page_token or not rows:
                return
            logger.debug("Agreement page %s exhausted, continuing", page_number + 1)
        logger.warning("Stopped paging agreements after max_pages=%s", max_pages)

    def get_agreement(self, agreement_id: str) -> Dict[str, Any]:
        """Full record for one agreement, including AI-extracted provisions."""
        return self._request("GET", f"{self._iam_account_base}/agreements/{agreement_id}")

    def expiring_agreements(
        self,
        start_date: str,
        end_date: str,
        agreement_type: Optional[str] = None,
        page_size: int = MAX_PAGE_SIZE,
    ) -> List[Dict[str, Any]]:
        """Agreements whose expiration date falls inside a window.

        Args:
            start_date: Inclusive lower bound, ``YYYY-MM-DD``.
            end_date: Inclusive upper bound, ``YYYY-MM-DD``.
            agreement_type: Optional agreement type to narrow to, e.g. ``"NDA"``.
        """
        clauses = [
            f"provisions/expiration_date ge {start_date}",
            f"provisions/expiration_date le {end_date}",
        ]
        if agreement_type:
            clauses.append(f"type eq '{agreement_type}'")
        return list(
            self.iter_agreements(odata_filter=" and ".join(clauses), page_size=page_size)
        )

    # ── Maestro / Workflow Builder API ──────────────────────────────────

    def list_workflows(self) -> Dict[str, Any]:
        """Maestro workflows published in the account."""
        return self._request("GET", f"{self._iam_account_base}/workflows")

    def get_workflow_trigger_requirements(self, workflow_id: str) -> Dict[str, Any]:
        """Trigger URL and input schema a workflow expects."""
        return self._request(
            "GET", f"{self._iam_account_base}/workflows/{workflow_id}/trigger-requirements"
        )

    def trigger_workflow(
        self, workflow_id: str, instance_name: str, trigger_inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Start a Maestro workflow instance.

        Args:
            workflow_id: The published workflow's ID.
            instance_name: Human-readable name for the new instance.
            trigger_inputs: Values matching the workflow's trigger schema.
        """
        return self._request(
            "POST",
            f"{self._iam_account_base}/workflows/{workflow_id}/actions/trigger",
            json_body={"instanceName": instance_name, "triggerInputs": trigger_inputs},
        )

    def list_workflow_instances(self, workflow_id: str) -> Dict[str, Any]:
        return self._request(
            "GET", f"{self._iam_account_base}/workflows/{workflow_id}/instances"
        )

    def get_workflow_instance(self, workflow_id: str, instance_id: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"{self._iam_account_base}/workflows/{workflow_id}/instances/{instance_id}",
        )

    def cancel_workflow_instance(self, workflow_id: str, instance_id: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"{self._iam_account_base}/workflows/{workflow_id}/instances/{instance_id}"
            "/actions/cancel",
        )

    # ── eSignature API ──────────────────────────────────────────────────

    @property
    def _esign_account_base(self) -> str:
        base = self.auth.esign_base_uri.rstrip("/")
        # userinfo returns the base URI already including /restapi.
        if base.endswith("/restapi"):
            return f"{base}/v2.1/accounts/{self.auth.account_id}"
        return f"{base}/restapi/v2.1/accounts/{self.auth.account_id}"

    def list_envelopes(self, from_date: str, status: Optional[str] = None) -> Dict[str, Any]:
        """Envelopes changed since ``from_date`` (``YYYY-MM-DD``)."""
        params: Dict[str, Any] = {"from_date": from_date}
        if status:
            params["status"] = status
        return self._request("GET", f"{self._esign_account_base}/envelopes", params=params)

    def get_envelope(self, envelope_id: str) -> Dict[str, Any]:
        return self._request("GET", f"{self._esign_account_base}/envelopes/{envelope_id}")


def _safe_json(response: "requests.Response") -> Any:
    try:
        return response.json()
    except ValueError:
        return {"text": response.text, "status_code": response.status_code}
