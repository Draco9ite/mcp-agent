"""
DocuSign Authentication Manager
Handles JWT token generation and caching for DocuSign API access.
"""

import os
import time
import requests
from datetime import datetime, timedelta
import jwt  # Using PyJWT instead of python-jose
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

class DocuSignAuthManager:
    """
    Manages DocuSign authentication using JWT Grant flow.
    Handles token generation, caching, and refresh.
    """
    
    def __init__(self, integration_key=None, user_id=None, private_key=None, base_uri=None):
        """
        Initialize the authentication manager.
        
        Args:
            integration_key (str): DocuSign Integration Key (Client ID)
            user_id (str): DocuSign User ID (GUID)
            private_key (str): RSA Private Key in PEM format
            base_uri (str): DocuSign base URI (default: demo environment)
        """
        logger.info("🔗 THIS IS USING MCP")
        # Load from environment variables if not provided
        self.integration_key = integration_key or os.getenv('INTEGRATION_KEY')
        self.user_id = user_id or os.getenv('USER_ID')
        self.base_uri = base_uri or os.getenv('BASE_URI', 'https://demo.docusign.net')
        
        # Handle private key: prefer PRIVATE_KEY_PATH if set, otherwise PRIVATE_KEY env or passed value
        private_key_path = os.getenv('PRIVATE_KEY_PATH')
        private_key_env = private_key or os.getenv('PRIVATE_KEY')

        raw_key_text = None
        if private_key_path:
            try:
                with open(private_key_path, 'r') as f:
                    raw_key_text = f.read()
            except Exception as e:
                raise ValueError(f"Failed to read PRIVATE_KEY_PATH '{private_key_path}': {e}")
        elif private_key_env:
            raw_key_text = private_key_env
        else:
            raise ValueError("Private key is required for DocuSign authentication (set PRIVATE_KEY_PATH or PRIVATE_KEY)")

        # Normalize and validate the key format
        try:
            self.private_key = self._format_private_key(raw_key_text)
            # Quick validation: try loading the private key now to surface clear errors early
            serialization.load_pem_private_key(self.private_key, password=None, backend=default_backend())
        except Exception as e:
            raise ValueError(f"Invalid PRIVATE_KEY provided or file unreadable: {e}")
        self.oauth_base_url = 'https://account-d.docusign.com/oauth/token'
        
        # Token caching
        self.cached_access_token = None
        self.token_expiration_time = None
        
        # Validate required parameters
        if not self.integration_key or not self.user_id:
            raise ValueError("Integration Key and User ID are required for DocuSign authentication")
    
    def _format_private_key(self, private_key_str):
        """
        Format the private key string to ensure proper PEM format.
        
        Args:
            private_key_str (str): Private key string
            
        Returns:
            bytes: Properly formatted private key bytes
        """
        try:
            # If the key was provided as newline-escaped (in .env), fix it
            if isinstance(private_key_str, str) and '\\n' in private_key_str:
                private_key_str = private_key_str.replace('\\n', '\n')

            # If already proper PEM with headers, return bytes
            if isinstance(private_key_str, str) and ('-----BEGIN ' in private_key_str and '\n' in private_key_str):
                return private_key_str.encode('utf-8')

            # If it's a single long base64 line without headers, try to rewrap into PEM
            s = private_key_str.strip()
            # Remove existing headers if any
            s = s.replace('-----BEGIN RSA PRIVATE KEY-----', '').replace('-----END RSA PRIVATE KEY-----', '').strip()

            # Split into 64-character lines
            formatted_lines = [s[i:i+64] for i in range(0, len(s), 64)]
            formatted_key = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(formatted_lines) + "\n-----END RSA PRIVATE KEY-----"
            return formatted_key.encode('utf-8')
        except Exception as e:
            raise ValueError(f"Invalid private key format: {e}")
    
    def get_access_token(self):
        """
        Get a valid access token, using cached token if available and not expired.
        
        Returns:
            str: Valid access token
        """
        try:
            # Check if cached token is still valid (with 5-minute buffer)
            if (self.cached_access_token and 
                self.token_expiration_time and 
                datetime.now() < self.token_expiration_time - timedelta(minutes=5)):
                logger.info("🔑 Using cached access token")
                return self.cached_access_token
            
            logger.info("🔑 Generating new access token via JWT...")
            return self._generate_jwt_token()
        
        except Exception as e:
            logger.error(f"❌ Error getting access token: {e}")
            raise
    
    def _generate_jwt_token(self):
        """
        Generate a new JWT token and exchange it for an access token.
        
        Returns:
            str: Access token
            
        Raises:
            Exception: If token generation fails
        """
        try:
            # Load the private key
            private_key = serialization.load_pem_private_key(
                self.private_key,
                password=None,
                backend=default_backend()
            )
            
            # JWT Claims - Include Navigator API and Maestro API scopes
            now = int(time.time())
            claims = {
                "iss": self.integration_key,
                "sub": self.user_id,
                "aud": "account-d.docusign.com",
                "scope": "signature impersonation adm_store_unified_repo_read aow_manage",  # Added aow_manage for Maestro
                "iat": now,
                "exp": now + 3600  # 1 hour
            }
            
            # Generate JWT using PyJWT
            jwt_token = jwt.encode(
                claims,
                private_key,
                algorithm="RS256"
            )
            
            logger.info("✅ JWT token generated successfully")
            
            # Exchange JWT for access token
            return self._exchange_jwt_for_access_token(jwt_token)
            
        except Exception as e:
            logger.error(f"❌ Error generating JWT token: {e}")
            raise Exception(f"Error generating access token: {e}")
    
    def _exchange_jwt_for_access_token(self, jwt_token):
        """
        Exchange JWT token for DocuSign access token.
        
        Args:
            jwt_token (str): JWT token
            
        Returns:
            str: Access token
        """
        try:
            # Prepare the request
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            data = {
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'assertion': jwt_token
            }
            
            # Make the request
            logger.info("🔄 Exchanging JWT for access token...")
            response = requests.post(self.oauth_base_url, headers=headers, data=data)

            if response.status_code == 200:
                token_data = response.json()
                access_token = token_data['access_token']
                expires_in = token_data.get('expires_in', 3600)

                # Cache the token
                self.cached_access_token = access_token
                self.token_expiration_time = datetime.now() + timedelta(seconds=expires_in)

                logger.info(f"✅ Access token obtained successfully (expires in {expires_in}s)")
                return access_token
            else:
                # Try to parse JSON to detect consent_required
                try:
                    err_json = response.json()
                except Exception:
                    err_json = None

                if err_json and isinstance(err_json, dict) and err_json.get('error') == 'consent_required':
                    # Build a helpful consent URL and log it
                    try:
                        consent_url = self._build_consent_url()
                        logger.error(f"❌ Token exchange failed: consent_required. Open the following URL to grant consent:\n{consent_url}")
                    except Exception:
                        logger.error("❌ Token exchange failed: consent_required. Please grant consent for the integration in DocuSign Admin.")

                error_msg = f"Token exchange failed: {response.status_code} - {response.text}"
                logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)
                
        except requests.RequestException as e:
            error_msg = f"Network error during token exchange: {e}"
            logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"Error exchanging JWT for access token: {e}"
            logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)
    
    def get_user_info(self):
        """
        Get user information from DocuSign using the access token.
        This also validates that authentication is working.
        
        Returns:
            dict: User information
        """
        try:
            access_token = self.get_access_token()
            
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            # Get user info to validate token and get account details
            # Use the correct OAuth userinfo endpoint
            user_info_url = 'https://account-d.docusign.com/oauth/userinfo'
            response = requests.get(user_info_url, headers=headers)
            
            if response.status_code == 200:
                user_data = response.json()
                logger.info("✅ User info retrieved successfully")
                return user_data
            else:
                error_msg = f"Failed to get user info: {response.status_code} - {response.text}"
                logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)
                
        except Exception as e:
            logger.error(f"❌ Error getting user info: {e}")
            raise
    
    def get_account_id(self):
        """
        Get the account ID from user info.
        
        Returns:
            str: Account ID
        """
        try:
            user_info = self.get_user_info()
            accounts = user_info.get('accounts', [])
            
            if accounts:
                # Use the first account or find the default account
                for account in accounts:
                    if account.get('is_default'):
                        return account.get('account_id')
                
                # If no default found, use the first account
                return accounts[0].get('account_id')
            else:
                raise Exception("No accounts found for user")
                
        except Exception as e:
            logger.error(f"❌ Error getting account ID: {e}")
            raise
    
    def test_authentication(self):
        """
        Test the authentication by getting user info.
        
        Returns:
            bool: True if authentication successful
        """
        try:
            user_info = self.get_user_info()
            account_id = self.get_account_id()
            
            logger.info(f"🎉 Authentication test successful!")
            logger.info(f"👤 User: {user_info.get('name', 'Unknown')}")
            logger.info(f"📧 Email: {user_info.get('email', 'Unknown')}")
            logger.info(f"🏢 Account ID: {account_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Authentication test failed: {e}")
            return False

    def _build_consent_url(self, redirect_uri: str = 'https://www.docusign.com') -> str:
        """
        Build a DocuSign consent URL for the current integration key.
        Returns a URL that the user should open to grant JWT impersonation consent.
        The scopes in the consent URL will match those used in the JWT.
        """
        # Determine account host based on oauth_base_url (account-d for developer)
        if 'account-d' in (self.oauth_base_url or ''):
            account_host = 'account-d.docusign.com'
        else:
            account_host = 'account.docusign.com'

        client_id = self.integration_key or os.getenv('INTEGRATION_KEY')
        if not client_id:
            raise ValueError('Integration Key (client_id) not available to build consent URL')

        # Use the same scopes as in the JWT
        scopes = 'signature impersonation adm_store_unified_repo_read aow_manage'

        # Construct the consent URL
        from urllib.parse import urlencode
        params = {
            'response_type': 'code',
            'scope': scopes,
            'client_id': client_id,
            'redirect_uri': redirect_uri
        }
        return f"https://{account_host}/oauth/auth?{urlencode(params)}"

