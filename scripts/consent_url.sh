#!/usr/bin/env bash
set -euo pipefail

# Helper: print (and optionally open) a DocuSign JWT consent URL built from .env
# Usage: ./scripts/consent_url.sh [--open] [--redirect-uri URL]
# Defaults: redirect_uri=https://www.docusign.com, environment inferred from BASE_URI (demo => account-d)

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: .env file not found at $ENV_FILE" >&2
  exit 2
fi

# Simple function to read a value from .env (ignores comments and exports)
get_env() {
  local key="$1"
  awk -F'=' -v k="$key" '$1==k{ $1=""; sub(/^=/,""); print substr($0,2) }' "$ENV_FILE" | sed 's/^"//;s/"$//' | sed "s/^'//;s/'$//"
}

INTEGRATION_KEY=$(get_env INTEGRATION_KEY)
BASE_URI=$(get_env BASE_URI)

if [ -z "$INTEGRATION_KEY" ]; then
  echo "Error: INTEGRATION_KEY not found in .env" >&2
  exit 2
fi

# Determine account host. If BASE_URI contains 'demo' or 'docusign.net' and 'demo' => account-d
ACCOUNT_HOST="account.docusign.com"
if [ -n "$BASE_URI" ]; then
  if echo "$BASE_URI" | grep -qi "demo"; then
    ACCOUNT_HOST="account-d.docusign.com"
  fi
fi

# Defaults
REDIRECT_URI="https://www.docusign.com"
OPEN_BROWSER=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --open) OPEN_BROWSER=true; shift ;;
    --redirect-uri) REDIRECT_URI="$2"; shift 2 ;;
    --help) echo "Usage: $0 [--open] [--redirect-uri URL]"; exit 0 ;;
    *) echo "Unknown arg: $1"; echo "Usage: $0 [--open] [--redirect-uri URL]"; exit 2 ;;
  esac
done

# URLEncode redirect_uri using python for correctness
if command -v python3 >/dev/null 2>&1; then
  ENC_REDIRECT=$(python3 -c "import sys,urllib.parse as u; print(u.quote(sys.argv[1], safe=''))" "$REDIRECT_URI")
elif command -v python >/dev/null 2>&1; then
  ENC_REDIRECT=$(python -c "import sys,urllib.parse as u; print(u.quote(sys.argv[1], safe=''))" "$REDIRECT_URI")
else
  # Fallback: simple replace (not fully RFC-safe)
  ENC_REDIRECT=$(echo "$REDIRECT_URI" | sed -e 's/:/%3A/g' -e 's@/@%2F@g')
fi

CONSENT_URL="https://${ACCOUNT_HOST}/oauth/auth?response_type=code&scope=signature%20impersonation&client_id=${INTEGRATION_KEY}&redirect_uri=${ENC_REDIRECT}"

echo
echo "Consent URL (open this in a browser as the user you want to impersonate):"
echo
echo "$CONSENT_URL"
echo
if [ "$OPEN_BROWSER" = true ]; then
  if command -v open >/dev/null 2>&1; then
    open "$CONSENT_URL"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$CONSENT_URL"
  else
    echo "Cannot open browser automatically: no 'open' or 'xdg-open' found." >&2
    exit 0
  fi
fi

exit 0
