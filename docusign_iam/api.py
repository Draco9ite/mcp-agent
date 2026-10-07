#!/usr/bin/env python3
"""
REST surface for the DocuSign IAM + CLM integration.

Mounted at ``/api/v1/docusign`` by ``app.py``. Reads are GETs; anything that
changes agreement metadata or starts a workflow is a POST that must carry
``"confirm": true``, mirroring the gate in the agent layer.

Endpoints:

    GET  /api/v1/docusign/whoami
    GET  /api/v1/docusign/agreements
    GET  /api/v1/docusign/agreements/<agreement_id>
    GET  /api/v1/docusign/agreements/<agreement_id>/reconcile
    GET  /api/v1/docusign/clm/documents/<document_id>
    GET  /api/v1/docusign/clm/attribute-map
    GET  /api/v1/docusign/clm/workflows
    GET  /api/v1/docusign/maestro/workflows
    GET  /api/v1/docusign/reconcile/<contract_type>
    POST /api/v1/docusign/metadata/preview
    POST /api/v1/docusign/metadata/apply
    POST /api/v1/docusign/clm/workflows/<workflow_key>/start
    POST /api/v1/docusign/maestro/workflows/<workflow_id>/trigger
    POST /api/v1/docusign/clm/documents/<document_id>/attributes
"""

import logging
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from .agreement_manager import CLM_WORKFLOWS, AgreementManager
from .auth import DocuSignAuthError
from .iam_client import DocuSignAPIError
from .models import build_attribute_groups, load_attribute_map, supported_contract_types

logger = logging.getLogger(__name__)

iam_clm_bp = Blueprint("docusign_iam_clm", __name__, url_prefix="/api/v1/docusign")

_manager: Optional[AgreementManager] = None


def get_manager() -> AgreementManager:
    """Process-wide AgreementManager, built on first request.

    Shared so the token and the CLM account discovery are cached across
    requests rather than re-fetched per call.
    """
    global _manager
    if _manager is None:
        _manager = AgreementManager()
    return _manager


def reset_manager(manager: Optional[AgreementManager] = None) -> None:
    """Replace the cached manager. Used by tests and after a re-consent."""
    global _manager
    _manager = manager


@iam_clm_bp.errorhandler(DocuSignAuthError)
def _handle_auth_error(exc: DocuSignAuthError):
    return jsonify({"error": "authentication_failed", "detail": str(exc)}), 401


@iam_clm_bp.errorhandler(DocuSignAPIError)
def _handle_api_error(exc: DocuSignAPIError):
    return (
        jsonify({"error": "docusign_api_error", "detail": str(exc), "body": exc.body}),
        exc.status_code or 502,
    )


# ── Reads ───────────────────────────────────────────────────────────────


@iam_clm_bp.route("/whoami", methods=["GET"])
def whoami():
    """Which DocuSign surfaces this deployment can currently reach."""
    report = get_manager().whoami()
    # 200 only when both sides answered; otherwise the caller should see a failure.
    ok = report.get("iam", {}).get("ok") and report.get("clm", {}).get("ok")
    return jsonify(report), (200 if ok else 503)


@iam_clm_bp.route("/agreements", methods=["GET"])
def list_agreements():
    limit, error = _int_arg("limit", default=25, minimum=1, maximum=100)
    if error:
        return error
    agreements = get_manager().list_agreements(
        contract_type=request.args.get("contract_type"),
        status=request.args.get("status"),
        expiring_before=request.args.get("expiring_before"),
        search=request.args.get("search"),
        limit=limit,
    )
    return jsonify({"count": len(agreements), "agreements": [a.to_dict() for a in agreements]})


@iam_clm_bp.route("/agreements/<agreement_id>", methods=["GET"])
def get_agreement(agreement_id: str):
    agreement = get_manager().get_agreement(agreement_id)
    payload = agreement.to_dict()
    if request.args.get("include_raw") == "true":
        payload["raw"] = agreement.raw
    return jsonify(payload)


@iam_clm_bp.route("/agreements/<agreement_id>/reconcile", methods=["GET"])
def reconcile_agreement(agreement_id: str):
    return jsonify(
        get_manager().reconcile_agreement(
            agreement_id, clm_document_id=request.args.get("clm_document_id")
        )
    )


@iam_clm_bp.route("/reconcile/<contract_type>", methods=["GET"])
def reconcile_contract_type(contract_type: str):
    limit, error = _int_arg("limit", default=25, minimum=1, maximum=100)
    if error:
        return error
    return jsonify(get_manager().reconcile_contract_type(contract_type, limit=limit))


@iam_clm_bp.route("/clm/documents/<document_id>", methods=["GET"])
def get_clm_document(document_id: str):
    agreement = get_manager().get_clm_agreement(document_id)
    payload = agreement.to_dict()
    payload["attribute_groups"] = agreement.raw.get("AttributeGroups", {})
    return jsonify(payload)


