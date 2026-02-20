"""
Upstox v2 OAuth2 Authentication Module.

Automates the browser-based Upstox login using Selenium (headless Chrome)
so that GitHub Actions can authenticate without human interaction.

Flow:
  1. Open the Upstox authorization URL in headless Chrome.
  2. Fill mobile number → click Get OTP / Continue.
  3. Fill 6-digit PIN.
  4. Generate TOTP with pyotp and fill it.
  5. Submit → capture the redirect URL that contains ?code=...
  6. Exchange the code for an access token via POST.
  7. Return the access token for all subsequent API calls.
"""

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
    # Suppress Selenium DevTools logs
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    return driver


def _wait_for_element(driver, by, selector, timeout=15):
    """Wait until an element is visible and return it."""
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((by, selector))
    )


def _safe_send_keys(driver, by, selector, text, timeout=15):
    el = _wait_for_element(driver, by, selector, timeout)
    el.clear()
    el.send_keys(text)
    return el


def _get_auth_code_via_selenium() -> str:
    """
    Automate the Upstox login page and return the OAuth2 auth code.
    """
    driver = _build_chrome_driver()
    auth_code = None

    try:
        logger.info("Opening Upstox authorization URL …")
        driver.get(cfg.UPSTOX_AUTH_URL)
        time.sleep(2)

        # ── Step 1: Enter mobile number ──────────────────────────────────────
        logger.info("Entering mobile number …")
        try:
            mobile_field = _wait_for_element(driver, By.ID, "mobileNum")
        except TimeoutException:
            # Some Upstox versions use 'email' or a different selector
            mobile_field = _wait_for_element(driver, By.NAME, "mobile")
        mobile_field.clear()
        mobile_field.send_keys(cfg.UPSTOX_MOBILE)

        # Click the "Get OTP" / "Continue" button
        try:
            btn = driver.find_element(By.ID, "getOtp")
        except NoSuchElementException:
            btn = driver.find_element(By.XPATH, "//button[contains(text(),'Continue') or contains(text(),'Get OTP')]")
        btn.click()
        time.sleep(2)

        # ── Step 2: Enter 6-digit PIN ────────────────────────────────────────
        logger.info("Entering PIN …")
        try:
            pin_field = _wait_for_element(driver, By.ID, "pinCode")
        except TimeoutException:
            pin_field = _wait_for_element(driver, By.NAME, "pin")
        pin_field.clear()
        pin_field.send_keys(cfg.UPSTOX_PIN)
        time.sleep(1)

        # ── Step 3: Enter TOTP ────────────────────────────────────────────────
        logger.info("Generating and entering TOTP …")
        totp = pyotp.TOTP(cfg.UPSTOX_TOTP_SECRET)
        otp_code = totp.now()
        logger.info(f"Generated TOTP: {otp_code}")

        try:
            totp_field = _wait_for_element(driver, By.ID, "otpNum")
        except TimeoutException:
            totp_field = _wait_for_element(driver, By.NAME, "totp")
        totp_field.clear()
        totp_field.send_keys(otp_code)
        time.sleep(1)

        # Submit login
        try:
            submit_btn = driver.find_element(By.ID, "continueBtn")
        except NoSuchElementException:
            submit_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
        submit_btn.click()

        # ── Step 4: Wait for redirect and extract code ────────────────────────
        logger.info("Waiting for OAuth2 redirect …")
        # Upstox will redirect to our callback URL which Chrome cannot actually load
        # (it's 127.0.0.1), so we poll the current URL until it changes.
        for _ in range(20):
            time.sleep(1)
            current_url = driver.current_url
            match = re.search(r"[?&]code=([^&]+)", current_url)
            if match:
                auth_code = match.group(1)
                logger.info("Auth code captured successfully.")
                break

        if not auth_code:
            # Try reading from page source (some flows show the code on screen)
            source = driver.page_source
            match = re.search(r"code=([A-Za-z0-9_\-]+)", source)
            if match:
                auth_code = match.group(1)

        if not auth_code:
            logger.error("Could not extract auth code. Current URL: %s", driver.current_url)
            raise RuntimeError("Failed to capture Upstox auth code after login.")

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


def get_access_token() -> str:
    """
    Full authentication flow:
      Selenium login → auth code → access token.
    Returns the access token string.
    """
    logger.info("Starting Upstox authentication …")
    auth_code    = _get_auth_code_via_selenium()
    access_token = _exchange_code_for_token(auth_code)
    return access_token
