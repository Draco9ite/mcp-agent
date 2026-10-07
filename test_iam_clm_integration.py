#!/usr/bin/env python3
"""
Tests for the DocuSign IAM + CLM integration.

Runs fully offline: no DocuSign credentials and no network. Auth is stubbed
with a fake token and the two clients are replaced by fakes that record the
calls made, so the tests assert on the request shapes the real APIs expect.

    pytest test_iam_clm_integration.py -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docusign_iam.agent import DocuSignIAMAgent
from docusign_iam.agreement_manager import CLM_WORKFLOWS, AgreementManager
from docusign_iam.auth import (
    SCOPE_IAM_CLM,
    SCOPE_IAM_CLM_JWT,
    DocuSignAuth,
    DocuSignAuthError,
)
from docusign_iam.iam_client import DocuSignAPIError
from docusign_iam.models import (
    Agreement,
    build_attribute_groups,
    diff_agreements,
    load_attribute_map,
    resolve_attribute_targets,
    supported_contract_types,
)

TEMPLATES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "smarter-docusign", "templates"
)


# ── Fakes ───────────────────────────────────────────────────────────────


class FakeAuth:
    """Stands in for DocuSignAuth with a token already in hand."""

    def __init__(self, demo=True):
        class Env:
            pass

        self.env = Env()
        self.env.demo = demo
        self.env.iam_base = "https://api-d.docusign.com"
        self.scopes = list(SCOPE_IAM_CLM_JWT)
        self.use_jwt = True
        self.access_token = "fake-token"

    @property
    def account_id(self):
        return "acct-123"

    @property
    def esign_base_uri(self):
        return "https://demo.docusign.net"

    def userinfo(self):
        return {"name": "Test User", "email": "test@example.com", "sub": "user-1"}

    def clm_account(self):
        return {
            "clm_account_id": "clm-9",
            "api_base_url": "https://apiuatna11.springcm.com",
            "source": "discovery",
        }

    def auth_header(self):
        return {"Authorization": "Bearer fake-token"}

    def json_headers(self):
        return {"Authorization": "Bearer fake-token", "Content-Type": "application/json"}


class FakeIAM:
    """Records Agreement Manager / Maestro calls and returns canned payloads."""

    def __init__(self, agreements=None):
        self.calls = []
        self.agreements = agreements or {}

    def list_agreements(self, **kwargs):
        self.calls.append(("list_agreements", kwargs))
        return {
            "data": list(self.agreements.values()),
            "response_metadata": {"page_token_next": None},
        }

    def get_agreement(self, agreement_id):
        self.calls.append(("get_agreement", agreement_id))
        if agreement_id not in self.agreements:
            raise DocuSignAPIError("not found", status_code=404)
        return self.agreements[agreement_id]

    def list_workflows(self):
        self.calls.append(("list_workflows", None))
        return {"workflows": [{"id": "wf-1", "name": "Price Adjustment"}]}

    def trigger_workflow(self, workflow_id, instance_name, trigger_inputs):
        self.calls.append(("trigger_workflow", workflow_id, instance_name, trigger_inputs))
        return {"instanceId": "inst-1", "workflowId": workflow_id}


class FakeCLM:
    """Records CLM calls and returns canned documents."""

    def __init__(self, documents=None, fail_on=None):
        self.calls = []
        self.documents = documents or {}
        self.fail_on = fail_on or set()
        self._account = {
            "clm_account_id": "clm-9",
            "api_base_url": "https://apiuatna11.springcm.com",
            "source": "discovery",
        }

    @property
    def account(self):
        # A property, like the real client, so a subclass can make it fail.
        return self._account

    def get_document(self, document_id, expand=None):
        self.calls.append(("get_document", document_id, expand))
        if document_id not in self.documents:
            raise DocuSignAPIError("no such document", status_code=404)
        return self.documents[document_id]

    def update_document_attributes(self, document_id, attribute_groups):
        self.calls.append(("update_document_attributes", document_id, attribute_groups))
        if document_id in self.fail_on:
            raise DocuSignAPIError("rejected by CLM", status_code=400)
        return {"Id": document_id}

    def clear_document_attribute(self, document_id, group, attribute):
        self.calls.append(("clear_document_attribute", document_id, group, attribute))
        if document_id in self.fail_on:
            raise DocuSignAPIError("rejected by CLM", status_code=400)
        return {"Id": document_id}

    def start_workflow(self, name, params):
        self.calls.append(("start_workflow", name, params))
        return {"Id": "wfi-1", "Name": name, "Params": params}

    def upload_csv(self, folder_uid, csv_path):
        self.calls.append(("upload_csv", folder_uid, csv_path))
        return {"Id": "csv-doc-1", "Name": os.path.basename(csv_path)}


AGREEMENT_MANAGER_RECORD = {
    "id": "agr-1",
    "file_name": "Acme NDA.pdf",
    "type": {"name": "NDA"},
    "status": "Active",
    "source_id": "clm-doc-1",
    "provisions": {
        "effective_date": "2026-01-01",
        "expiration_date": "2027-01-01",
        "total_agreement_value": "25000",
        "total_agreement_value_currency_code": "USD",
    },
    "parties": [{"name_in_agreement": "Acme Corp", "role": "Counterparty", "id": "p1"}],
}

CLM_DOCUMENT = {
    "Id": "clm-doc-1",
    "Name": "Acme NDA.pdf",
    "AttributeGroups": {
        "Contract Status & Administrative Metadata": {
            "Document Type": {"Value": "NDA"},
            "Status": {"Value": "Active"},
            "Effective Date": {"Value": "2026-01-01"},
        },
        "CLM Agreement Details": {"Expiration Date": {"Value": "2025-02-07"}},
    },
}


@pytest.fixture
def manager():
    return AgreementManager(
        auth=FakeAuth(),
        iam=FakeIAM(agreements={"agr-1": AGREEMENT_MANAGER_RECORD}),
        clm=FakeCLM(documents={"clm-doc-1": CLM_DOCUMENT}),
    )


# ── Attribute map ───────────────────────────────────────────────────────


def test_attribute_map_covers_every_csv_template_contract_type():
    assert supported_contract_types() == [
        "EMSNA",
        "FFP",
        "ISDA",
        "LC",
        "MNGSA",
        "NAESB",
        "NDA",
        "PCG",
    ]


def test_effective_date_writes_to_both_nda_targets():
    # The reference document lists two targets for this column; both must be written
    # or the agreement record and the document drift apart.
    targets = resolve_attribute_targets("NDA", "Effective_Date")
    assert ("Contract Status & Administrative Metadata", "Effective Date") in targets
    assert ("CLM Agreement Details", "Effective Date") in targets


def test_pass1_expiration_date_column_is_mapped():
    # Pass 1's only mapping; absent from the V1.4 reference tables.
    assert resolve_attribute_targets("NDA", "Expiration_Date") == [
        ("CLM Agreement Details", "Expiration Date")
    ]


def test_unmapped_column_is_reported_not_guessed():
    groups, unmapped = build_attribute_groups("NDA", {"Not_A_Column": "x"})
    assert groups == {}
    assert unmapped == ["Not_A_Column"]


def test_blank_value_clears_and_none_omits():
    groups, _ = build_attribute_groups(
        "NDA", {"Expiration_Date": "", "Effective_Date": None}
    )
    assert groups == {"CLM Agreement Details": {"Expiration Date": {"Value": ""}}}


def test_qualified_column_resolves_without_a_map_entry():
    assert resolve_attribute_targets("NDA", "Custom Group / Custom Attr") == [
        ("Custom Group", "Custom Attr")
    ]


def test_every_mapped_target_is_a_group_and_attribute_pair():
    for contract_type, columns in load_attribute_map().items():
        for column, targets in columns.items():
            assert targets, f"{contract_type}/{column} has no targets"
            for target in targets:
                assert len(target) == 2, f"{contract_type}/{column}: {target}"
                assert all(part.strip() for part in target)


# ── Models ──────────────────────────────────────────────────────────────


def test_agreement_manager_record_normalizes():
    agreement = Agreement.from_agreement_manager(AGREEMENT_MANAGER_RECORD)
    assert agreement.contract_type == "NDA"
    assert agreement.effective_date == "2026-01-01"
    assert agreement.total_value == 25000.0
    assert agreement.currency == "USD"
    assert agreement.clm_document_id == "clm-doc-1"
    assert agreement.parties[0].name == "Acme Corp"


def test_clm_document_normalizes_from_attribute_groups():
    agreement = Agreement.from_clm_document(CLM_DOCUMENT)
    assert agreement.contract_type == "NDA"
    assert agreement.effective_date == "2026-01-01"
    assert agreement.expiration_date == "2025-02-07"


def test_diff_reports_the_stale_migrated_expiration_date():
    differences = diff_agreements(
        Agreement.from_agreement_manager(AGREEMENT_MANAGER_RECORD),
        Agreement.from_clm_document(CLM_DOCUMENT),
    )
    assert [d.field_name for d in differences] == ["expiration_date"]
    assert differences[0].clm_value == "2025-02-07"


def test_diff_ignores_a_time_component_and_case():
    left = Agreement(effective_date="2026-01-01T00:00:00Z", status="active")
    right = Agreement(effective_date="2026-01-01", status="Active")
    assert diff_agreements(left, right) == []


def test_to_dict_drops_the_raw_payload():
    assert "raw" not in Agreement.from_agreement_manager(AGREEMENT_MANAGER_RECORD).to_dict()


# ── Agreement Manager facade ────────────────────────────────────────────


def test_whoami_reports_both_surfaces(manager):
    report = manager.whoami()
    assert report["environment"] == "demo"
    assert report["grant"] == "jwt"
    assert report["iam"]["ok"] is True
    assert report["clm"]["clm_account_id"] == "clm-9"


def test_whoami_reports_a_clm_failure_without_failing_the_call():
    class UnentitledCLM(FakeCLM):
        @property
        def account(self):
            raise DocuSignAuthError("not entitled")

    report = AgreementManager(auth=FakeAuth(), iam=FakeIAM(), clm=UnentitledCLM()).whoami()
    assert report["iam"]["ok"] is True
    assert report["clm"]["ok"] is False
    assert "not entitled" in report["clm"]["error"]


def test_list_agreements_builds_the_odata_filter(manager):
    manager.list_agreements(contract_type="NDA", status="Active", expiring_before="2026-12-31")
    _, kwargs = manager.iam.calls[0]
    assert kwargs["odata_filter"] == (
        "type eq 'NDA' and status eq 'Active' "
        "and provisions/expiration_date le 2026-12-31"
    )


def test_reconcile_uses_the_source_id_as_the_clm_link(manager):
    report = manager.reconcile_agreement("agr-1")
    assert report["linked"] is True
    assert report["clm_document_id"] == "clm-doc-1"
    assert report["in_sync"] is False
    assert [d["field_name"] for d in report["differences"]] == ["expiration_date"]


def test_reconcile_reports_an_unlinked_agreement():
    record = dict(AGREEMENT_MANAGER_RECORD, source_id=None)
    manager = AgreementManager(
        auth=FakeAuth(), iam=FakeIAM(agreements={"agr-1": record}), clm=FakeCLM()
    )
    report = manager.reconcile_agreement("agr-1")
    assert report["linked"] is False
    assert "No CLM document is linked" in report["reason"]


def test_reconcile_reports_a_missing_clm_document(manager):
    report = manager.reconcile_agreement("agr-1", clm_document_id="does-not-exist")
    assert report["linked"] is False
    assert "could not be read" in report["reason"]


def test_reconcile_contract_type_summarizes(manager):
    summary = manager.reconcile_contract_type("NDA")
    assert summary["examined"] == 1
    assert summary["linked"] == 1
    assert summary["out_of_sync"] == 1


# ── Metadata rows ───────────────────────────────────────────────────────


def test_metadata_row_dry_run_sends_nothing(manager):
    result = manager.apply_metadata_row(
        {"Document_ID": "clm-doc-1", "Contract_Type": "NDA", "Effective_Date": "2026-03-01"}
    )
    assert result["status"] == "dry_run"
    # Three targets: Effective_Date writes two, and Contract_Type writes
    # Contract Status & Administrative Metadata / Document Type.
    assert result["attributes_written"] == 3
    assert result["attribute_groups"]["Contract Status & Administrative Metadata"][
        "Document Type"
    ] == {"Value": "NDA"}
    assert manager.clm.calls == []


def test_metadata_row_applies_when_asked(manager):
    result = manager.apply_metadata_row(
        {"Document_ID": "clm-doc-1", "Contract_Type": "NDA", "Effective_Date": "2026-03-01"},
        dry_run=False,
    )
    assert result["status"] == "applied"
    call = manager.clm.calls[0]
    assert call[0] == "update_document_attributes"
    assert call[1] == "clm-doc-1"
    assert call[2]["CLM Agreement Details"]["Effective Date"] == {"Value": "2026-03-01"}


def test_metadata_row_reports_a_clm_rejection():
    manager = AgreementManager(
        auth=FakeAuth(), iam=FakeIAM(), clm=FakeCLM(fail_on={"clm-doc-1"})
    )
    result = manager.apply_metadata_row(
        {"Document_ID": "clm-doc-1", "Contract_Type": "NDA", "Effective_Date": "2026-03-01"},
        dry_run=False,
    )
    assert result["status"] == "error"
    assert "rejected by CLM" in result["reason"]


def test_metadata_row_skips_the_template_placeholder(manager):
    result = manager.apply_metadata_row(
        {"Document_ID": "<Agreement ID>", "Contract_Type": "NDA", "Effective_Date": "x"}
    )
    assert result["status"] == "skipped"
    assert manager.clm.calls == []


def test_metadata_row_requires_a_contract_type(manager):
    result = manager.apply_metadata_row({"Document_ID": "clm-doc-1", "Effective_Date": "x"})
    assert result["status"] == "skipped"
    assert "Contract_Type" in result["reason"]


def test_metadata_row_rejects_an_unknown_contract_type(manager):
    result = manager.apply_metadata_row(
        {"Document_ID": "clm-doc-1", "Contract_Type": "MSA", "Effective_Date": "x"}
    )
    assert result["status"] == "error"
    assert "Unknown contract type" in result["reason"]


# ── CSV runs, against the real shipped templates ────────────────────────


@pytest.mark.skipif(
    not os.path.isdir(TEMPLATES), reason="smarter-docusign templates not checked out"
)
@pytest.mark.parametrize(
    "template",
    [
        "Metadata_Update_NDA.csv",
        "Metadata_Update_ISDA.csv",
        "Metadata_Update_NAESB.csv",
        "Pass1_Clear_NDA_Expiration_Date.csv",
    ],
)
def test_shipped_templates_have_no_unmapped_columns(manager, template):
    # Every column in a shipped template must resolve to a CLM attribute, or a
    # real run would silently drop that column's values.
    run = manager.run_bulk_metadata_update(os.path.join(TEMPLATES, template))
    assert run["unmapped_columns"] == []
    assert run["errors"] == 0


@pytest.mark.skipif(
    not os.path.isdir(TEMPLATES), reason="smarter-docusign templates not checked out"
)
def test_template_placeholder_rows_are_skipped_not_written(manager):
    run = manager.run_bulk_metadata_update(
        os.path.join(TEMPLATES, "Metadata_Update_NDA.csv")
    )
    assert run["rows_read"] >= 1
    assert run["skipped"] == run["rows_read"]
    assert manager.clm.calls == []


def test_csv_run_applies_rows_and_counts_them(tmp_path, manager):
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text(
        "Document_ID,Contract_Type,Effective_Date\n"
        "clm-doc-1,NDA,2026-03-01\n"
        "clm-doc-2,NDA,2026-04-01\n"
    )
    run = manager.run_bulk_metadata_update(str(csv_path), dry_run=False)
    assert run["rows_read"] == 2
    assert run["applied"] == 2
    assert len(manager.clm.calls) == 2


def test_csv_run_honours_max_rows_for_a_validation_run(tmp_path, manager):
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text(
        "Document_ID,Contract_Type,Effective_Date\n"
        "clm-doc-1,NDA,2026-03-01\n"
        "clm-doc-2,NDA,2026-04-01\n"
    )
    run = manager.run_bulk_metadata_update(str(csv_path), dry_run=False, max_rows=1)
    assert run["rows_read"] == 1
    assert len(manager.clm.calls) == 1


def test_csv_run_rejects_a_file_without_a_document_id_column(tmp_path, manager):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("Contract_Type,Effective_Date\nNDA,2026-03-01\n")
    with pytest.raises(ValueError, match="Document_ID"):
        manager.run_bulk_metadata_update(str(csv_path))


def test_csv_run_row_numbers_point_at_the_file_line(tmp_path, manager):
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("Document_ID,Contract_Type,Effective_Date\nclm-doc-1,NDA,2026-03-01\n")
    run = manager.run_bulk_metadata_update(str(csv_path))
    assert run["results"][0]["row_number"] == 2


def test_csv_run_tolerates_a_utf8_bom(tmp_path, manager):
    # Excel writes a BOM, which would otherwise corrupt the first header name.
    csv_path = tmp_path / "bom.csv"
    csv_path.write_bytes(
        b"\xef\xbb\xbfDocument_ID,Contract_Type,Effective_Date\nclm-doc-1,NDA,2026-03-01\n"
    )
    run = manager.run_bulk_metadata_update(str(csv_path))
    assert run["results"][0]["status"] == "dry_run"


def test_clear_nda_expiration_dates_targets_the_pass1_attribute(manager):
    result = manager.clear_nda_expiration_dates(["clm-doc-1"], dry_run=False)
    assert result["errors"] == 0
    assert manager.clm.calls[0] == (
        "clear_document_attribute",
        "clm-doc-1",
        "CLM Agreement Details",
        "Expiration Date",
    )


# ── Workflows ───────────────────────────────────────────────────────────


def test_clm_workflow_names_resolve_for_every_key(manager):
    for key in CLM_WORKFLOWS:
        assert manager.clm_workflow_name(key)


def test_clm_workflow_name_can_be_overridden(manager, monkeypatch):
    monkeypatch.setenv("CLM_WORKFLOW_BULK_METADATA_UPDATE", "Renamed Workflow")
    assert manager.clm_workflow_name("bulk_metadata_update") == "Renamed Workflow"


def test_unknown_clm_workflow_key_is_rejected(manager):
    with pytest.raises(ValueError, match="Unknown CLM workflow key"):
        manager.clm_workflow_name("nope")


def test_start_bulk_metadata_workflow_uploads_then_starts(tmp_path, manager):
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("Document_ID,Contract_Type\nclm-doc-1,NDA\n")
    result = manager.start_bulk_metadata_workflow("folder-1", str(csv_path))
    assert manager.clm.calls[0][0] == "upload_csv"
    name, params = manager.clm.calls[1][1], manager.clm.calls[1][2]
    assert name == "Bulk Metadata Update Workflow"
    assert params == {"CsvDocumentId": "csv-doc-1"}
    assert result["csv_document"]["Id"] == "csv-doc-1"


def test_start_bulk_metadata_workflow_pass1_uses_the_pass1_workflow(tmp_path, manager):
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("Document_ID,Contract_Type,Expiration_Date\nclm-doc-1,NDA,\n")
    manager.start_bulk_metadata_workflow("folder-1", str(csv_path), pass1=True)
    assert manager.clm.calls[1][1] == (
        "Bulk Metadata Update Workflow - Pass 1 Clear NDA Expiration Date"
    )


def test_trigger_maestro_workflow_passes_inputs_through(manager):
    manager.trigger_maestro_workflow("wf-1", "run-1", {"deal_value": 100})
    assert manager.iam.calls[0] == ("trigger_workflow", "wf-1", "run-1", {"deal_value": 100})


# ── Agent ───────────────────────────────────────────────────────────────


@pytest.fixture
def agent(manager):
    return DocuSignIAMAgent(manager=manager)


def test_every_tool_has_a_handler(agent):
    for tool in agent.tools:
        assert hasattr(agent, "_tool_" + tool["function"]["name"])


def test_tool_schemas_are_well_formed(agent):
    for tool in agent.tools:
        function = tool["function"]
        assert tool["type"] == "function"
        assert function["description"]
        assert function["parameters"]["type"] == "object"
        for required in function["parameters"].get("required", []):
            assert required in function["parameters"]["properties"]


def test_dispatch_accepts_a_json_string(agent):
    result = agent.dispatch("reconcile_agreement", json.dumps({"agreement_id": "agr-1"}))
    assert result["linked"] is True


def test_dispatch_rejects_an_unknown_tool(agent):
    assert "Unknown tool" in agent.dispatch("nope")["error"]


def test_dispatch_reports_bad_arguments(agent):
    assert "Bad arguments" in agent.dispatch("get_agreement", {"wrong": 1})["error"]


def test_dispatch_returns_errors_rather_than_raising(agent):
    result = agent.dispatch("get_agreement", {"agreement_id": "missing"})
    assert "DocuSignAPIError" in result["error"]


def test_apply_metadata_requires_confirmation(agent, tmp_path):
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("Document_ID,Contract_Type,Effective_Date\nclm-doc-1,NDA,2026-03-01\n")
    blocked = agent.dispatch("apply_metadata_update", {"csv_path": str(csv_path)})
    assert "confirm=true" in blocked["error"]
    assert agent.manager.clm.calls == []

    allowed = agent.dispatch(
        "apply_metadata_update", {"csv_path": str(csv_path), "confirm": True}
    )
    assert allowed["applied"] == 1


def test_starting_a_workflow_requires_confirmation(agent):
    assert "confirm=true" in agent.dispatch(
        "start_clm_workflow", {"workflow_key": "bulk_metadata_update"}
    )["error"]
    assert agent.manager.clm.calls == []


def test_triggering_maestro_requires_confirmation(agent):
    assert "confirm=true" in agent.dispatch(
        "trigger_maestro_workflow", {"workflow_id": "wf-1", "instance_name": "r"}
    )["error"]
    assert agent.manager.iam.calls == []


def test_preview_tool_never_writes(agent, tmp_path):
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("Document_ID,Contract_Type,Effective_Date\nclm-doc-1,NDA,2026-03-01\n")
    result = agent.dispatch("preview_metadata_update", {"csv_path": str(csv_path)})
    assert result["dry_run"] is True
    assert agent.manager.clm.calls == []


# ── Auth ────────────────────────────────────────────────────────────────


def test_scope_sets_cover_both_products():
    for scopes in (SCOPE_IAM_CLM, SCOPE_IAM_CLM_JWT):
        assert "adm_store_unified_repo_read" in scopes  # Agreement Manager
        assert "aow_manage" in scopes  # Maestro
        assert "spring_read" in scopes and "spring_write" in scopes  # CLM
    assert "impersonation" in SCOPE_IAM_CLM_JWT
    assert "extended" in SCOPE_IAM_CLM


def test_auth_without_credentials_explains_what_is_missing(tmp_path, monkeypatch):
    for name in ("INTEGRATION_KEY", "USER_ID", "PRIVATE_KEY", "PRIVATE_KEY_PATH", "SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    auth = DocuSignAuth(token_file=str(tmp_path / "tokens.json"))
    with pytest.raises(DocuSignAuthError, match="No usable credentials"):
        auth.get_access_token()


def test_consent_url_requests_every_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("INTEGRATION_KEY", "key-1")
    monkeypatch.setenv("DOCUSIGN_REDIRECT_URI", "https://example.test/oauth/callback")
    auth = DocuSignAuth(token_file=str(tmp_path / "tokens.json"))
    url = auth.get_consent_url()
    assert url.startswith("https://account-d.docusign.com/oauth/auth?")
    assert "spring_write" in url and "adm_store_unified_repo_read" in url


def test_token_cache_round_trips_and_is_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("INTEGRATION_KEY", "key-1")
    token_file = tmp_path / "tokens.json"
    auth = DocuSignAuth(token_file=str(token_file))
    auth._apply({"access_token": "tok", "refresh_token": "ref", "expires_in": 3600})

    assert oct(os.stat(token_file).st_mode)[-3:] == "600"
    reloaded = DocuSignAuth(token_file=str(token_file))
    assert reloaded.access_token == "tok"
    assert reloaded._is_expired() is False


def test_jwt_response_keeps_the_existing_refresh_token(tmp_path, monkeypatch):
    monkeypatch.setenv("INTEGRATION_KEY", "key-1")
    auth = DocuSignAuth(token_file=str(tmp_path / "tokens.json"))
    auth._apply({"access_token": "a", "refresh_token": "ref", "expires_in": 3600})
    auth._apply({"access_token": "b", "expires_in": 3600})  # JWT grant: no refresh token
    assert auth.refresh_token == "ref"


def test_private_key_newline_escapes_are_restored(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_KEY", "-----BEGIN KEY-----\\nabc\\n-----END KEY-----")
    monkeypatch.setenv("USER_ID", "user-1")
    auth = DocuSignAuth(token_file=str(tmp_path / "tokens.json"))
    assert "\n" in auth.private_key and "\\n" not in auth.private_key
    assert auth.use_jwt is True


def test_clm_discovery_prefers_explicit_environment_values(tmp_path, monkeypatch):
    monkeypatch.setenv("CLM_ACCOUNT_ID", "clm-explicit")
    monkeypatch.setenv("CLM_API_BASE_URL", "https://apiuatna11.springcm.com/")
    auth = DocuSignAuth(token_file=str(tmp_path / "tokens.json"))
    account = auth.clm_account()
    assert account == {
        "clm_account_id": "clm-explicit",
        "api_base_url": "https://apiuatna11.springcm.com",
        "source": "environment",
    }


# ── REST API ────────────────────────────────────────────────────────────


@pytest.fixture
def client(manager):
    from flask import Flask

    from docusign_iam.api import iam_clm_bp, reset_manager

    app = Flask(__name__)
    app.register_blueprint(iam_clm_bp)
    reset_manager(manager)
    try:
        with app.test_client() as test_client:
            yield test_client
    finally:
        reset_manager(None)


def test_whoami_endpoint_reports_both_surfaces(client):
    response = client.get("/api/v1/docusign/whoami")
    assert response.status_code == 200
    assert response.get_json()["clm"]["clm_account_id"] == "clm-9"


def test_whoami_endpoint_is_unavailable_when_a_surface_is_down(manager):
    from flask import Flask

    from docusign_iam.api import iam_clm_bp, reset_manager

    class UnentitledCLM(FakeCLM):
        @property
        def account(self):
            raise DocuSignAuthError("not entitled")

    app = Flask(__name__)
    app.register_blueprint(iam_clm_bp)
    reset_manager(AgreementManager(auth=FakeAuth(), iam=FakeIAM(), clm=UnentitledCLM()))
    try:
        with app.test_client() as test_client:
            assert test_client.get("/api/v1/docusign/whoami").status_code == 503
    finally:
        reset_manager(None)


def test_agreements_endpoint_lists(client):
    body = client.get("/api/v1/docusign/agreements?contract_type=NDA").get_json()
    assert body["count"] == 1
    assert body["agreements"][0]["id"] == "agr-1"


def test_agreements_endpoint_validates_limit(client):
    response = client.get("/api/v1/docusign/agreements?limit=500")
    assert response.status_code == 400
    assert "between 1 and 100" in response.get_json()["detail"]


def test_reconcile_endpoint_returns_the_differences(client):
    body = client.get("/api/v1/docusign/agreements/agr-1/reconcile").get_json()
    assert body["in_sync"] is False
    assert body["differences"][0]["field_name"] == "expiration_date"


def test_attribute_map_endpoint_lists_contract_types(client):
    body = client.get("/api/v1/docusign/clm/attribute-map").get_json()
    assert "NDA" in body["contract_types"]
    assert body["column_counts"]["NDA"] > 0


def test_attribute_map_endpoint_rejects_an_unknown_type(client):
    response = client.get("/api/v1/docusign/clm/attribute-map?contract_type=MSA")
    assert response.status_code == 400
    assert "NDA" in response.get_json()["supported"]


def test_metadata_apply_endpoint_requires_confirm(client, tmp_path, manager):
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("Document_ID,Contract_Type,Effective_Date\nclm-doc-1,NDA,2026-03-01\n")

    blocked = client.post(
        "/api/v1/docusign/metadata/apply", json={"csv_path": str(csv_path)}
    )
    assert blocked.status_code == 400
    assert manager.clm.calls == []

    allowed = client.post(
        "/api/v1/docusign/metadata/apply",
        json={"csv_path": str(csv_path), "confirm": True},
    )
    assert allowed.status_code == 200
    assert allowed.get_json()["applied"] == 1


def test_metadata_preview_endpoint_never_writes(client, tmp_path, manager):
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("Document_ID,Contract_Type,Effective_Date\nclm-doc-1,NDA,2026-03-01\n")
    body = client.post(
        "/api/v1/docusign/metadata/preview", json={"csv_path": str(csv_path)}
    ).get_json()
    assert body["dry_run"] is True
    assert manager.clm.calls == []


def test_metadata_preview_endpoint_reports_a_missing_file(client):
    response = client.post(
        "/api/v1/docusign/metadata/preview", json={"csv_path": "/no/such.csv"}
    )
    assert response.status_code == 400


def test_document_attributes_endpoint_defaults_to_a_dry_run(client, manager):
    body = client.post(
        "/api/v1/docusign/clm/documents/clm-doc-1/attributes",
        json={"contract_type": "NDA", "values": {"Effective_Date": "2026-03-01"}},
    ).get_json()
    assert body["status"] == "dry_run"
    assert manager.clm.calls == []


def test_document_attributes_endpoint_applies_with_confirm(client, manager):
    body = client.post(
        "/api/v1/docusign/clm/documents/clm-doc-1/attributes",
        json={
            "contract_type": "NDA",
            "values": {"Effective_Date": "2026-03-01"},
            "confirm": True,
        },
    ).get_json()
    assert body["status"] == "applied"
    assert manager.clm.calls[0][0] == "update_document_attributes"


def test_clm_workflow_start_endpoint_requires_confirm(client, manager):
    response = client.post(
        "/api/v1/docusign/clm/workflows/bulk_metadata_update/start", json={}
    )
    assert response.status_code == 400
    assert manager.clm.calls == []


def test_clm_workflow_start_endpoint_rejects_an_unknown_key(client):
    response = client.post(
        "/api/v1/docusign/clm/workflows/nope/start", json={"confirm": True}
    )
    assert response.status_code == 400
    assert "bulk_metadata_update" in response.get_json()["supported"]


def test_clm_workflow_start_endpoint_starts_with_confirm(client, manager):
    body = client.post(
        "/api/v1/docusign/clm/workflows/bulk_metadata_update/start",
        json={"confirm": True, "params": {"CsvDocumentId": "csv-1"}},
    ).get_json()
    assert body["workflow_name"] == "Bulk Metadata Update Workflow"
    assert manager.clm.calls[0] == (
        "start_workflow",
        "Bulk Metadata Update Workflow",
        {"CsvDocumentId": "csv-1"},
    )


def test_maestro_trigger_endpoint_requires_confirm_and_a_name(client, manager):
    assert (
        client.post(
            "/api/v1/docusign/maestro/workflows/wf-1/trigger", json={"instance_name": "r"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/docusign/maestro/workflows/wf-1/trigger", json={"confirm": True}
        ).status_code
        == 400
    )
    assert manager.iam.calls == []


# ── CLM AI agent steps ──────────────────────────────────────────────────


def test_agent_keys_resolve_to_the_docusign_agent_identifier(manager):
    assert manager.clm_agent_name("counterparty_brief") == "system_counterparty_brief_agent"


def test_agent_key_can_be_overridden(manager, monkeypatch):
    monkeypatch.setenv("CLM_AI_AGENT_COUNTERPARTY_BRIEF", "custom_brief_agent")
    assert manager.clm_agent_name("counterparty_brief") == "custom_brief_agent"


def test_unknown_agent_key_is_rejected(manager):
    with pytest.raises(ValueError, match="Unknown CLM AI agent key"):
        manager.clm_agent_name("nope")


def test_xml_element_paths_shows_the_real_shape():
    from docusign_iam.agreement_manager import _xml_element_paths

    assert _xml_element_paths(
        "<root><CounterpartyName>Acme</CounterpartyName><Terms><Length>12</Length></Terms></root>"
    ) == ["/root/CounterpartyName", "/root/Terms/Length"]


def test_xml_element_paths_tolerates_malformed_output():
    from docusign_iam.agreement_manager import _xml_element_paths

    # An agent returning unparseable XML is a reportable result, not a crash.
    assert _xml_element_paths("<root>") == []
    assert _xml_element_paths("") == []
    assert _xml_element_paths(None) == []


def test_xml_element_paths_deduplicates_repeated_siblings():
    from docusign_iam.agreement_manager import _xml_element_paths

    assert _xml_element_paths("<root><Item>a</Item><Item>b</Item></root>") == ["/root/Item"]


@pytest.mark.parametrize(
    "instance",
    [
        {"Variables": {"MFC_x_AgentOutput": {"Value": "<root><Name>Acme</Name></root>"}}},
        {"Variables": {"MFC_x_AgentOutput": "<root><Name>Acme</Name></root>"}},
        {"variables": [{"name": "MFC_x_AgentOutput", "value": "<root><Name>Acme</Name></root>"}]},
        {"Variables": [{"Name": "MFC_x_AgentOutput", "Value": "<root><Name>Acme</Name></root>"}]},
    ],
)
def test_agent_output_is_found_in_every_instance_variable_shape(manager, instance):
    # CLM has returned instance variables under several shapes; all must work.
    manager.clm.get_workflow_instance = lambda _id: dict(instance, Status="Completed")
    result = manager.read_agent_output("wfi-1")
    assert result["found"] is True
    assert result["paths"] == ["/root/Name"]
    assert "warning" not in result


def test_agent_output_warns_when_the_root_has_no_children(manager):
    # The reference workflow declares MFC_x_AgentOutput as a bare root, which a
    # later step's XPath reads as empty instead of failing.
    manager.clm.get_workflow_instance = lambda _id: {
        "Status": "Completed",
        "Variables": {"MFC_x_AgentOutput": "<root/>"},
    }
    result = manager.read_agent_output("wfi-1")
    assert result["found"] is True
    assert "resolve to nothing" in result["warning"]


def test_agent_output_reports_a_missing_variable(manager):
    manager.clm.get_workflow_instance = lambda _id: {"Status": "Completed", "Variables": {}}
    result = manager.read_agent_output("wfi-1")
    assert result["found"] is False
    assert "MFC_x_AgentOutput" in result["reason"]


def test_agent_output_variable_name_is_configurable(manager):
    manager.clm.get_workflow_instance = lambda _id: {
        "Variables": {"MyOutput": "<root><A>1</A></root>"}
    }
    assert manager.read_agent_output("wfi-1", variable_name="MyOutput")["found"] is True


def test_ai_agent_reference_workflow_is_startable(manager):
    assert manager.clm_workflow_name("ai_agent_reference") == "Agentic Fun - LZ Test"


def test_agent_output_endpoint_returns_the_paths(client, manager):
    manager.clm.get_workflow_instance = lambda _id: {
        "Status": "Completed",
        "Variables": {"MFC_x_AgentOutput": "<root><Name>Acme</Name></root>"},
    }
    body = client.get("/api/v1/docusign/clm/instances/wfi-1/agent-output").get_json()
    assert body["paths"] == ["/root/Name"]


def test_agent_output_endpoint_honours_the_variable_query(client, manager):
    manager.clm.get_workflow_instance = lambda _id: {"Variables": {"MyOut": "<r><A>1</A></r>"}}
    body = client.get(
        "/api/v1/docusign/clm/instances/wfi-1/agent-output?variable=MyOut"
    ).get_json()
    assert body["found"] is True


def test_agent_output_tool_is_dispatchable(agent):
    agent.manager.clm.get_workflow_instance = lambda _id: {
        "Variables": {"MFC_x_AgentOutput": "<root><Name>Acme</Name></root>"}
    }
    assert agent.dispatch("read_agent_output", {"instance_id": "wfi-1"})["paths"] == [
        "/root/Name"
    ]
