"""Setlist.fm REST API v1.0 client with pagination and rate-limit handling."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any
from urllib.parse import quote

import requests

from setlistash import __version__


class SetlistFMError(Exception):
    """Base exception for Setlist.fm API errors."""


class AuthenticationError(SetlistFMError):
    """Raised on HTTP 403 when the API key is missing or invalid."""


class UserNotFoundError(SetlistFMError):
    """Raised on HTTP 404 when the user does not exist or has no attended shows."""


class RateLimitError(SetlistFMError):
    """Raised when HTTP 429 retries are exhausted."""


class SetlistFMClient:
    """Client for retrieving attended setlists from the Setlist.fm v1.0 API."""

    BASE_URL = "https://api.setlist.fm/rest/1.0"
    MIN_REQUEST_INTERVAL = 0.6
    MAX_RETRIES = 3
    REQUEST_TIMEOUT = 30.0

    def __init__(
        self,
        api_key: str,
        language: str = "en",
        min_interval: float = MIN_REQUEST_INTERVAL,
        max_retries: int = MAX_RETRIES,
        timeout: float = REQUEST_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key
        self.language = language or "en"
        self.min_interval = max(0.0, min_interval)
        self.max_retries = max(0, max_retries)
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()
        self.session.headers.update(
            {
                "x-api-key": self.api_key,
                "Accept": "application/json",
                "Accept-Language": self.language,
                "User-Agent": f"setlistash/{__version__}",
            }
        )
        self._last_request_at: float | None = None

    def _respect_rate_limit(self) -> None:
        """Sleep if needed to enforce the minimum delay between consecutive requests."""
        if self._last_request_at is None or self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _parse_retry_after(self, response: requests.Response) -> float | None:
        """Parse a numeric Retry-After header value in seconds, if present."""
        header_value = response.headers.get("Retry-After")
        if not header_value:
            return None
        try:
            parsed = float(header_value.strip())
            return parsed if parsed > 0 else None
        except ValueError:
            return None

    def _request_page(self, user_id: str, page: int) -> dict[str, Any]:
        """Fetch a single paginated attended setlists page with retry on HTTP 429."""
        encoded_user = quote(user_id, safe="")
        url = f"{self.BASE_URL}/user/{encoded_user}/attended"

        for attempt in range(self.max_retries + 1):
            self._respect_rate_limit()
            try:
                response = self.session.get(
                    url,
                    params={"p": page},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise SetlistFMError(
                    f"Network error while contacting setlist.fm (page {page}): {exc}"
                ) from exc
            finally:
                self._last_request_at = time.monotonic()

            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise SetlistFMError(
                        f"Invalid JSON payload returned by setlist.fm on page {page}."
                    ) from exc
                if not isinstance(payload, dict):
                    raise SetlistFMError(
                        f"Unexpected response format returned by setlist.fm on page {page}."
                    )
                return payload

            if response.status_code == 403:
                raise AuthenticationError(
                    "HTTP 403 Forbidden: Your Setlist.fm API key is missing or invalid. "
                    "Run 'setlistash config' to save a valid API key."
                )

            if response.status_code == 404:
                raise UserNotFoundError(
                    f"HTTP 404 Not Found: User '{user_id}' was not found or has no "
                    "attended concerts recorded on setlist.fm."
                )

            if response.status_code == 429:
                if attempt < self.max_retries:
                    backoff = 2.0 * (2**attempt)
                    retry_after = self._parse_retry_after(response)
                    sleep_seconds = (
                        max(backoff, retry_after) if retry_after is not None else backoff
                    )
                    time.sleep(sleep_seconds)
                    continue
                raise RateLimitError(
                    "HTTP 429 Too Many Requests: Rate limit exceeded after "
                    f"{self.max_retries} retry attempts. Please wait and try again later."
                )

            detail = response.text.strip()[:200]
            raise SetlistFMError(
                f"Setlist.fm API returned HTTP {response.status_code} on page {page}: {detail}"
            )

        raise RateLimitError("Failed to retrieve setlists due to repeated rate limiting.")

    def fetch_attended_setlists(
        self,
        user_id: str,
        progress_callback: Callable[[int, int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve all attended setlists for a given user across all pages."""
        collected: list[dict[str, Any]] = []
        page = 1

        while True:
            payload = self._request_page(user_id=user_id, page=page)
            raw_items = payload.get("setlist")
            if not isinstance(raw_items, list) or not raw_items:
                break

            valid_items = [item for item in raw_items if isinstance(item, dict)]
            if not valid_items:
                break

            collected.extend(valid_items)

            try:
                total = int(payload.get("total") or 0)
            except (TypeError, ValueError):
                total = 0

            try:
                items_per_page = int(payload.get("itemsPerPage") or 0)
            except (TypeError, ValueError):
                items_per_page = 0

            if progress_callback is not None:
                progress_callback(page, len(collected), total)

            if total > 0 and len(collected) >= total:
                break
            if total == 0 and items_per_page > 0 and len(raw_items) < items_per_page:
                break

            page += 1

        return collected