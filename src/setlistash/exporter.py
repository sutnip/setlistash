"""Backup exporter generating timestamped folders, CSV index, and Markdown files."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from setlistash.utils import (
    UNKNOWN_DATE,
    build_unique_markdown_filename,
    create_unique_backup_dir,
    parse_event_date,
    yaml_quote,
)

CSV_COLUMNS = [
    "event_date",
    "artist",
    "tour",
    "venue",
    "city",
    "state",
    "country",
    "song_count",
    "setlist_id",
    "setlist_url",
    "markdown_file",
]


@dataclass
class ExportSummary:
    """Metadata describing the generated backup archive."""

    backup_dir: Path
    csv_path: Path
    setlists_dir: Path
    raw_json_path: Path | None
    event_count: int
    total_songs: int


def _safe_dict(value: Any) -> dict[str, Any]:
    """Return value if it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def _safe_str(value: Any) -> str:
    """Convert a scalar value to a stripped string, returning empty string for None."""
    if value is None:
        return ""
    return str(value).strip()


def _iter_set_objects(setlist: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract valid set dictionaries from a setlist object."""
    sets_container = _safe_dict(setlist.get("sets"))
    raw_sets = sets_container.get("set")
    if not isinstance(raw_sets, list):
        return []
    return [item for item in raw_sets if isinstance(item, dict)]


def _iter_song_objects(set_obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract valid song dictionaries from a single set object."""
    raw_songs = set_obj.get("song")
    if not isinstance(raw_songs, list):
        return []
    return [song for song in raw_songs if isinstance(song, dict)]


def count_songs(setlist: dict[str, Any]) -> int:
    """Count the total number of recorded song entries in a setlist."""
    total = 0
    for set_obj in _iter_set_objects(setlist):
        total += len(_iter_song_objects(set_obj))
    return total


def extract_event_metadata(setlist: dict[str, Any]) -> dict[str, str]:
    """Safely extract normalized string metadata from a setlist API object."""
    artist_obj = _safe_dict(setlist.get("artist"))
    venue_obj = _safe_dict(setlist.get("venue"))
    city_obj = _safe_dict(venue_obj.get("city"))
    country_obj = _safe_dict(city_obj.get("country"))
    tour_obj = _safe_dict(setlist.get("tour"))

    return {
        "setlist_id": _safe_str(setlist.get("id")),
        "event_date": parse_event_date(setlist.get("eventDate")),
        "artist": _safe_str(artist_obj.get("name")) or "Unknown Artist",
        "tour": _safe_str(tour_obj.get("name")),
        "venue": _safe_str(venue_obj.get("name")) or "Unknown Venue",
        "city": _safe_str(city_obj.get("name")),
        "state": _safe_str(city_obj.get("state")),
        "country": _safe_str(country_obj.get("name")),
        "setlist_url": _safe_str(setlist.get("url")),
        "info": _safe_str(setlist.get("info")),
    }


def _format_set_heading(set_obj: dict[str, Any], set_index: int) -> str:
    """Build a Markdown H3 heading for a set or encore block."""
    set_name = _safe_str(set_obj.get("name"))
    encore_raw = set_obj.get("encore")

    if encore_raw is not None:
        try:
            encore_num = int(encore_raw)
        except (TypeError, ValueError):
            encore_num = 1
        encore_label = "Encore" if encore_num <= 1 else f"Encore {encore_num}"
        if set_name:
            return f"### {encore_label}: {set_name}"
        return f"### {encore_label}"

    if set_name:
        return f"### {set_name}"

    return "### Main Set" if set_index == 0 else f"### Set {set_index + 1}"


def _format_song_line(index: int, song: dict[str, Any]) -> str:
    """Format a numbered Markdown list line for a single song entry."""
    song_name = _safe_str(song.get("name")) or "Untitled Song"
    parts = [f"{index}. {song_name}"]

    cover_obj = _safe_dict(song.get("cover"))
    cover_name = _safe_str(cover_obj.get("name"))
    if cover_name:
        parts.append(f"(cover of {cover_name})")

    with_obj = _safe_dict(song.get("with"))
    with_name = _safe_str(with_obj.get("name"))
    if with_name:
        parts.append(f"(with {with_name})")

    if bool(song.get("tape")):
        parts.append("[tape]")

    song_info = _safe_str(song.get("info"))
    if song_info:
        parts.append(f"({song_info})")

    return " ".join(parts)


def render_setlist_markdown(setlist: dict[str, Any]) -> str:
    """Render a complete Markdown document with YAML frontmatter for one concert."""
    meta = extract_event_metadata(setlist)
    title = f"{meta['artist']} - {meta['venue']} ({meta['event_date']})"

    lines: list[str] = [
        "---",
        f"title: {yaml_quote(title)}",
        f"artist: {yaml_quote(meta['artist'])}",
        f"date: {yaml_quote(meta['event_date'])}",
        f"venue: {yaml_quote(meta['venue'])}",
        f"city: {yaml_quote(meta['city'])}",
        f"country: {yaml_quote(meta['country'])}",
        f"tour: {yaml_quote(meta['tour'])}",
        f"setlist_id: {yaml_quote(meta['setlist_id'])}",
        f"url: {yaml_quote(meta['setlist_url'])}",
        "---",
        "",
        f"# {title}",
        "",
        f"- **Date:** {meta['event_date']}",
        f"- **Artist:** {meta['artist']}",
    ]

    if meta["tour"]:
        lines.append(f"- **Tour:** {meta['tour']}")

    lines.append(f"- **Venue:** {meta['venue']}")

    location_parts = [
        part for part in (meta["city"], meta["state"], meta["country"]) if part
    ]
    if location_parts:
        lines.append(f"- **Location:** {', '.join(location_parts)}")

    if meta["setlist_url"]:
        lines.append(f"- **Setlist.fm:** {meta['setlist_url']}")

    if meta["info"]:
        lines.append(f"- **Event Info:** {meta['info']}")

    lines.extend(["", "## Setlist", ""])

    set_blocks: list[list[str]] = []
    for set_index, set_obj in enumerate(_iter_set_objects(setlist)):
        songs = _iter_song_objects(set_obj)
        if not songs:
            continue
        block = [_format_set_heading(set_obj, set_index), ""]
        for song_idx, song in enumerate(songs, start=1):
            block.append(_format_song_line(song_idx, song))
        set_blocks.append(block)

    if not set_blocks:
        lines.append("*No setlist recorded on setlist.fm.*")
    else:
        for block_idx, block in enumerate(set_blocks):
            if block_idx > 0:
                lines.append("")
            lines.extend(block)

    return "\n".join(lines) + "\n"


def _sort_key_newest_first(setlist: dict[str, Any]) -> tuple[bool, str, str, str]:
    """Build a sort key ordering valid ISO dates newest-to-oldest, unknown dates last."""
    meta = extract_event_metadata(setlist)
    has_valid_date = meta["event_date"] != UNKNOWN_DATE
    date_sort_value = meta["event_date"] if has_valid_date else ""
    return (has_valid_date, date_sort_value, meta["artist"], meta["setlist_id"])


def export_backup(
    setlists: list[dict[str, Any]],
    username: str,
    output_dir: Path,
    include_raw_json: bool = False,
    timestamp: datetime | None = None,
) -> ExportSummary:
    """Write a complete non-destructive backup containing CSV, Markdown, and optional JSON."""
    backup_dir = create_unique_backup_dir(
        output_dir=output_dir,
        username=username,
        timestamp=timestamp,
    )
    setlists_dir = backup_dir / "setlists"
    setlists_dir.mkdir(parents=True, exist_ok=True)

    sorted_setlists = sorted(
        setlists,
        key=_sort_key_newest_first,
        reverse=True,
    )

    used_filenames: set[str] = set()
    csv_rows: list[dict[str, str | int]] = []
    total_songs = 0

    for item in sorted_setlists:
        meta = extract_event_metadata(item)
        song_count = count_songs(item)
        total_songs += song_count

        filename = build_unique_markdown_filename(
            iso_date=meta["event_date"],
            artist=meta["artist"],
            venue=meta["venue"],
            setlist_id=meta["setlist_id"],
            used_filenames=used_filenames,
        )
        markdown_content = render_setlist_markdown(item)
        (setlists_dir / filename).write_text(markdown_content, encoding="utf-8")

        csv_rows.append(
            {
                "event_date": meta["event_date"],
                "artist": meta["artist"],
                "tour": meta["tour"],
                "venue": meta["venue"],
                "city": meta["city"],
                "state": meta["state"],
                "country": meta["country"],
                "song_count": song_count,
                "setlist_id": meta["setlist_id"],
                "setlist_url": meta["setlist_url"],
                "markdown_file": f"setlists/{filename}",
            }
        )

    csv_path = backup_dir / "events.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(csv_rows)

    raw_json_path: Path | None = None
    if include_raw_json:
        raw_json_path = backup_dir / "raw_setlists.json"
        raw_json_path.write_text(
            json.dumps(sorted_setlists, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return ExportSummary(
        backup_dir=backup_dir,
        csv_path=csv_path,
        setlists_dir=setlists_dir,
        raw_json_path=raw_json_path,
        event_count=len(sorted_setlists),
        total_songs=total_songs,
    )