@iam_clm_bp.route("/clm/attribute-map", methods=["GET"])
def clm_attribute_map():
    """The CSV column to CLM attribute mapping, optionally for one type."""
    contract_type = request.args.get("contract_type")
    mapping = load_attribute_map()
    if contract_type:
        if contract_type not in mapping:
            return _bad_request(
                f"Unknown contract type {contract_type!r}",
                supported=supported_contract_types(),
            )
        return jsonify({"contract_type": contract_type, "columns": mapping[contract_type]})
    return jsonify(
        {
            "contract_types": supported_contract_types(),
            "column_counts": {k: len(v) for k, v in mapping.items()},
        }
    )


@iam_clm_bp.route("/clm/workflows", methods=["GET"])
def list_clm_workflows():
    manager = get_manager()
    return jsonify(
        {
            "workflows": [
                {"key": key, "clm_workflow_name": manager.clm_workflow_name(key)}
                for key in sorted(CLM_WORKFLOWS)
            ]
        }
    )


@iam_clm_bp.route("/maestro/workflows", methods=["GET"])
def list_maestro_workflows():
    return jsonify(get_manager().list_maestro_workflows())


# ── Writes ──────────────────────────────────────────────────────────────


@iam_clm_bp.route("/metadata/preview", methods=["POST"])
def preview_metadata():
    """Show what a metadata CSV would write, without changing anything."""
    body = request.get_json(silent=True) or {}
    csv_path = body.get("csv_path")
    if not csv_path:
        return _bad_request("csv_path is required")
    try:
        return jsonify(
            get_manager().run_bulk_metadata_update(
                csv_path,
                dry_run=True,
                contract_type=body.get("contract_type"),
                max_rows=body.get("max_rows"),
            )
        )
    except (FileNotFoundError, ValueError) as exc:
        return _bad_request(str(exc))


@iam_clm_bp.route("/metadata/apply", methods=["POST"])
def apply_metadata():
    """Write a metadata CSV to CLM. Requires an explicit confirmation."""
    body = request.get_json(silent=True) or {}
    csv_path = body.get("csv_path")
    if not csv_path:
        return _bad_request("csv_path is required")
    if body.get("confirm") is not True:
        return _bad_request(
            'confirm must be true to apply. POST /metadata/preview first: blank '
            "cells clear attributes, so an unreviewed CSV can erase data."
        )
    try:
        return jsonify(
            get_manager().run_bulk_metadata_update(
                csv_path,
                dry_run=False,
                contract_type=body.get("contract_type"),
                max_rows=body.get("max_rows"),
            )
        )
    except (FileNotFoundError, ValueError) as exc:
        return _bad_request(str(exc))


@iam_clm_bp.route("/clm/documents/<document_id>/attributes", methods=["POST"])
def update_clm_attributes(document_id: str):
    """Write attributes onto one CLM document from flat column values."""
    body = request.get_json(silent=True) or {}
    contract_type = body.get("contract_type")
    values = body.get("values")
    if not contract_type or not isinstance(values, dict):
        return _bad_request("contract_type and a values object are required")
    if contract_type not in supported_contract_types():
        return _bad_request(
            f"Unknown contract type {contract_type!r}", supported=supported_contract_types()
        )

    attribute_groups, unmapped = build_attribute_groups(contract_type, values)
    if not attribute_groups:
        return _bad_request("No mapped columns carried a value", unmapped_columns=unmapped)

    dry_run = body.get("confirm") is not True
    response: Dict[str, Any] = {
        "document_id": document_id,
        "contract_type": contract_type,
        "attribute_groups": attribute_groups,
        "unmapped_columns": unmapped,
        "status": "dry_run" if dry_run else "applied",
    }
    if not dry_run:
        get_manager().clm.update_document_attributes(document_id, attribute_groups)
    return jsonify(response)


@iam_clm_bp.route("/clm/workflows/<workflow_key>/start", methods=["POST"])
def start_clm_workflow(workflow_key: str):
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return _bad_request("confirm must be true to start a CLM workflow")
    if workflow_key not in CLM_WORKFLOWS:
        return _bad_request(
            f"Unknown workflow key {workflow_key!r}", supported=sorted(CLM_WORKFLOWS)
        )
    return jsonify(get_manager().start_clm_workflow(workflow_key, body.get("params") or {}))


@iam_clm_bp.route("/maestro/workflows/<workflow_id>/trigger", methods=["POST"])
def trigger_maestro_workflow(workflow_id: str):
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return _bad_request("confirm must be true to trigger a Maestro workflow")
    instance_name = body.get("instance_name")
    if not instance_name:
        return _bad_request("instance_name is required")
    return jsonify(
        get_manager().trigger_maestro_workflow(
            workflow_id, instance_name, body.get("trigger_inputs") or {}
        )
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _bad_request(detail: str, **extra: Any):
    payload = {"error": "bad_request", "detail": detail}
    payload.update(extra)
    return jsonify(payload), 400


def _int_arg(
    name: str, default: int, minimum: int, maximum: int
) -> Tuple[int, Optional[Any]]:
    raw = request.args.get(name)
    if raw is None:
        return default, None
    try:
        value = int(raw)
    except ValueError:
        return default, _bad_request(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        return default, _bad_request(f"{name} must be between {minimum} and {maximum}")
    return value, None
