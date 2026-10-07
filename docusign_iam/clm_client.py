#!/usr/bin/env python3
"""
DocuSign CLM (formerly SpringCM) client.

CLM is a separate service from the IAM APIs: its own regional host, its own
account ID, and the ``spring_read`` / ``spring_write`` OAuth scopes. Both the
host and the account ID come from :meth:`DocuSignAuth.clm_account`, so nothing
here hard-codes a region.

Object API calls go to ``{apiBaseUrl}/v2/{clmAccountId}/...``. Document content
(upload and download) is served by the content host rather than the object host,
so uploads post to ``{contentBaseUrl}/content/v2/{clmAccountId}/folders/{id}/documents``.

The CLM workflows this drives are the Party Management solutions kept in the
``smarter-docusign`` repository (Solution 1 through Solution 5); see
``docs/IAM_CLM_Integration.md`` for the parameter contract of each.
"""

import logging
import os
from typing import Any, BinaryIO, Dict, List, Optional

import requests

from .auth import DocuSignAuth
from .iam_client import DocuSignAPIError, _safe_json

logger = logging.getLogger(__name__)


class CLMClient:
    """REST client for the CLM Object API and content endpoints."""

    def __init__(self, auth: Optional[DocuSignAuth] = None, timeout: int = 120):
        self.auth = auth or DocuSignAuth()
        self.timeout = timeout
        self.session = requests.Session()
        self._account: Optional[Dict[str, Any]] = None

    # ── Account resolution ──────────────────────────────────────────────

    @property
    def account(self) -> Dict[str, Any]:
        if self._account is None:
            self._account = self.auth.clm_account()
        return self._account

    @property
    def clm_account_id(self) -> str:
        return self.account["clm_account_id"]

    @property
    def object_base(self) -> str:
        """Base path for Object API calls, including the account segment."""
        return f"{self.account['api_base_url']}/v2/{self.clm_account_id}"

    @property
    def content_base(self) -> str:
        """Base path for upload and download calls.

        CLM serves document content from a content host. ``CLM_CONTENT_BASE_URL``
        overrides it for accounts whose content host does not follow the default
        ``api.`` → ``content.`` substitution.
        """
        explicit = os.getenv("CLM_CONTENT_BASE_URL")
        base = explicit.rstrip("/") if explicit else self.account["api_base_url"]
        return f"{base}/content/v2/{self.clm_account_id}"

    # ── Low-level request ───────────────────────────────────────────────

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        files: Optional[Dict[str, Any]] = None,
        stream: bool = False,
    ) -> Any:
        headers = self.auth.auth_header()
        headers["Accept"] = "application/json"
        if files is None:
            # requests sets the multipart Content-Type itself; setting it here
            # would clobber the boundary.
            headers["Content-Type"] = "application/json"

        def send() -> "requests.Response":
            return self.session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                files=files,
                timeout=self.timeout,
                stream=stream,
            )

        response = send()
        if response.status_code == 401:
            self.auth.access_token = None
            headers.update(self.auth.auth_header())
            response = send()

        if not response.ok:
            raise DocuSignAPIError(
                f"{method} {url} failed with {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                body=_safe_json(response),
            )
        if stream:
            return response
        if not response.content:
            return {}
        return _safe_json(response)

    # ── Folders ─────────────────────────────────────────────────────────

    def get_folder(self, folder_uid: str, expand: Optional[List[str]] = None) -> Dict[str, Any]:
        params = {"expand": ",".join(expand)} if expand else None
        return self._request("GET", f"{self.object_base}/folders/{folder_uid}", params=params)

    def find_folder_by_path(self, path: str) -> Dict[str, Any]:
        """Resolve a CLM folder by its path, e.g. ``/Accounts/Migration/NDA``."""
        return self._request(
            "GET", f"{self.object_base}/folders", params={"path": path}
        )

    def list_folder_documents(
        self,
        folder_uid: str,
        offset: int = 0,
        limit: int = 100,
        expand: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Documents directly inside a folder.

        Args:
            expand: CLM expansions to include, e.g. ``["AttributeGroups"]`` to
                get each document's attribute values in the same call.
        """
        params: Dict[str, Any] = {"offset": offset, "limit": limit}
        if expand:
            params["expand"] = ",".join(expand)
        return self._request(
            "GET", f"{self.object_base}/folders/{folder_uid}/documents", params=params
        )

    def iter_folder_documents(
        self, folder_uid: str, expand: Optional[List[str]] = None, page_size: int = 100
    ):
        """Yield every document in a folder, paging by offset."""
        offset = 0
        while True:
            page = self.list_folder_documents(
                folder_uid, offset=offset, limit=page_size, expand=expand
            )
            items = page.get("Items") or page.get("items") or []
            for item in items:
                yield item
            if len(items) < page_size:
                return
            offset += page_size

    # ── Documents ───────────────────────────────────────────────────────

    def get_document(
        self, document_uid: str, expand: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        params = {"expand": ",".join(expand)} if expand else None
        return self._request(
            "GET", f"{self.object_base}/documents/{document_uid}", params=params
        )

    def get_document_attributes(self, document_uid: str) -> Dict[str, Any]:
        """A document's attribute groups and their current values."""
        document = self.get_document(document_uid, expand=["AttributeGroups"])
        return document.get("AttributeGroups", {}) or {}

    def patch_document(self, document_uid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Partially update a document. The CLM API has no PUT for attributes."""
        return self._request(
            "PATCH", f"{self.object_base}/documents/{document_uid}", json_body=body
        )

    def update_document_attributes(
        self, document_uid: str, attribute_groups: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Write attribute values onto a document.

        Args:
            attribute_groups: Nested CLM shape, e.g.::

                {"Agreement Attributes": {
                    "Effective Date": {"Value": "2026-01-01"},
                    "Counterparty": {"Value": "Acme Corp"}}}

            Use :func:`docusign_iam.models.build_attribute_groups` to produce it
            from flat column/value pairs such as a migration CSV row.
        """
        return self.patch_document(document_uid, {"AttributeGroups": attribute_groups})

    def clear_document_attribute(
        self, document_uid: str, group_name: str, attribute_name: str
    ) -> Dict[str, Any]:
        """Blank one attribute, as Solution 2 Pass 1 does for NDA Expiration Date.

        CLM clears a value when it is written as an empty string; omitting the
        attribute leaves the old value in place.
        """
        return self.update_document_attributes(
            document_uid, {group_name: {attribute_name: {"Value": ""}}}
        )

    def list_attribute_groups(self) -> Dict[str, Any]:
        """Attribute group definitions configured on the account."""
        return self._request("GET", f"{self.object_base}/attributegroups")

    def get_attribute_group(self, group_id: str) -> Dict[str, Any]:
        return self._request("GET", f"{self.object_base}/attributegroups/{group_id}")

    # ── Workflows ───────────────────────────────────────────────────────

    def start_workflow(self, workflow_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Start a CLM workflow by name.

        Args:
            workflow_name: The workflow's name in CLM, e.g.
                ``"Solution 2 - Bulk Metadata Update"``.
            params: Values bound to the workflow's ``/Params`` XPath root. The
                workflow reads them with expressions such as
                ``/Params/CsvDocumentId/text()``.

        Returns:
            The created workflow instance, including its ``Href`` and ``Id``.
        """
        return self._request(
            "POST",
            f"{self.object_base}/workflows",
            json_body={"Name": workflow_name, "Params": params or {}},
        )

    def get_workflow_instance(self, instance_id: str) -> Dict[str, Any]:
        return self._request(
            "GET", f"{self.object_base}/workflowinstances/{instance_id}"
        )

    def list_workflow_instances(
        self, workflow_name: Optional[str] = None, status: Optional[str] = None
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if workflow_name:
            params["name"] = workflow_name
        if status:
            params["status"] = status
        return self._request(
            "GET", f"{self.object_base}/workflowinstances", params=params or None
        )

    # ── Content ─────────────────────────────────────────────────────────

    def upload_document(
        self,
        folder_uid: str,
        file_name: str,
        file_obj: BinaryIO,
        content_type: str = "application/octet-stream",
    ) -> Dict[str, Any]:
        """Upload a file into a CLM folder.

        Used to stage the migration and metadata CSV manifests that Solution 1
        and Solution 2 read, so a run can be launched end to end from the API.
        """
        return self._request(
            "POST",
            f"{self.content_base}/folders/{folder_uid}/documents",
            files={"file": (file_name, file_obj, content_type)},
        )

    def upload_csv(self, folder_uid: str, csv_path: str) -> Dict[str, Any]:
        """Upload a CSV manifest from disk and return the created document."""
        with open(csv_path, "rb") as handle:
            return self.upload_document(
                folder_uid, os.path.basename(csv_path), handle, "text/csv"
            )

    def download_document(self, document_uid: str, destination_path: str) -> str:
        """Stream a document's current version to a local path."""
        response = self._request(
            "GET",
            f"{self.content_base}/documents/{document_uid}",
            stream=True,
        )
        with open(destination_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    handle.write(chunk)
        return destination_path
