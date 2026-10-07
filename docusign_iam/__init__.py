"""
DocuSign IAM + CLM integration.

Binds three things together behind one Agreement Manager facade:

* the **Agreement Manager API** (formerly Navigator), the AI-ready agreement
  repository in DocuSign IAM;
* **Maestro / Workflow Builder**, for IAM-side orchestration;
* **DocuSign CLM**, where the Party Management workflows and the
  operator-maintained attribute groups live.

Typical use::

    from docusign_iam import AgreementManager

    manager = AgreementManager()
    report = manager.reconcile_agreement("<agreement-id>")
    run = manager.run_bulk_metadata_update("templates/Metadata_Update_NDA.csv")

Importing this package does not perform any network call; a token is minted on
the first API call.
"""

from .auth import (
    DocuSignAuth,
    DocuSignAuthError,
    SCOPE_AGREEMENT_MANAGER,
    SCOPE_CLM,
    SCOPE_ESIGN,
    SCOPE_IAM_CLM,
    SCOPE_IAM_CLM_JWT,
    SCOPE_MAESTRO,
)
from .clm_client import CLMClient
from .iam_client import DocuSignAPIError, IAMClient
from .models import (
    Agreement,
    FieldDifference,
    Party,
    build_attribute_groups,
    diff_agreements,
    load_attribute_map,
    resolve_attribute_targets,
    supported_contract_types,
)

__all__ = [
    "Agreement",
    "AgreementManager",
    "CLMClient",
    "DocuSignAPIError",
    "DocuSignAuth",
    "DocuSignAuthError",
    "FieldDifference",
    "IAMClient",
    "Party",
    "SCOPE_AGREEMENT_MANAGER",
    "SCOPE_CLM",
    "SCOPE_ESIGN",
    "SCOPE_IAM_CLM",
    "SCOPE_IAM_CLM_JWT",
    "SCOPE_MAESTRO",
    "build_attribute_groups",
    "diff_agreements",
    "load_attribute_map",
    "resolve_attribute_targets",
    "supported_contract_types",
]


def __getattr__(name):
    # AgreementManager pulls in both clients; keep it lazy so `import
    # docusign_iam` stays cheap for callers that only want the models.
    if name == "AgreementManager":
        from .agreement_manager import AgreementManager

        return AgreementManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
