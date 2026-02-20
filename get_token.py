"""
Daily Upstox Token Updater
==========================
Run this script each morning BEFORE 9:10 AM IST on trading days.

What it does:
  1. Opens the Upstox login page in your browser
  2. You login normally (mobile OTP → PIN → TOTP)
  3. You paste the auth code from the redirect URL
  4. Script fetches the access token automatically
  5. Script updates UPSTOX_ACCESS_TOKEN in GitHub Secrets automatically

Requirements (run once):
  pip install requests PyNaCl

Usage:
  python get_token.py
"""

import sys
import base64
import webbrowser
import requests

# ── CONFIG — fill these in once ──────────────────────────────────────────────
UPSTOX_API_KEY    = ""   # your Upstox API Key (from Developer Portal)
UPSTOX_API_SECRET = ""   # your Upstox API Secret
REDIRECT_URI      = "https://127.0.0.1/upstox-callback"

GITHUB_TOKEN      = ""   # GitHub Personal Access Token (needs repo scope)
GITHUB_REPO_OWNER = ""   # e.g. "pravatdey"
GITHUB_REPO_NAME  = ""   # e.g. "share-marketing-ai-model"
# ─────────────────────────────────────────────────────────────────────────────


def validate_config():
    missing = [k for k, v in {
        "UPSTOX_API_KEY":    UPSTOX_API_KEY,
        "UPSTOX_API_SECRET": UPSTOX_API_SECRET,
        "GITHUB_TOKEN":      GITHUB_TOKEN,
        "GITHUB_REPO_OWNER": GITHUB_REPO_OWNER,
        "GITHUB_REPO_NAME":  GITHUB_REPO_NAME,
    }.items() if not v]
    if missing:
        print(f"ERROR: Fill in these values at the top of get_token.py: {missing}")
        sys.exit(1)


def get_access_token(auth_code: str) -> str:
    resp = requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "code":          auth_code,
            "client_id":     UPSTOX_API_KEY,
            "client_secret": UPSTOX_API_SECRET,
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        },
    )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        print("ERROR: Could not get access token:", data)
        sys.exit(1)
    return token


def update_github_secret(secret_name: str, secret_value: str):
    """Encrypt and upload secret to GitHub using the repo's public key."""
    try:
        from nacl import encoding, public
    except ImportError:
        print("ERROR: Run this first:  pip install PyNaCl")
        sys.exit(1)

    base_url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
    headers  = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Get repo public key for encryption
    key_resp = requests.get(f"{base_url}/actions/secrets/public-key", headers=headers)
    if key_resp.status_code != 200:
        print("ERROR: Could not fetch GitHub public key:", key_resp.text)
        sys.exit(1)

    key_data   = key_resp.json()
    public_key = public.PublicKey(key_data["key"].encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key)
    encrypted  = base64.b64encode(sealed_box.encrypt(secret_value.encode("utf-8"))).decode("utf-8")

    # Upload encrypted secret
    put_resp = requests.put(
        f"{base_url}/actions/secrets/{secret_name}",
        headers=headers,
        json={"encrypted_value": encrypted, "key_id": key_data["key_id"]},
    )
    if put_resp.status_code in (201, 204):
        print(f"GitHub Secret '{secret_name}' updated successfully.")
    else:
        print(f"ERROR: Could not update secret: {put_resp.status_code} {put_resp.text}")
        sys.exit(1)


def main():
    validate_config()

    # Step 1: Open browser for login
    auth_url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={UPSTOX_API_KEY}"
        f"&redirect_uri={REDIRECT_URI}"
    )
    print("\n=== Upstox Daily Token Updater ===")
    print("\nStep 1: Opening Upstox login in your browser...")
    print("        Login normally: mobile OTP → PIN → TOTP from authenticator app")
    webbrowser.open(auth_url)

    # Step 2: Get auth code from user
    print("\nStep 2: After login, your browser shows an error page (that's normal).")
    print("        Look at the URL bar. It looks like:")
    print("        https://127.0.0.1/upstox-callback?code=XXXXXXXXXXXXXXXX")
    print("        Copy everything AFTER 'code=' from the URL.\n")
    auth_code = input("Paste the code here: ").strip()
    if not auth_code:
        print("ERROR: No code entered.")
        sys.exit(1)

    # Step 3: Exchange for access token
    print("\nStep 3: Fetching access token...")
    token = get_access_token(auth_code)
    print("        Access token obtained.")

    # Step 4: Update GitHub Secret
    print("\nStep 4: Updating UPSTOX_ACCESS_TOKEN in GitHub Secrets...")
    update_github_secret("UPSTOX_ACCESS_TOKEN", token)

    print("\nDone! The bot will use this token today. Run this script again tomorrow morning.")


if __name__ == "__main__":
    main()
