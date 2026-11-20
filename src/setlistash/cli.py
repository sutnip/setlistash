"""Command-line interface for setlistash."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import getpass
from pathlib import Path
import sys

from setlistash import __version__
from setlistash.api import (
    AuthenticationError,
    RateLimitError,
    SetlistFMClient,
    SetlistFMError,
    UserNotFoundError,
)
from setlistash.config import (
    API_KEY_ENV_VAR,
    AppConfig,
    ConfigError,
    get_local_config_path,
    get_system_config_path,
    load_config_file,
    resolve_api_key,
    resolve_username,
    save_config,
)
from setlistash.exporter import export_backup


def build_parser() -> argparse.ArgumentParser:
    """Construct the root argparse parser with subcommands and aliases."""
    parser = argparse.ArgumentParser(
        prog="setlistash",
        description="Back up all attended concerts from a Setlist.fm profile to CSV and Markdown.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="COMMAND",
    )

    config_parser = subparsers.add_parser(
        "config",
        aliases=["cfg"],
        help="Interactively configure your Setlist.fm API key and default username.",
        description="Save your Setlist.fm API key and optional default username to config.json.",
    )
    config_parser.add_argument(
        "-L",
        "--local",
        action="store_true",
        help="Save config.json in the current working directory (portable mode).",
    )
    config_parser.set_defaults(handler=handle_config)

    run_parser = subparsers.add_parser(
        "run",
        aliases=["backup", "r"],
        help="Download and export all attended concerts for a Setlist.fm user.",
        description="Fetch all attended concerts for USERNAME and write a timestamped backup.",
    )
    run_parser.add_argument(
        "username",
        nargs="?",
        default=None,
        help="Setlist.fm username (optional if default_user is saved in config.json).",
    )
    run_parser.add_argument(
        "-o",
        "--output-dir",
        default=".",
        help="Parent directory where the timestamped backup folder will be created (default: .).",
    )
    run_parser.add_argument(
        "-k",
        "--api-key",
        default=None,
        help=f"Setlist.fm API key (overrides {API_KEY_ENV_VAR} and config files).",
    )
    run_parser.add_argument(
        "-l",
        "--lang",
        default="en",
        help="Accept-Language header sent to Setlist.fm API (default: en).",
    )
    run_parser.add_argument(
        "-r",
        "--include-raw-json",
        action="store_true",
        help="Also save the complete raw API response list to raw_setlists.json.",
    )
    run_parser.set_defaults(handler=handle_run)

    return parser


def handle_config(args: argparse.Namespace) -> int:
    """Execute the interactive configuration subcommand."""
    target_path = (
        get_local_config_path() if args.local else get_system_config_path()
    )

    existing = AppConfig()
    if target_path.is_file():
        try:
            existing = load_config_file(target_path)
        except ConfigError as exc:
            print(f"Warning: {exc}", file=sys.stderr)

    print(f"Configuring setlistash ({target_path})")

    api_prompt = (
        "Setlist.fm API key (input hidden, press Enter to keep existing): "
        if existing.api_key
        else "Setlist.fm API key (input hidden): "
    )
    try:
        entered_key = getpass.getpass(api_prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nConfiguration cancelled.", file=sys.stderr)
        return 130

    final_key = entered_key or existing.api_key
    if not final_key:
        print(
            "Error: An API key is required. Request one at https://www.setlist.fm/settings/api",
            file=sys.stderr,
        )
        return 1

    user_prompt = (
        f"Default Setlist.fm username (optional, press Enter to keep '{existing.default_user}'): "
        if existing.default_user
        else "Default Setlist.fm username (optional, press Enter to skip): "
    )
    try:
        entered_user = input(user_prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nConfiguration cancelled.", file=sys.stderr)
        return 130

    final_user = entered_user if entered_user else existing.default_user

    try:
        saved_path = save_config(
            AppConfig(api_key=final_key, default_user=final_user),
            local=args.local,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Configuration saved to {saved_path}")
    return 0


def handle_run(args: argparse.Namespace) -> int:
    """Execute the backup subcommand."""
    try:
        api_key, _source = resolve_api_key(cli_api_key=args.api_key)
        username = resolve_username(cli_username=args.username)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not api_key:
        print(
            "Error: No Setlist.fm API key found.\n"
            "Run 'setlistash config' to store your API key, set the "
            f"{API_KEY_ENV_VAR} environment variable, or pass '-k <API_KEY>'.",
            file=sys.stderr,
        )
        return 1

    if not username:
        print(
            "Error: No Setlist.fm username provided.\n"
            "Pass a username ('setlistash run <USERNAME>') or save a default user "
            "with 'setlistash config'.",
            file=sys.stderr,
        )
        return 1

    client = SetlistFMClient(api_key=api_key, language=args.lang)
    print(f"Fetching attended concerts for '{username}'...")

    def report_progress(page: int, fetched: int, total: int) -> None:
        if total > 0:
            print(f"  Page {page}: collected {fetched}/{total} concerts")
        else:
            print(f"  Page {page}: collected {fetched} concerts")

    try:
        setlists = client.fetch_attended_setlists(
            user_id=username,
            progress_callback=report_progress,
        )
    except (AuthenticationError, UserNotFoundError, RateLimitError, SetlistFMError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nBackup interrupted by user.", file=sys.stderr)
        return 130

    if not setlists:
        print(f"No attended concerts found for user '{username}'.")
        return 0

    try:
        summary = export_backup(
            setlists=setlists,
            username=username,
            output_dir=Path(args.output_dir),
            include_raw_json=args.include_raw_json,
        )
    except OSError as exc:
        print(f"Error writing backup files: {exc}", file=sys.stderr)
        return 1

    print("\nBackup completed successfully:")
    print(f"  Directory : {summary.backup_dir}")
    print(f"  Concerts  : {summary.event_count}")
    print(f"  Songs     : {summary.total_songs}")
    print(f"  CSV Index : {summary.csv_path}")
    print(f"  Markdown  : {summary.setlists_dir}")
    if summary.raw_json_path is not None:
        print(f"  Raw JSON  : {summary.raw_json_path}")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for CLI executables."""
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())