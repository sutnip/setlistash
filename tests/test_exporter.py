"""Unit tests for date conversion, filename sanitization, and backup export."""

from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from setlistash.exporter import CSV_COLUMNS, export_backup, render_setlist_markdown
from setlistash.utils import (
    MAX_FILENAME_LENGTH,
    UNKNOWN_DATE,
    build_unique_markdown_filename,
    parse_event_date,
    sanitize_segment,
)


def _sample_setlist_full() -> dict[str, Any]:
    return {
        "id": "63de4613",
        "versionId": "7be1aa04",
        "eventDate": "23-08-2024",
        "lastUpdated": "2024-08-24T10:15:00.000+0000",
        "artist": {
            "mbid": "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d",
            "name": 'AC/DC: "Live" Band?',
            "sortName": "AC/DC",
            "url": "https://www.setlist.fm/setlists/acdc.html",
        },
        "venue": {
            "id": "4bd63b76",
            "name": "Stadio Olimpico | Rome...",
            "city": {
                "id": "3169070",
                "name": "Rome",
                "state": "Lazio",
                "stateCode": "07",
                "country": {"code": "IT", "name": "Italy"},
            },
            "url": "https://www.setlist.fm/venue/stadio-olimpico.html",
        },
        "tour": {"name": "World Tour 2024"},
        "sets": {
            "set": [
                {
                    "name": "Acoustic Set",
                    "song": [
                        {
                            "name": "Song Title",
                            "with": {"name": "Guest Artist"},
                            "cover": {"name": "Original Artist"},
                            "info": "live debut",
                            "tape": False,
                        },
                        {
                            "name": "Intro Tape",
                            "tape": True,
                        },
                    ],
                },
                {
                    "encore": 1,
                    "song": [{"name": "Encore Song"}],
                },
                {
                    "encore": 2,
                    "song": [{"name": "Final Encore"}],
                },
            ]
        },
        "info": "Festival headline show",
        "url": "https://www.setlist.fm/setlist/artist/2024/63de4613.html",
    }


def _sample_setlist_empty() -> dict[str, Any]:
    return {
        "id": "11aa22bb",
        "eventDate": "05-05-2022",
        "artist": {"name": "Minimal Band"},
        "venue": {
            "name": "Small Club",
            "city": {
                "name": "Milan",
                "country": {"code": "IT", "name": "Italy"},
            },
        },
        "sets": {"set": []},
        "url": "https://www.setlist.fm/setlist/minimal/2022/11aa22bb.html",
    }


def test_parse_event_date_valid_and_invalid() -> None:
    assert parse_event_date("23-08-2024") == "2024-08-23"
    assert parse_event_date(" 01-01-1999 ") == "1999-01-01"
    assert parse_event_date("2024-08-23") == UNKNOWN_DATE
    assert parse_event_date("") == UNKNOWN_DATE
    assert parse_event_date(None) == UNKNOWN_DATE


def test_sanitize_segment_windows_and_posix_rules() -> None:
    dirty = 'A<B>C:D"E/F\\G|H?I*J\x00\x1f   ... '
    cleaned = sanitize_segment(dirty)
    for forbidden in '<>:"/\\|?*\x00\x1f':
        assert forbidden not in cleaned
    assert not cleaned.endswith(".")
    assert not cleaned.endswith(" ")
    assert "  " not in cleaned
    assert cleaned == "A-B-C-D-E-F-G-H-I-J"

    assert sanitize_segment("CON") == "CON-item"
    assert sanitize_segment("nul") == "nul-item"
    assert sanitize_segment("   ...   ", fallback="Fallback") == "Fallback"


def test_filename_max_length_and_collision_deduplication() -> None:
    used: set[str] = set()
    long_artist = "A" * 90
    long_venue = "V" * 90

    first = build_unique_markdown_filename(
        iso_date="2024-08-23",
        artist=long_artist,
        venue=long_venue,
        setlist_id="abc12345",
        used_filenames=used,
    )
    second = build_unique_markdown_filename(
        iso_date="2024-08-23",
        artist=long_artist,
        venue=long_venue,
        setlist_id="def67890",
        used_filenames=used,
    )

    assert len(first) <= MAX_FILENAME_LENGTH
    assert len(second) <= MAX_FILENAME_LENGTH
    assert first != second
    assert second.endswith("_def67890.md")


