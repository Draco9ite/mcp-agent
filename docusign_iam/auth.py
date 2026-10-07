#!/usr/bin/env python3
"""
Unified DocuSign authentication for IAM and CLM.

One access token, two product families:

* **IAM** (Agreement Manager, Maestro, eSignature) is reached on
  ``api-d.docusign.com`` / ``api.docusign.com`` and uses the account ID from
  the OAuth userinfo response.
* **CLM** (formerly SpringCM) lives on its own host and uses its own account
  ID, both of which are discovered from the CLM auth service using the same
  bearer token.

Supported grants:

* **JWT grant** (service integration) — needs ``INTEGRATION_KEY``, ``USER_ID``
  and ``PRIVATE_KEY``. Preferred for unattended runs.
* **Authorization code grant** — needs ``INTEGRATION_KEY`` and ``SECRET_KEY``.
  Reuses the token cache written by ``docusign_mcp_client.DocuSignMCPClient``
  so an operator consent already granted for the MCP server also serves here.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Scopes ──────────────────────────────────────────────────────────────
# Agreement Manager (formerly Navigator) requires its own store scope; Maestro
# requires aow_manage; CLM requires the spring_* scopes.
SCOPE_ESIGN = ["signature"]
SCOPE_AGREEMENT_MANAGER = ["adm_store_unified_repo_read"]
SCOPE_MAESTRO = ["aow_manage"]
SCOPE_CLM = ["spring_read", "spring_write"]

#: Everything this integration touches, for an authorization-code consent.
SCOPE_IAM_CLM = (
    SCOPE_ESIGN + ["extended"] + SCOPE_AGREEMENT_MANAGER + SCOPE_MAESTRO + SCOPE_CLM
)

#: JWT grant needs `impersonation` instead of `extended`.
SCOPE_IAM_CLM_JWT = (
    SCOPE_ESIGN
    + ["impersonation"]
    + SCOPE_AGREEMENT_MANAGER
    + SCOPE_MAESTRO
    + SCOPE_CLM
)


class DocuSignAuthError(RuntimeError):
    """Raised when a token cannot be obtained or refreshed."""


class _Environment:
    """Host names for one DocuSign environment (demo or production)."""

    def __init__(self, demo: bool):
        self.demo = demo
        if demo:
            self.oauth_host = "account-d.docusign.com"
            self.iam_base = "https://api-d.docusign.com"
            self.esign_base = "https://demo.docusign.net"
            self.clm_auth_base = "https://authuat.springcm.com"
        else:
            self.oauth_host = "account.docusign.com"
            self.iam_base = "https://api.docusign.com"
            self.esign_base = "https://www.docusign.net"
            self.clm_auth_base = "https://auth.springcm.com"

    @property
    def auth_url(self) -> str:
        return f"https://{self.oauth_host}/oauth/auth"

    @property
    def token_url(self) -> str:
        return f"https://{self.oauth_host}/oauth/token"

    @property
    def userinfo_url(self) -> str:
        return f"https://{self.oauth_host}/oauth/userinfo"


class DocuSignAuth:
    """Issues and caches a DocuSign access token good for IAM and CLM.

    Args:
        scopes: OAuth scopes to request. Defaults to the full IAM + CLM set
            for the grant in use.
        token_file: Where to cache tokens. Defaults to ``.docusign_iam_tokens.json``
            next to the repository root. Pass ``.mcp_tokens.json`` to share the
            consent already stored by the MCP client.
        demo: Target the demo environment. Defaults to ``True`` unless
            ``BASE_URI`` points at production.
    """

    def __init__(
        self,
        scopes: Optional[List[str]] = None,
        token_file: Optional[str] = None,
        demo: Optional[bool] = None,
    ):
        self.integration_key = os.getenv("INTEGRATION_KEY")
        self.user_id = os.getenv("USER_ID")
        self.client_secret = os.getenv("SECRET_KEY")
        self.private_key = _load_private_key()
        self.account_id_override = os.getenv("ACCOUNT_ID")

        if demo is None:
            demo = "demo" in os.getenv("BASE_URI", "https://demo.docusign.net").lower()
        self.env = _Environment(demo=demo)

        self.use_jwt = bool(self.private_key and self.user_id)
        default_scopes = SCOPE_IAM_CLM_JWT if self.use_jwt else SCOPE_IAM_CLM
        self.scopes = scopes or _scopes_from_env(default_scopes)

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._token_file = token_file or os.path.join(
            repo_root, ".docusign_iam_tokens.json"
        )

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_in: Optional[int] = None
        self.obtained_at: Optional[int] = None

        self._userinfo: Optional[Dict[str, Any]] = None
        self._clm_account: Optional[Dict[str, Any]] = None

        self._load_tokens()

    # ── Token acquisition ───────────────────────────────────────────────

    def get_access_token(self) -> str:
        """Return a valid access token, minting or refreshing as needed."""
        if not self._is_expired():
            return self.access_token

        if self.use_jwt:
            self._jwt_grant()
        elif self.refresh_token:
            try:
                self._refresh()
            except Exception as exc:
                raise DocuSignAuthError(
                    "Refresh token rejected; re-run the consent flow "
                    f"({self.get_consent_url()}): {exc}"
                ) from exc
        else:
            raise DocuSignAuthError(
                "No usable credentials. Either set PRIVATE_KEY + USER_ID for the "
                "JWT grant, or complete the consent flow at "
                f"{self.get_consent_url()}"
            )
        return self.access_token

    def _jwt_grant(self) -> None:
        """Exchange a signed JWT assertion for an access token."""
        import jwt  # PyJWT; imported lazily so auth-code-only installs work

        if not (self.integration_key and self.user_id and self.private_key):
            raise DocuSignAuthError(
                "JWT grant needs INTEGRATION_KEY, USER_ID and PRIVATE_KEY"
            )

        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": self.integration_key,
                "sub": self.user_id,
                "aud": self.env.oauth_host,
                "iat": now,
                "exp": now + int(os.getenv("JWT_TOKEN_EXPIRY", 3600)),
                "scope": " ".join(self.scopes),
            },
            self.private_key,
            algorithm="RS256",
        )

        response = requests.post(
            self.env.token_url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=30,
        )
        if response.status_code == 400 and "consent_required" in response.text:
            raise DocuSignAuthError(
                "DocuSign returned consent_required. Grant consent once at "
                f"{self.get_consent_url()} then retry."
            )
        response.raise_for_status()
        self._apply(response.json())

    def _refresh(self) -> None:
        response = requests.post(
            self.env.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.integration_key,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        response.raise_for_status()
        self._apply(response.json())

    def exchange_code(self, auth_code: str, redirect_uri: str) -> Dict[str, Any]:
        """Complete the authorization code grant and cache the tokens."""
        response = requests.post(
            self.env.token_url,
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": redirect_uri,
                "client_id": self.integration_key,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        response.raise_for_status()
        tokens = response.json()
        self._apply(tokens)
        return tokens

    def get_consent_url(self, redirect_uri: Optional[str] = None) -> str:
        """URL an admin visits once to grant this integration its scopes."""
        params = {
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "client_id": self.integration_key or "",
            "redirect_uri": redirect_uri or _default_redirect_uri(),
        }
        return f"{self.env.auth_url}?{urlencode(params)}"

    # ── Account discovery ───────────────────────────────────────────────

    def userinfo(self) -> Dict[str, Any]:
        """Cached ``/oauth/userinfo`` response for the authenticated user."""
        if self._userinfo is None:
            response = requests.get(
                self.env.userinfo_url,
                headers={"Authorization": f"Bearer {self.get_access_token()}"},
                timeout=30,
            )
            response.raise_for_status()
            self._userinfo = response.json()
        return self._userinfo

    @property
    def account_id(self) -> str:
        """DocuSign account GUID used by every IAM endpoint."""
        if self.account_id_override:
            return self.account_id_override
        accounts = self.userinfo().get("accounts", [])
        if not accounts:
            raise DocuSignAuthError("userinfo returned no accounts")
        default = next((a for a in accounts if a.get("is_default")), accounts[0])
        return default["account_id"]

    @property
    def esign_base_uri(self) -> str:
        """Account-specific eSignature base URI, from userinfo when available."""
        if self.account_id_override:
            return os.getenv("BASE_URI", self.env.esign_base)
        for account in self.userinfo().get("accounts", []):
            if account.get("account_id") == self.account_id:
                return account.get("base_uri", self.env.esign_base)
        return self.env.esign_base

    def clm_account(self) -> Dict[str, Any]:
        """Discover the CLM account ID and API host for this DocuSign account.

        CLM is a separate service with its own account identifier and regional
        host, so neither can be assumed from the DocuSign account ID. Both are
        resolved once against the CLM auth service and then cached.
        """
        if self._clm_account is not None:
            return self._clm_account

        explicit_id = os.getenv("CLM_ACCOUNT_ID")
        explicit_base = os.getenv("CLM_API_BASE_URL")
        if explicit_id and explicit_base:
            self._clm_account = {
                "clm_account_id": explicit_id,
                "api_base_url": explicit_base.rstrip("/"),
                "source": "environment",
            }
            return self._clm_account

        url = f"{self.env.clm_auth_base}/api/v2/{self.account_id}/account"
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {self.get_access_token()}"},
            timeout=30,
        )
        if response.status_code == 403:
            raise DocuSignAuthError(
                "CLM account discovery was forbidden. Confirm the account is "
                "CLM-enabled and the token carries the spring_read/spring_write "
                "scopes."
            )
        response.raise_for_status()
        payload = response.json()

        clm_account_id = (
            payload.get("ClmAccountId")
            or payload.get("AccountId")
            or payload.get("clmAccountId")
            or explicit_id
        )
        api_base_url = (
            payload.get("ApiBaseUrl")
            or payload.get("apiBaseUrl")
            or payload.get("BaseUrl")
            or explicit_base
        )
        if not (clm_account_id and api_base_url):
            raise DocuSignAuthError(
                "CLM discovery response was missing the account ID or API base "
                f"URL; set CLM_ACCOUNT_ID and CLM_API_BASE_URL explicitly. Got: {payload}"
            )

        self._clm_account = {
            "clm_account_id": str(clm_account_id),
            "api_base_url": str(api_base_url).rstrip("/"),
            "source": "discovery",
            "raw": payload,
        }
        return self._clm_account

    # ── Request helpers ─────────────────────────────────────────────────

    def auth_header(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.get_access_token()}"}

    def json_headers(self) -> Dict[str, str]:
        headers = self.auth_header()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        return headers

    # ── Token cache ─────────────────────────────────────────────────────

    def _apply(self, tokens: Dict[str, Any]) -> None:
        self.access_token = tokens.get("access_token")
        # A JWT-grant response carries no refresh token; keep the existing one.
        self.refresh_token = tokens.get("refresh_token") or self.refresh_token
        self.expires_in = tokens.get("expires_in")
        self.obtained_at = int(time.time())
        # A new token may belong to a different user, so drop derived state.
        self._userinfo = None
        self._clm_account = None
        self._save_tokens()

    def _is_expired(self, threshold_seconds: int = 60) -> bool:
        if not (self.access_token and self.expires_in and self.obtained_at):
            return True
        return time.time() >= (self.obtained_at + self.expires_in - threshold_seconds)

    def _save_tokens(self) -> None:
        try:
            with open(self._token_file, "w") as handle:
                json.dump(
                    {
                        "access_token": self.access_token,
                        "refresh_token": self.refresh_token,
                        "expires_in": self.expires_in,
                        "obtained_at": self.obtained_at,
                        "scope": " ".join(self.scopes),
                    },
                    handle,
                )
            os.chmod(self._token_file, 0o600)
        except OSError as exc:
            logger.warning("Could not persist DocuSign tokens: %s", exc)

    def _load_tokens(self) -> bool:
        try:
            if not os.path.exists(self._token_file):
                return False
            with open(self._token_file) as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("Could not read DocuSign token cache: %s", exc)
            return False
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")
        self.expires_in = data.get("expires_in")
        self.obtained_at = data.get("obtained_at")
        return True

    def clear_tokens(self) -> None:
        try:
            os.remove(self._token_file)
        except OSError:
            pass
        self.access_token = None
        self.refresh_token = None
        self.expires_in = None
        self.obtained_at = None
        self._userinfo = None
        self._clm_account = None


# ── Module helpers ──────────────────────────────────────────────────────


def _load_private_key() -> Optional[str]:
    """Read the RSA private key from PRIVATE_KEY or PRIVATE_KEY_PATH.

    ``PRIVATE_KEY`` may hold the PEM directly or with literal ``\\n`` escapes,
    which is how multi-line keys survive most secret stores.
    """
    key = os.getenv("PRIVATE_KEY")
    if key:
        return key.replace("\\n", "\n").strip()
    path = os.getenv("PRIVATE_KEY_PATH")
    if path and os.path.exists(path):
        with open(path) as handle:
            return handle.read().strip()
    return None


def _scopes_from_env(default: List[str]) -> List[str]:
    configured = os.getenv("DOCUSIGN_OAUTH_SCOPES")
    return configured.split() if configured else list(default)


def _default_redirect_uri() -> str:
    explicit = os.getenv("DOCUSIGN_REDIRECT_URI")
    if explicit:
        return explicit
    app_url = os.getenv("APP_URL")
    if app_url:
        return f"{app_url.rstrip('/')}/oauth/callback"
    hostname = os.getenv("WEBSITE_HOSTNAME")
    if hostname:
        return f"https://{hostname}/oauth/callback"
    return f"http://localhost:{os.getenv('PORT', '8000')}/oauth/callback"