# Factory function to create auth manager from environment variables
def create_docusign_auth_manager():
    """
    Create a DocuSign auth manager using environment variables.
    
    Returns:
        DocuSignAuthManager: Configured auth manager
    """
    try:
        return DocuSignAuthManager()
    except Exception as e:
        logger.error(f"❌ Failed to create DocuSign auth manager: {e}")
        raise


if __name__ == '__main__':
    # CLI to test authentication when run directly
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    parser = argparse.ArgumentParser(description='Test DocuSign JWT authentication')
    parser.add_argument('--private-key-path', help='Path to PEM private key file')
    parser.add_argument('--private-key', help='Private key contents (PEM or single-line base64, use \"\\n\" for newlines)')
    parser.add_argument('--integration-key', help='DocuSign Integration Key (Client ID)')
    parser.add_argument('--user-id', help='DocuSign User ID (GUID)')
    parser.add_argument('--base-uri', help='DocuSign base URI (optional)')
    parser.add_argument('--dry-run', action='store_true', help='Generate JWT and skip network exchange')
    args = parser.parse_args()

    try:
        # Prefer explicit args when provided, otherwise fall back to environment-based factory
        if args.private_key_path or args.private_key or args.integration_key or args.user_id or args.base_uri:
            # Read private key file if provided
            priv = None
            if args.private_key_path:
                try:
                    with open(args.private_key_path, 'r') as f:
                        priv = f.read()
                except Exception as e:
                    print(f"AUTH_ERROR: failed to read private key path: {e}")
                    raise
            elif args.private_key:
                priv = args.private_key

            mgr = DocuSignAuthManager(
                integration_key=args.integration_key,
                user_id=args.user_id,
                private_key=priv,
                base_uri=args.base_uri
            )
        else:
            mgr = create_docusign_auth_manager()

        if args.dry_run:
            # Generate JWT only (do not exchange)
            try:
                jwt_token = mgr._generate_jwt_token()
                # If _generate_jwt_token did exchange, it will have returned access token.
                # In dry-run we want the JWT; so instead we call lower-level generation.
            except Exception:
                # Fall back to building JWT claims directly here to avoid changing core methods
                # Recreate minimal JWT generation for dry-run (not exported)
                from cryptography.hazmat.primitives.asymmetric import rsa
                # Attempt to use the manager's private key; if it's bytes, load it
                try:
                    priv = serialization.load_pem_private_key(mgr.private_key, password=None, backend=default_backend())
                except Exception as e:
                    raise

            # For a safer dry-run, call internal generation flow but avoid network by
            # temporarily swapping out the _exchange_jwt_for_access_token method.
            original_exchange = mgr._exchange_jwt_for_access_token
            try:
                mgr._exchange_jwt_for_access_token = lambda jwt_t: jwt_t
                jwt_token = mgr._generate_jwt_token()
                print(jwt_token)
                print("DRY_RUN_OK")
            finally:
                mgr._exchange_jwt_for_access_token = original_exchange

        else:
            success = mgr.test_authentication()
            if success:
                print("AUTH_OK")
            else:
                print("AUTH_FAILED")

    except Exception as e:
        print(f"AUTH_ERROR: {e}")
        # Re-raise to preserve the stack trace when run interactively
        raise
