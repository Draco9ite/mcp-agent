# Connecting the Docusign demo org to Claude

There are **two separate paths** into Docusign, and they need different setup.
Doing one does not give you the other.

| | **Connector path** (MCP) | **Code path** (`docusign_iam/`) |
|---|---|---|
| What calls Docusign | Anthropic's infrastructure, on Claude's behalf | this app, from wherever it runs |
| Setup | a Docusign app + a Claude custom connector | a Docusign app + credentials in the environment |
| Gives you | Docusign tools inside a Claude session | the Agreement Manager REST API at `/api/v1/docusign` |
| Needs outbound network to `*.docusign.com` from this container | **no** | **yes** |

Both read from the same Docusign app, so the Integration Key and secret you
create once serve both.

---

## Path 1 — The Claude connector

### Step 1: Docusign Admin → Apps and Keys

1. Create an app. Copy the **Integration Key (IK)**.
2. Add a **Client Secret** and copy it immediately — it is not shown again.
3. Add **both** of these Redirect URIs:

   ```
   https://claude.ai/api/mcp/auth_callback
   https://claude.com/api/mcp/auth_callback
   ```

### Step 2: claude.ai → Settings → Connectors

Select **+ Add → Add custom connector**, then fill in:

| Field | Value |
|---|---|
| Name | `Docusign` (anything) |
| Remote MCP server URL | `https://mcp-d.docusign.com/mcp` (demo) |
| Client ID | your Integration Key |
| Client Secret | the secret from step 1 |

If the dialog shows name and URL on one screen, the client ID and secret go
under **Advanced settings**. Select **Add**, then **Connect**, then sign in to
Docusign and **Allow Access**.

Production uses `https://mcp.docusign.com/mcp`. Start on demo.

### Step 3: Start a new session

**Connectors are read when a session starts.** An existing session will not see
a connector you just added — open a new one.

To confirm it worked, ask Claude what connectors it has; the Docusign tools
should be listed.

---

## Path 2 — This app's Agreement Manager API

The REST API in `docusign_iam/` calls Docusign directly rather than through
Claude, so it needs its own credentials and its own network access.

### Credentials

Use the same app from Path 1. For unattended runs, prefer the JWT grant:

```bash
INTEGRATION_KEY=<your integration key>
USER_ID=<the user GUID to impersonate>
PRIVATE_KEY_PATH=/secrets/docusign_private.key   # RSA keypair from Apps and Keys
BASE_URI=https://demo.docusign.net
```

Grant consent once, as the user being impersonated:

```bash
./scripts/consent_url.sh          # prints the URL with all six scopes
```

### Network access

If this app runs in a Claude cloud container, outbound HTTPS to Docusign must
be allowed or every call fails with a proxy `403`. Set it in the cloud
environment menu → **Edit → Network access**: either a broader level, or Custom
with these under Allowed domains, keeping the default package-manager list:

```
account-d.docusign.com      # OAuth (demo)
api-d.docusign.com          # Agreement Manager + Maestro (demo)
demo.docusign.net           # eSignature (demo)
authuat.springcm.com        # CLM account discovery (demo)
apiuatna11.springcm.com     # CLM Object API (demo; your region may differ)
```

Swap in the production equivalents (`account.docusign.com`,
`api.docusign.com`, `auth.springcm.com`, …) when you move off demo.

### Confirm

```bash
curl -s localhost:8000/api/v1/docusign/whoami | jq
```

`iam.ok` and `clm.ok` are reported separately, so a partial setup tells you
which half is missing instead of failing opaquely.

---

## Scopes

The Docusign MCP server advertises six scopes, and this repo now requests all of
the non-browser ones in both paths:

| Scope | Needed for | Was it requested before? |
|---|---|---|
| `signature` | eSignature | yes |
| `aow_manage` | Maestro / Workflow Builder | yes |
| `impersonation` (JWT) / `extended` (auth code) | the grant itself | yes |
| `adm_store_unified_repo_read` | **Agreement Manager repository** | **no** |
| `spring_read`, `spring_write` | **CLM** | **no** |
| `cors` | browser-based callers only | n/a |

The two in bold were missing from this repo's defaults. Without them the
eSignature and Maestro tools work while every Agreement Manager and CLM call
fails on authorization — which looks like a broken integration rather than a
missing scope. Fixed in `docusign_mcp_client.py` (`MCP_SCOPES`),
`docusign_iam/auth.py` (`SCOPE_IAM_CLM`) and `scripts/consent_url.sh`.

If you had already granted consent with the old scope set, **re-grant it** —
consent is per scope set, so the new scopes are not picked up until you do.

## MCP endpoint

The default is now the official host, chosen from `BASE_URI`:

| Environment | Endpoint |
|---|---|
| demo | `https://mcp-d.docusign.com/mcp` |
| production | `https://mcp.docusign.com/mcp` |

This repo previously pointed at
`https://services.demo.docusign.net/docusign-mcp-server/v1.0/mcp`, hardcoded in
five places. It is now set in one place and overridable:

```bash
DOCUSIGN_MCP_BASE_URL=https://services.demo.docusign.net/docusign-mcp-server/v1.0/mcp
```

That one variable restores the old behaviour if your account is still served
there.

## References

- [Build with the Docusign MCP Server](https://developers.docusign.com/platform/mcp-server/)
- [Using Claude with the Docusign MCP Connector](https://developers.docusign.com/platform/mcp-server/anthropic-claude/claude-code/)
- [Docusign MCP connector guide](https://www.docusign.com/blog/developers/claude-docusign-mcp-connector-guide)
- [Add a connector that isn't in the directory](https://claude.com/docs/connectors/custom/remote-mcp)
- [Authentication scopes](https://developers.docusign.com/platform/auth/reference/scopes/)
