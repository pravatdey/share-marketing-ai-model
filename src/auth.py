"""
Upstox v2 OAuth2 Authentication Module.

Automates the browser-based Upstox login using Selenium (headless Chrome)
so that GitHub Actions can authenticate without human interaction.

Flow:
  1. Open the Upstox authorization URL in headless Chrome.
  2. Fill mobile number → click Get OTP / Continue.
  3. Fill 6-digit PIN.
  4. Generate TOTP with pyotp and fill it.  ← this is the automated OTP
  5. Submit → capture the redirect URL that contains ?code=...
  6. Exchange the code for an access token via POST.
  7. Return the access token for all subsequent API calls.

Note on OTP:
  The TOTP (6-digit code) is generated automatically by pyotp using the
  UPSTOX_TOTP_SECRET (the base32 secret from your Upstox authenticator setup).
  No manual OTP entry is needed.
"""

import os
import re
import time
import logging
import requests
import pyotp

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

import config.settings as cfg

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = "logs"


def _save_screenshot(driver, name: str):
    """Save a debug screenshot and page source to the logs directory."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    png_path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    html_path = os.path.join(SCREENSHOT_DIR, f"{name}.html")
    try:
        driver.save_screenshot(png_path)
        logger.info("Screenshot saved: %s", png_path)
    except Exception as exc:
        logger.warning("Could not save screenshot %s: %s", png_path, exc)
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        logger.info("Page source saved: %s", html_path)
    except Exception as exc:
        logger.warning("Could not save page source %s: %s", html_path, exc)


def _build_chrome_driver() -> webdriver.Chrome:
    """Return a headless Chrome WebDriver (works on GitHub Actions Ubuntu runner)."""
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--allow-insecure-localhost")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    return driver


def _wait_for_element(driver, by, selector, timeout=15):
    """Wait until an element is visible and return it."""
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((by, selector))
    )


def _try_selectors(driver, selectors, timeout=10):
    """
    Try multiple (By, selector) pairs and return the first found element.
    Raises TimeoutException if none match.
    """
    for by, selector in selectors:
        try:
            return _wait_for_element(driver, by, selector, timeout=timeout)
        except TimeoutException:
            continue
    raise TimeoutException(f"None of the selectors matched: {selectors}")


def _safe_send_keys(driver, by, selector, text, timeout=15):
    el = _wait_for_element(driver, by, selector, timeout)
    el.clear()
    el.send_keys(text)
    return el


def _get_auth_code_via_selenium() -> str:
    """
    Automate the Upstox login page and return the OAuth2 auth code.
    Screenshots are saved to logs/ at every step for debugging.
    """
    driver = _build_chrome_driver()
    auth_code = None

    try:
        logger.info("Opening Upstox authorization URL …")
        driver.get(cfg.UPSTOX_AUTH_URL)
        time.sleep(2)
        _save_screenshot(driver, "01_login_page")

        # ── Step 1: Enter mobile number ──────────────────────────────────────
        logger.info("Entering mobile number …")
        mobile_field = _try_selectors(driver, [
            (By.ID,    "mobileNum"),
            (By.NAME,  "mobile"),
            (By.XPATH, "//input[@type='tel']"),
            (By.XPATH, "//input[contains(@placeholder,'mobile') or contains(@placeholder,'Mobile')]"),
        ])
        mobile_field.clear()
        mobile_field.send_keys(cfg.UPSTOX_MOBILE)
        _save_screenshot(driver, "02_mobile_entered")

        # Click "Get OTP" / "Continue"
        try:
            btn = driver.find_element(By.ID, "getOtp")
        except NoSuchElementException:
            btn = driver.find_element(
                By.XPATH,
                "//button[contains(text(),'Continue') or contains(text(),'Get OTP') or contains(text(),'get otp')]",
            )
        btn.click()
        time.sleep(3)
        _save_screenshot(driver, "03_after_get_otp_click")

        # ── Step 2: Enter 6-digit PIN ────────────────────────────────────────
        logger.info("Entering PIN …")
        pin_field = _try_selectors(driver, [
            (By.ID,    "pinCode"),
            (By.NAME,  "pin"),
            (By.XPATH, "//input[@type='password' and @maxlength='6']"),
            (By.XPATH, "//input[contains(@placeholder,'PIN') or contains(@placeholder,'pin')]"),
        ])
        pin_field.clear()
        pin_field.send_keys(cfg.UPSTOX_PIN)
        time.sleep(1)
        _save_screenshot(driver, "04_pin_entered")

        # ── Step 3: Enter TOTP (auto-generated — no manual OTP needed) ────────
        # pyotp generates the same 6-digit code your authenticator app shows.
        # Make sure UPSTOX_TOTP_SECRET in GitHub Secrets is the base32 secret
        # from when you set up 2FA on Upstox (not the 6-digit code itself).
        logger.info("Generating and entering TOTP …")
        totp = pyotp.TOTP(cfg.UPSTOX_TOTP_SECRET)
        otp_code = totp.now()
        logger.info("Generated TOTP: %s", otp_code)
        _save_screenshot(driver, "05_before_totp_entry")

        totp_field = _try_selectors(driver, [
            (By.ID,          "otpNum"),
            (By.NAME,        "totp"),
            (By.CSS_SELECTOR, "input[autocomplete='one-time-code']"),
            (By.XPATH,       "//input[@type='number' and @maxlength='6']"),
            (By.XPATH,       "//input[@type='tel'    and @maxlength='6']"),
            (By.XPATH,       "//input[@type='text'   and @maxlength='6']"),
            (By.XPATH,       "//input[contains(@placeholder,'TOTP') or contains(@placeholder,'totp')]"),
            (By.XPATH,       "//input[contains(@placeholder,'OTP')  or contains(@placeholder,'otp')]"),
            (By.XPATH,       "//input[contains(@id,'otp') or contains(@id,'totp') or contains(@name,'otp')]"),
        ])
        totp_field.clear()
        totp_field.send_keys(otp_code)
        time.sleep(1)
        _save_screenshot(driver, "06_totp_entered")

        # Submit login
        try:
            submit_btn = driver.find_element(By.ID, "continueBtn")
        except NoSuchElementException:
            submit_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
        submit_btn.click()
        _save_screenshot(driver, "07_after_submit")

        # ── Step 4: Wait for redirect and extract code ────────────────────────
        logger.info("Waiting for OAuth2 redirect …")
        for _ in range(20):
            time.sleep(1)
            current_url = driver.current_url
            match = re.search(r"[?&]code=([^&]+)", current_url)
            if match:
                auth_code = match.group(1)
                logger.info("Auth code captured successfully.")
                break

        if not auth_code:
            source = driver.page_source
            match = re.search(r"code=([A-Za-z0-9_\-]+)", source)
            if match:
                auth_code = match.group(1)

        if not auth_code:
            _save_screenshot(driver, "08_redirect_failed")
            logger.error("Could not extract auth code. Current URL: %s", driver.current_url)
            raise RuntimeError("Failed to capture Upstox auth code after login.")

    except Exception:
        _save_screenshot(driver, "error_state")
        raise
    finally:
        driver.quit()

    return auth_code


def _exchange_code_for_token(auth_code: str) -> str:
    """Exchange the OAuth2 auth code for an access token."""
    url = f"{cfg.UPSTOX_BASE_URL}/login/authorization/token"
    payload = {
        "code":          auth_code,
        "client_id":     cfg.UPSTOX_API_KEY,
        "client_secret": cfg.UPSTOX_API_SECRET,
        "redirect_uri":  cfg.UPSTOX_REDIRECT_URI,
        "grant_type":    "authorization_code",
    }
    headers = {
        "accept":       "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Api-Version":  "2.0",
    }
    resp = requests.post(url, data=payload, headers=headers, timeout=30)

    if resp.status_code != 200:
        logger.error("Token exchange failed: %s – %s", resp.status_code, resp.text)
        raise RuntimeError(f"Token exchange failed: {resp.status_code} – {resp.text}")

    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {resp.json()}")

    logger.info("Access token obtained successfully.")
    return token


def _validate_token(token: str) -> bool:
    """Return True if the token is still accepted by the Upstox API."""
    try:
        resp = requests.get(
            f"{cfg.UPSTOX_BASE_URL}/user/profile",
            headers={
                "accept":        "application/json",
                "Authorization": f"Bearer {token}",
                "Api-Version":   "2.0",
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def get_access_token() -> str:
    """
    1. If UPSTOX_ACCESS_TOKEN is set in env/secrets and still valid → use it
       directly, skipping Selenium + SMS OTP entirely.
    2. Otherwise → fall back to full Selenium browser login.

    To use option 1:
      - Go to https://api.upstox.com/v2/login/authorization/dialog manually
        (or use any Upstox login tool) to get today's access token.
      - Paste it into GitHub Secrets as UPSTOX_ACCESS_TOKEN before 9:10 AM IST.
      - The bot validates it with a quick API call and reuses it all day.
      - Upstox tokens expire at midnight IST, so update the secret each morning.
    """
    stored_token = cfg.UPSTOX_ACCESS_TOKEN
    if stored_token:
        logger.info("Found UPSTOX_ACCESS_TOKEN secret — validating …")
        if _validate_token(stored_token):
            logger.info("Stored token is valid. Skipping Selenium login.")
            return stored_token
        logger.warning("Stored token is expired or invalid. Falling back to Selenium login.")

    logger.info("Starting Upstox authentication via Selenium …")
    auth_code    = _get_auth_code_via_selenium()
    access_token = _exchange_code_for_token(auth_code)
    return access_token
