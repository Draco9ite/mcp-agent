# DocuSign IAM + CLM integration

`docusign_iam/` is an agent and an Agreement Manager API that spans both halves
of DocuSign: the **IAM** platform (the Agreement Manager repository and Maestro)
and **CLM**, where the Party Management workflows and the attribute groups live.

The two systems hold the same contracts in different shapes. IAM's Agreement
Manager returns AI-extracted `provisions` and `parties`; CLM stores
operator-maintained values in named attribute groups. This package reads both,
reports where they disagree, and writes CLM metadata through the same
column-to-attribute mapping the CLM workflows use.

## What it is made of

| Module | Responsibility |
|---|---|
| `auth.py` | One token for both products. JWT grant or authorization code; discovers the IAM account and the CLM account/host. |
| `iam_client.py` | Agreement Manager API, Maestro API, eSignature API. |
| `clm_client.py` | CLM Object API: folders, documents, attribute groups, workflows, upload/download. |
| `models.py` | Normalized `Agreement`, the CLM attribute mapping, and the diff. |
| `agreement_manager.py` | The facade: reconcile, bulk metadata, workflow launching. |
| `agent.py` | Twelve tools an LLM can call, plus the dispatcher. |
| `api.py` | Flask blueprint at `/api/v1/docusign`. |
| `clm_attribute_map.json` | Generated. 248 column mappings across 8 contract types. |

## Setup

### 1. Credentials

JWT grant, for unattended runs:

```bash
INTEGRATION_KEY=<your integration key>
USER_ID=<the user GUID to impersonate>
PRIVATE_KEY_PATH=/secrets/docusign_private.key   # or PRIVATE_KEY with \n escapes
BASE_URI=https://demo.docusign.net               # demo; omit "demo" for production
```

Authorization code grant, if you would rather reuse an operator consent: set
`INTEGRATION_KEY` and `SECRET_KEY` and complete the flow once. The token cache
written by `docusign_mcp_client` can be reused by passing
`DocuSignAuth(token_file=".mcp_tokens.json")`.

### 2. Scopes

The integration requests all of these, because it spans both products:

| Scope | Needed for |
|---|---|
| `signature` | eSignature envelopes |
| `impersonation` (JWT) / `extended` (auth code) | the grant itself |
| `adm_store_unified_repo_read` | Agreement Manager repository |
| `aow_manage` | Maestro workflows |
| `spring_read`, `spring_write` | CLM |

An admin grants them once:

```python
from docusign_iam import DocuSignAuth
print(DocuSignAuth().get_consent_url())
```

### 3. CLM account

CLM has its own account ID and regional host. Both are discovered from the CLM
auth service with the same token, so nothing needs configuring in the normal
case. Pin them with `CLM_ACCOUNT_ID` and `CLM_API_BASE_URL` if discovery is
unavailable.

### 4. Confirm what is reachable

```bash
curl -s localhost:8000/api/v1/docusign/whoami | jq
```

`iam.ok` and `clm.ok` are reported separately: a partial setup — IAM working,
CLM not entitled — shows up field by field rather than as one opaque failure.
The endpoint returns 503 unless both answered.

## The Agreement Manager API

Reads are GETs. Anything that changes metadata or starts a workflow is a POST
that must carry `"confirm": true`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/docusign/whoami` | Credentials and reachability |
| GET | `/api/v1/docusign/agreements` | List agreements (`contract_type`, `status`, `expiring_before`, `search`, `limit`) |
| GET | `/api/v1/docusign/agreements/<id>` | One agreement (`include_raw=true` for the full payload) |
| GET | `/api/v1/docusign/agreements/<id>/reconcile` | Compare that agreement against CLM |
| GET | `/api/v1/docusign/reconcile/<contract_type>` | Reconcile a whole contract type |
| GET | `/api/v1/docusign/clm/documents/<id>` | A CLM document and its attribute values |
| GET | `/api/v1/docusign/clm/attribute-map` | The column-to-attribute mapping |
| GET | `/api/v1/docusign/clm/workflows` | Startable CLM workflows |
| GET | `/api/v1/docusign/maestro/workflows` | Maestro workflows |
| POST | `/api/v1/docusign/metadata/preview` | What a metadata CSV would write |
| POST | `/api/v1/docusign/metadata/apply` | Write it (needs `confirm`) |
| POST | `/api/v1/docusign/clm/documents/<id>/attributes` | Write attributes on one document |
| POST | `/api/v1/docusign/clm/workflows/<key>/start` | Start a CLM workflow (needs `confirm`) |
| POST | `/api/v1/docusign/maestro/workflows/<id>/trigger` | Trigger Maestro (needs `confirm`) |

### Reconciliation

`reconcile` compares `contract_type`, `status`, `effective_date` and
`expiration_date`. Dates are compared without a time component and strings
without case, so a formatting difference is not reported as a drift. A value
present on one side and empty on the other *is* reported — for the migration
work, an empty CLM attribute is exactly the case worth seeing.

The CLM document is found through the agreement record's `source_id`, which
Agreement Manager sets when the agreement was ingested from CLM. Pass
`clm_document_id` to compare against a specific document instead.

```bash
curl -s localhost:8000/api/v1/docusign/agreements/<id>/reconcile | jq '.differences'
```

```json
[{"field_name": "expiration_date",
  "agreement_manager_value": "2027-01-01",
  "clm_value": "2025-02-07"}]
