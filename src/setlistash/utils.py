"""Date parsing, cross-platform filename sanitization, and path helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

MAX_FILENAME_LENGTH = 120
UNKNOWN_DATE = "unknown-date"

_CONTROL_AND_FORBIDDEN_RE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')
_WHITESPACE_RE = re.compile(r"\s+")
_MULTI_HYPHEN_RE = re.compile(r"-{2,}")

WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def parse_event_date(date_str: str | None) -> str:
    """Convert a Setlist.fm DD-MM-YYYY date string to ISO YYYY-MM-DD."""
    if not date_str or not isinstance(date_str, str):
        return UNKNOWN_DATE
    cleaned = date_str.strip()
    if not cleaned:
        return UNKNOWN_DATE
    try:
        return datetime.strptime(cleaned, "%d-%m-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return UNKNOWN_DATE


def sanitize_segment(text: str | None, fallback: str = "Unknown") -> str:
    """Sanitize a text segment for safe cross-platform filenames.

    Removes control characters and characters prohibited on Windows, macOS,
    and Linux, replaces whitespace runs with hyphens, strips trailing dots
    and spaces, and guards against Windows reserved device names.
    """
    if not text or not isinstance(text, str):
        return fallback

    cleaned = _CONTROL_AND_FORBIDDEN_RE.sub(" ", text)
    cleaned = _WHITESPACE_RE.sub("-", cleaned.strip())
    cleaned = _MULTI_HYPHEN_RE.sub("-", cleaned)
    cleaned = cleaned.strip(" ._-")

    if not cleaned:
        return fallback

    stem_check = cleaned.split(".", 1)[0].upper()
    if stem_check in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}-item"

    return cleaned


def _truncate_stem(stem: str, max_stem_length: int) -> str:
    """Truncate a filename stem safely without leaving trailing dots or separators."""
    if max_stem_length < 1:
        return "event"
    truncated = stem[:max_stem_length].rstrip(" ._-")
    if not truncated:
        return "event"
    stem_check = truncated.split(".", 1)[0].upper()
    if stem_check in WINDOWS_RESERVED_NAMES:
        adjusted_max = max(1, max_stem_length - 5)
        truncated = f"{truncated[:adjusted_max].rstrip(' ._-')}-item"
    return truncated


def build_unique_markdown_filename(
    iso_date: str,
    artist: str | None,
    venue: str | None,
    setlist_id: str | None,
    used_filenames: set[str],
    max_length: int = MAX_FILENAME_LENGTH,
) -> str:
    """Build a unique, cross-platform-safe Markdown filename capped at max_length.

    Format: {YYYY-MM-DD}_{Artist}_{Venue}.md
    On collision (case-insensitive), appends _{setlist_id} before .md.
    """
    extension = ".md"
    max_stem_length = max(16, max_length - len(extension))

    date_part = sanitize_segment(iso_date, fallback=UNKNOWN_DATE)
    artist_part = sanitize_segment(artist, fallback="Unknown-Artist")
    venue_part = sanitize_segment(venue, fallback="Unknown-Venue")

    base_stem = _truncate_stem(f"{date_part}_{artist_part}_{venue_part}", max_stem_length)
    candidate = f"{base_stem}{extension}"

    if candidate.lower() not in used_filenames:
        used_filenames.add(candidate.lower())
        return candidate

    id_part = sanitize_segment(setlist_id, fallback="id")
    suffix = f"_{id_part}"
    prefix_budget = max(1, max_stem_length - len(suffix))
    stem_with_id = f"{_truncate_stem(base_stem, prefix_budget)}{suffix}"
    candidate = f"{stem_with_id}{extension}"

    counter = 2
    while candidate.lower() in used_filenames:
        extra_suffix = f"_{id_part}_{counter}"
        extra_budget = max(1, max_stem_length - len(extra_suffix))
        candidate = f"{_truncate_stem(base_stem, extra_budget)}{extra_suffix}{extension}"
        counter += 1

    used_filenames.add(candidate.lower())
    return candidate


def yaml_quote(value: str) -> str:
    """Serialize a string as a safely escaped YAML double-quoted scalar."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def create_unique_backup_dir(
    output_dir: Path,
    username: str,
    timestamp: datetime | None = None,
) -> Path:
    """Create a timestamped backup directory without ever overwriting an existing one."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_user = sanitize_segment(username, fallback="user")
    ts = (timestamp if timestamp is not None else datetime.now()).strftime(
        "%Y-%m-%d_%H-%M-%S"
    )
    base_name = f"setlistash_backup_{safe_user}_{ts}"

    candidate = output_dir / base_name
    suffix = 1
    while True:
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = output_dir / f"{base_name}_{suffix}"
            suffix += 1