import os
import time
import json
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("INTEGRATION_KEY")
CLIENT_SECRET = os.getenv("SECRET_KEY")

# Build redirect URI from APP_URL or WEBSITE_HOSTNAME (Azure App Service) 
def _build_redirect_uri():
    explicit = os.getenv("DOCUSIGN_REDIRECT_URI")
    if explicit:
        return explicit
    app_url = os.getenv("APP_URL")
    if app_url:
        return f"{app_url.rstrip('/')}/oauth/callback"
    hostname = os.getenv("WEBSITE_HOSTNAME")
    if hostname:
        return f"https://{hostname}/oauth/callback"
    port = os.getenv("PORT", "8000")
    return f"http://localhost:{port}/oauth/callback"

REDIRECT_URI = _build_redirect_uri()
AUTH_URL = "https://account-d.docusign.com/oauth/auth"
TOKEN_URL = "https://account-d.docusign.com/oauth/token"
MCP_SERVER_URL = "https://services.demo.docusign.net/docusign-mcp-server/v1.0/mcp"
# Allow overriding the MCP base URL via environment variable for accounts with custom MCP hosts
MCP_SERVER_URL_OVERRIDE = os.getenv('DOCUSIGN_MCP_BASE_URL')
SCOPES = os.getenv('DOCUSIGN_OAUTH_SCOPES', "signature extended aow_manage account_product_read")