```

That is the migrated-NDA problem Solution 2 Pass 1 exists to fix, visible
per-agreement before anything is changed.

### Bulk metadata

```bash
# 1. See what would change. Nothing is written.
curl -s -X POST localhost:8000/api/v1/docusign/metadata/preview \
  -H 'Content-Type: application/json' \
  -d '{"csv_path": "templates/Metadata_Update_NDA.csv"}' | jq '{rows_read, unmapped_columns}'

# 2. Validate against one row.
curl -s -X POST localhost:8000/api/v1/docusign/metadata/apply \
  -H 'Content-Type: application/json' \
  -d '{"csv_path": "nda_rows.csv", "max_rows": 1, "confirm": true}' | jq

# 3. Then the rest.
```

**A blank cell clears the attribute**, matching the CLM workflow. To preserve a
value, leave its column out of the CSV entirely. `unmapped_columns` in the
response names any column that resolved to no attribute, so a typo'd header
shows up as a reported column rather than as silently dropped values.

Use the API path for validation and small batches. For a full ~900-row run, the
CLM workflow is still the right tool: it pauses every 400 rows, which this does
not.

## The agent

Twelve tools, in the Azure OpenAI Assistants shape the other agents here use:

```python
from docusign_iam.agent import DocuSignIAMAgent, IAM_CLM_TOOLS, IAM_CLM_INSTRUCTIONS

agent = DocuSignIAMAgent()
agent.dispatch("reconcile_agreement", {"agreement_id": "<id>"})
```

`dispatch` accepts the JSON string an Assistants run hands back, and returns
`{"error": ...}` instead of raising, so a tool failure is something the model
can report rather than something that kills the run.

Write tools (`apply_metadata_update`, `start_clm_workflow`,
`trigger_maestro_workflow`) refuse to act without `confirm=true`, and the
instructions tell the model to show a dry run and get a go-ahead first. There is
deliberately no tool that applies a bulk change in one step.

## The CLM workflows

Keys map to the workflow definitions in
[draco9ite/smarter-docusign](https://github.com/draco9ite/smarter-docusign).
Override a name with `CLM_WORKFLOW_<KEY>` when an account renamed one on import.

| Key | CLM workflow | Solution |
|---|---|---|
| `party_management_migration` | Party Management - Document Migration Workflow | 1 |
| `bulk_metadata_update` | Bulk Metadata Update Workflow | 2 (V1.4) |
| `bulk_metadata_pass1_clear_nda_expiration` | …- Pass 1 Clear NDA Expiration Date | 2 (Pass 1) |
| `mfc_amendment_ai_review` | MFC - Amendment | 3, 5 |
| `merge_doc_combine_folder_pdfs` | Merge Doc - Combine Folder PDFs | 4 |

`start_bulk_metadata_workflow` does both halves at once: uploads the CSV to the
folder the workflow watches, then starts the workflow with that document's ID as
`CsvDocumentId`.

## The attribute mapping

`clm_attribute_map.json` is generated from
`smarter-docusign/docs/Solution2_CSV_Column_Reference.md`, so the API and the
CLM workflows agree on where every column lands:

```bash
python scripts/generate_clm_attribute_map.py \
    --reference ../smarter-docusign/docs/Solution2_CSV_Column_Reference.md
```

248 mappings across EMSNA, FFP, ISDA, LC, MNGSA, NAESB, NDA and PCG. Some
columns write to two targets — NDA `Effective_Date` writes both
*Contract Status & Administrative Metadata / Effective Date* and
*CLM Agreement Details / Effective Date* — and the map preserves that.

One column is supplemented rather than parsed: `Expiration_Date` for NDA, whose
only use is Solution 2 Pass 1 and which the V1.4 reference tables do not list.
It is declared in `SUPPLEMENTAL` in the generator, with a comment saying why.

## Tests

```bash
pytest test_iam_clm_integration.py -v
```

75 tests, fully offline: no credentials and no network. Auth is stubbed and both
clients are replaced by fakes that record their calls, so the tests assert on the
request shapes the real APIs expect. Among them, a test reads every shipped CSV
template and asserts no column is unmapped — a header renamed in one repo and not
the other fails the suite rather than silently dropping data in a production run.

## API references

- [Agreement Manager API](https://developers.docusign.com/docs/agreement-manager-api/) — `GET /v1/accounts/{accountId}/agreements`, scope `adm_store_unified_repo_read`, OData `$filter`/`$select`/`$search`
- [Maestro API](https://developers.docusign.com/docs/maestro-api/how-to/trigger-workflow/) — `POST /v1/accounts/{accountId}/workflows/{workflowId}/actions/trigger`
- [CLM API](https://developers.docusign.com/docs/clm-api/) — `/v2/{clmAccountId}/...`, scopes `spring_read`/`spring_write`
- [Authentication scopes](https://developers.docusign.com/platform/auth/reference/scopes/)
