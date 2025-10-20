"""Selenium helper for capturing Yandex Music OAuth tokens."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver import DesiredCapabilities
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


logger = logging.getLogger(__name__)


@dataclass
class OAuthToken:
    access_token: str
    expires_in: Optional[int] = None


class YandexOAuthError(Exception):
    """Base class for Yandex OAuth errors."""


class YandexOAuthAuthenticationError(YandexOAuthError):
    """Raised when login credentials are invalid."""


class YandexOAuthTimeoutError(YandexOAuthError):
    """Raised when token retrieval timed out."""


class YandexOAuthFetcher:
    """Minimal Selenium flow to capture access_token from OAuth redirect."""

    def __init__(
        self,
        auth_url: str,
        *,
        headless: bool = True,
        timeout: int = 120,
    ) -> None:
        self._auth_url = auth_url
        self._headless = headless
        self._timeout = timeout

    def _build_driver(self) -> webdriver.Chrome:
        options = ChromeOptions()
        if self._headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-infobars")
        options.add_argument("--log-level=3")

        binary_path = os.getenv("YANDEX_OAUTH_CHROME_BINARY")
        if binary_path:
            options.binary_location = binary_path

        capabilities = DesiredCapabilities.CHROME.copy()
        capabilities["goog:loggingPrefs"] = {"performance": "ALL"}
        options.capabilities.update(capabilities)

        driver_path = os.getenv("YANDEX_OAUTH_DRIVER_PATH")
        if driver_path:
            service = ChromeService(driver_path)
        else:
            service = ChromeService(ChromeDriverManager().install())

        driver = webdriver.Chrome(service=service, options=options)
        try:
            driver.execute_cdp_cmd("Page.enable", {})
            driver.execute_cdp_cmd("Network.enable", {})
        except Exception:  # pragma: no cover - safety
            logger.debug("Failed to enable CDP logging", exc_info=True)
        return driver

    def _is_alive(self, driver: webdriver.Chrome) -> bool:
        try:
            driver.title  # simple ping
            return True
        except Exception:
            return False

    def _auto_login(
        self,
        driver: webdriver.Chrome,
        wait: WebDriverWait,
        username: Optional[str],
        password: Optional[str],
        otp: Optional[str],
    ) -> None:
        if not username or not password:
            return

        logger.debug("Attempting automated login")
        login_input = wait.until(EC.presence_of_element_located((By.NAME, "login")))
        login_input.clear()
        login_input.send_keys(username)
        login_input.send_keys(Keys.ENTER)

        try:
            password_input = wait.until(EC.presence_of_element_located((By.NAME, "passwd")))
        except TimeoutException as exc:
            logger.debug("Password field not found: %s", exc)
            return

        password_input.clear()
        password_input.send_keys(password)
        password_input.send_keys(Keys.ENTER)

        if otp:
            try:
                otp_input = wait.until(EC.presence_of_element_located((By.NAME, "otp")))
            except TimeoutException as exc:  # pragma: no cover - only when otp needed
                logger.debug("OTP field not found: %s", exc)
                return
            otp_input.clear()
            otp_input.send_keys(otp)
            otp_input.send_keys(Keys.ENTER)

    def _extract_token_from_logs(self, logs: list[dict]) -> Optional[OAuthToken]:
        for entry in logs:
            raw_message = entry.get("message", "")
            if "access_token=" in raw_message:
                token = self._extract_from_text(raw_message)
                if token:
                    return token
            try:
                payload = json.loads(raw_message).get("message", {})
            except json.JSONDecodeError:
                continue

            params = payload.get("params", {})

            fragment = params.get("frame", {}).get("urlFragment")
            if isinstance(fragment, str) and "access_token=" in fragment:
                return self._token_from_fragment(fragment)

            request_url = params.get("request", {}).get("url")
            if isinstance(request_url, str) and "access_token=" in request_url:
                return self._token_from_fragment(request_url.split("#")[-1])

        return None

    def _extract_from_text(self, text: str) -> Optional[OAuthToken]:
        start = text.find("access_token=")
        if start == -1:
            return None
        substring = text[start:]
        end = substring.find("\"")
        if end == -1:
            end = len(substring)
        fragment = substring[:end].replace("\\u0026", "&")
        return self._token_from_fragment(fragment)

    def _token_from_fragment(self, fragment: str) -> Optional[OAuthToken]:
        parts = fragment.split("&")
        token = None
        expires: Optional[int] = None
        for part in parts:
            if part.startswith("access_token="):
                token = part.split("=", 1)[1]
            if part.startswith("expires_in="):
                try:
                    expires = int(part.split("=", 1)[1])
                except ValueError:
                    expires = None
        if token:
            return OAuthToken(access_token=token, expires_in=expires)
        return None

    def fetch_token(
        self,
        username: Optional[str],
        password: Optional[str],
        *,
        otp: Optional[str] = None,
    ) -> OAuthToken:
        try:
            driver = self._build_driver()
        except WebDriverException as exc:  # pragma: no cover - environment specific
            raise YandexOAuthError(f"Не удалось инициализировать браузер: {exc}") from exc

        token: Optional[OAuthToken] = None
        try:
            logger.debug("Opening OAuth URL: %s", self._auth_url)
            driver.get(self._auth_url)
            wait = WebDriverWait(driver, 30)

            self._auto_login(driver, wait, username, password, otp)

            deadline = time.time() + self._timeout
            while time.time() < deadline and self._is_alive(driver):
                try:
                    logs = driver.get_log("performance")
                except Exception:  # pragma: no cover - driver closed
                    logs = []
                token = self._extract_token_from_logs(logs)
                if token:
                    logger.debug("Yandex OAuth token captured")
                    break
                current_url = driver.current_url
                if current_url and "access_token=" in current_url:
                    token = self._token_from_fragment(current_url.split("#")[-1])
                    if token:
                        logger.debug("Token extracted from current URL")
                        break
                time.sleep(1)

            if not token:
                raise YandexOAuthTimeoutError("Не удалось получить токен за отведённое время")
            return token
        finally:
            try:
                driver.quit()
            except Exception:
                pass