def test_filename_collision_is_case_insensitive() -> None:
    used: set[str] = set()
    first = build_unique_markdown_filename(
        iso_date="2024-08-23",
        artist="Radiohead",
        venue="Arena",
        setlist_id="id1",
        used_filenames=used,
    )
    second = build_unique_markdown_filename(
        iso_date="2024-08-23",
        artist="radiohead",
        venue="ARENA",
        setlist_id="id2",
        used_filenames=used,
    )

    assert first == "2024-08-23_Radiohead_Arena.md"
    assert second == "2024-08-23_radiohead_ARENA_id2.md"


def test_render_empty_setlist_and_missing_optional_fields() -> None:
    minimal_payload: dict[str, Any] = {
        "id": "onlyid99",
        "artist": None,
        "venue": None,
        "tour": None,
        "sets": None,
    }
    rendered = render_setlist_markdown(minimal_payload)
    assert 'setlist_id: "onlyid99"' in rendered
    assert "# Unknown Artist - Unknown Venue (unknown-date)" in rendered
    assert "*No setlist recorded on setlist.fm.*" in rendered

    empty_rendered = render_setlist_markdown(_sample_setlist_empty())
    assert "*No setlist recorded on setlist.fm.*" in empty_rendered
    assert "- **Tour:**" not in empty_rendered


def test_render_full_setlist_annotations_and_encores() -> None:
    rendered = render_setlist_markdown(_sample_setlist_full())

    assert 'artist: "AC/DC: \\"Live\\" Band?"' in rendered
    assert 'tour: "World Tour 2024"' in rendered
    assert "### Acoustic Set" in rendered
    assert (
        "1. Song Title (cover of Original Artist) (with Guest Artist) (live debut)"
        in rendered
    )
    assert "2. Intro Tape [tape]" in rendered
    assert "### Encore\n\n1. Encore Song" in rendered
    assert "### Encore 2\n\n1. Final Encore" in rendered
    assert "- **Event Info:** Festival headline show" in rendered


def test_export_backup_csv_markdown_raw_json_and_zero_overwrite(tmp_path: Path) -> None:
    older = _sample_setlist_empty()
    newer = _sample_setlist_full()
    fixed_time = datetime(2026, 11, 20, 12, 30, 0)

    summary_1 = export_backup(
        setlists=[older, newer],
        username="musicfan",
        output_dir=tmp_path,
        include_raw_json=True,
        timestamp=fixed_time,
    )

    assert summary_1.backup_dir.name == "setlistash_backup_musicfan_2026-11-20_12-30-00"
    assert summary_1.event_count == 2
    assert summary_1.total_songs == 4
    assert summary_1.csv_path.is_file()
    assert summary_1.raw_json_path is not None and summary_1.raw_json_path.is_file()

    raw_bytes = summary_1.csv_path.read_bytes()
    assert raw_bytes.startswith(b"\xef\xbb\xbf")

    with summary_1.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == CSV_COLUMNS
        rows = list(reader)

    assert len(rows) == 2
    assert rows[0]["event_date"] == "2024-08-23"
    assert rows[0]["song_count"] == "4"
    assert rows[0]["setlist_id"] == "63de4613"
    assert rows[0]["markdown_file"].startswith("setlists/2024-08-23_")
    assert (summary_1.backup_dir / rows[0]["markdown_file"]).is_file()

    assert rows[1]["event_date"] == "2022-05-05"
    assert rows[1]["song_count"] == "0"
    assert rows[1]["markdown_file"] == "setlists/2022-05-05_Minimal-Band_Small-Club.md"
    assert (summary_1.backup_dir / rows[1]["markdown_file"]).is_file()

    raw_dump = json.loads(summary_1.raw_json_path.read_text(encoding="utf-8"))
    assert len(raw_dump) == 2
    assert raw_dump[0]["id"] == "63de4613"

    summary_2 = export_backup(
        setlists=[older],
        username="musicfan",
        output_dir=tmp_path,
        include_raw_json=False,
        timestamp=fixed_time,
    )
    assert summary_2.backup_dir.name == "setlistash_backup_musicfan_2026-11-20_12-30-00_1"
    assert summary_2.raw_json_path is None
    assert summary_1.backup_dir.is_dir()