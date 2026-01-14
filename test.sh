# open "https://account-d.docusign.com/oauth/auth?response_type=code&scope=signature%20impersonation&client_id=0c91b7c5-3ae7-4ac8-bd0a-093e9538954b&redirect_uri=https://www.docusign.com"

export INTEGRATION_KEY=$(grep -E '^INTEGRATION_KEY=' .env | cut -d'=' -f2-)
open "https://account-d.docusign.com/oauth/auth?response_type=code&scope=signature%20impersonation&client_id=${INTEGRATION_KEY}&redirect_uri=0c91b7c5-3ae7-4ac8-bd0a-093e9538954b"


https://account-d.docusign.com/oauth/auth?
  response_type=code&
  scope=signature%20impersonation&
  client_id=yourintegrationkey&
  redirect_uri=https://www.docusign.com