class DocuSignMCPClient:
    def __init__(self):
        self.client_id = CLIENT_ID
        self.client_secret = CLIENT_SECRET
        self.redirect_uri = REDIRECT_URI
        self.auth_url = AUTH_URL
        self.token_url = TOKEN_URL
        self.scopes = SCOPES
        # Prefer explicit environment override for MCP base URL if provided
        if MCP_SERVER_URL_OVERRIDE:
            # Use the URL exactly as provided (MCP endpoint should be complete)
            self.mcp_server_url = MCP_SERVER_URL_OVERRIDE
        else:
            self.mcp_server_url = MCP_SERVER_URL

        self.access_token = None
        self.refresh_token = None
        self.expires_in = None
        self.obtained_at = None  # epoch seconds when token was obtained

        self._token_file = os.path.join(os.path.dirname(__file__), '.mcp_tokens.json')
        # Try to load existing tokens from disk
        self.load_tokens()

    def get_authorization_url(self):
        params = {
            "response_type": "code",
            "scope": self.scopes,
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri
        }
        from urllib.parse import urlencode
        return f"{self.auth_url}?{urlencode(params)}"

    def fetch_token(self, auth_code):
        data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        response = requests.post(self.token_url, data=data)
        response.raise_for_status()
        tokens = response.json()
        self._apply_token_response(tokens)
        self.save_tokens(tokens)
        return tokens

    def refresh_access_token(self):
        if not self.refresh_token:
            raise Exception("No refresh token available.")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        response = requests.post(self.token_url, data=data)
        response.raise_for_status()
        tokens = response.json()
        self._apply_token_response(tokens)
        self.save_tokens(tokens)
        return tokens

    def _apply_token_response(self, tokens: dict):
        self.access_token = tokens.get('access_token')
        self.refresh_token = tokens.get('refresh_token')
        self.expires_in = tokens.get('expires_in')
        # record when token was obtained
        self.obtained_at = int(time.time())

    def save_tokens(self, tokens: dict):
        """Save tokens to a local JSON file for reuse across runs."""
        try:
            import json
            data = {
                'access_token': tokens.get('access_token'),
                'refresh_token': tokens.get('refresh_token'),
                'expires_in': tokens.get('expires_in'),
                'scope': tokens.get('scope'),
                'obtained_at': int(time.time())
            }
            with open(self._token_file, 'w') as f:
                json.dump(data, f)
        except Exception:
            # Don't crash the flow if persisting fails; caller can log
            pass

    def load_tokens(self):
        """Load tokens from disk if available and populate fields."""
        try:
            import json
            if os.path.exists(self._token_file):
                with open(self._token_file, 'r') as f:
                    data = json.load(f)
                    self.access_token = data.get('access_token')
                    self.refresh_token = data.get('refresh_token')
                    self.expires_in = data.get('expires_in')
                    self.obtained_at = data.get('obtained_at')
                    return True
        except Exception:
            return False
        return False

    def clear_tokens(self):
        """Remove stored tokens and clear memory."""
        try:
            if os.path.exists(self._token_file):
                os.remove(self._token_file)
        except Exception:
            pass
        self.access_token = None
        self.refresh_token = None
        self.expires_in = None
        self.obtained_at = None

    def _is_token_expired(self, threshold_seconds: int = 60) -> bool:
        """Return True if token is expired or will expire within threshold_seconds."""
        if not self.access_token or not self.expires_in or not self.obtained_at:
            return True
        # If current time is greater than obtained_at + expires_in - threshold -> refresh
        expiry_time = int(self.obtained_at) + int(self.expires_in)
        if time.time() >= (expiry_time - threshold_seconds):
            return True
        return False

    def call_mcp_server(self, endpoint: str = "", method: str = "GET", payload: dict | None = None):
        """Call the MCP server with automatic token refresh if needed.
        DEPRECATED: Use call_mcp_tool() for JSON-RPC calls instead."""
        # Auto-refresh if token is near expiry
        try:
            if self._is_token_expired():
                # Attempt refresh if we have a refresh token
                if self.refresh_token:
                    try:
                        self.refresh_access_token()
                    except Exception:
                        # clear tokens if refresh failed to force re-auth
                        self.clear_tokens()
                        raise
                else:
                    raise Exception("No valid access token available. Please perform OAuth flow.")

            if not self.access_token:
                raise Exception("No access token. Authenticate first.")

            url = self.mcp_server_url.rstrip('/') + '/' + endpoint.lstrip('/') if endpoint else self.mcp_server_url
            headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

            if method.upper() == "GET":
                response = requests.get(url, headers=headers)
            elif method.upper() == "POST":
                response = requests.post(url, headers=headers, json=payload)
            else:
                raise ValueError("Unsupported method")

            # If unauthorized/forbidden, try a single refresh then retry once
            if response.status_code in (401, 403):
                # try refreshing
                if self.refresh_token:
                    self.refresh_access_token()
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    if method.upper() == "GET":
                        response = requests.get(url, headers=headers)
                    else:
                        response = requests.post(url, headers=headers, json=payload)

            response.raise_for_status()
            # Try to return JSON, but fall back to raw text
            try:
                return response.json()
            except ValueError:
                return {'text': response.text, 'status_code': response.status_code}

        except Exception:
            # Re-raise for caller to handle/log
            raise

    def call_mcp_tool(self, tool_name: str, params: dict) -> dict:
        """Call an MCP tool using JSON-RPC 2.0 protocol over HTTP with SSE.
        
        Args:
            tool_name: Name of the MCP tool (e.g., 'getAllAgreements', 'triggerWorkflow')
            params: Dictionary of parameters for the tool
            
        Returns:
            Dictionary with the tool's response
        """
        try:
            if self._is_token_expired():
                if self.refresh_token:
                    try:
                        self.refresh_access_token()
                    except Exception:
                        self.clear_tokens()
                        raise
                else:
                    raise Exception("No valid access token available. Please perform OAuth flow.")

            if not self.access_token:
                raise Exception("No access token. Authenticate first.")

            # Prepare JSON-RPC 2.0 request
            payload = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {
                        "params": params
                    }
                },
                "id": 1
            }
            
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"
            }
            
            response = requests.post(self.mcp_server_url, json=payload, headers=headers)
            
            # If unauthorized/forbidden, try refresh once
            if response.status_code in (401, 403):
                if self.refresh_token:
                    self.refresh_access_token()
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    response = requests.post(self.mcp_server_url, json=payload, headers=headers)
            
            response.raise_for_status()
            
            # Parse SSE response format
            response_text = response.text
            if response_text.startswith("event: message\ndata: "):
                json_str = response_text.replace("event: message\ndata: ", "").strip()
                result = json.loads(json_str)
                
                if "result" in result:
                    content = result["result"].get("content", [])
                    if content and len(content) > 0:
                        text_content = content[0].get("text", "")
                        return json.loads(text_content) if text_content else {}
            
            return {}
            
        except Exception:
            raise

    def get_server_info(self):
        """Get server root info / tool listing from MCP server."""
        return self.call_mcp_server('', method='GET')


# Example usage (manual testing):
# client = DocuSignMCPClient()
# print("Go to this URL and authorize:", client.get_authorization_url())
# After user authorizes, get the code from the redirect and call:
# tokens = client.fetch_token(auth_code)
# result = client.call_mcp_server('tools/docusign/search', method='POST', payload={'cutoff_date':'2025-11-20','request_id':'test1'})
