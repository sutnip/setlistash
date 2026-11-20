"""Cross-platform configuration and credential resolution for setlistash."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "setlistash"
CONFIG_FILENAME = "config.json"
API_KEY_ENV_VAR = "SETLISTFM_API_KEY"


class ConfigError(Exception):
    """Raised when a configuration file cannot be read or written."""


@dataclass
class AppConfig:
    """Stored user configuration for setlistash."""

    api_key: str | None = None
    default_user: str | None = None


def get_local_config_path(cwd: Path | None = None) -> Path:
    """Return the path to a portable config.json in the working directory."""
    base = cwd if cwd is not None else Path.cwd()
    return base / CONFIG_FILENAME


def get_system_config_path() -> Path:
    """Return the OS-appropriate config.json path via platformdirs."""
    return Path(user_config_dir(APP_NAME)) / CONFIG_FILENAME


def load_config_file(path: Path) -> AppConfig:
    """Load an AppConfig from a JSON file if it exists."""
    if not path.is_file():
        return AppConfig()

    try:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Failed to read configuration file '{path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"Configuration file '{path}' must contain a JSON object.")

    raw_key = data.get("api_key")
    raw_user = data.get("default_user")

    api_key = raw_key.strip() if isinstance(raw_key, str) and raw_key.strip() else None
    default_user = (
        raw_user.strip() if isinstance(raw_user, str) and raw_user.strip() else None
    )

    return AppConfig(api_key=api_key, default_user=default_user)


def save_config(
    config: AppConfig,
    local: bool = False,
    cwd: Path | None = None,
) -> Path:
    """Persist configuration to either the local directory or system config directory."""
    target_path = (
        get_local_config_path(cwd=cwd) if local else get_system_config_path()
    )
    payload = {
        "api_key": config.api_key or "",
        "default_user": config.default_user or "",
    }

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            os.chmod(target_path, 0o600)
    except OSError as exc:
        raise ConfigError(
            f"Failed to write configuration file '{target_path}': {exc}"
        ) from exc

    return target_path


def resolve_api_key(
    cli_api_key: str | None = None,
    cwd: Path | None = None,
) -> tuple[str | None, str | None]:
    """Resolve the Setlist.fm API key following the documented precedence hierarchy.

    Precedence:
    1. CLI argument (-k / --api-key)
    2. Environment variable (SETLISTFM_API_KEY)
    3. Local config.json in current working directory
    4. System config.json in platformdirs user_config_dir
    """
    if cli_api_key and cli_api_key.strip():
        return cli_api_key.strip(), "cli"

    env_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if env_key:
        return env_key, "env"

    local_path = get_local_config_path(cwd=cwd)
    if local_path.is_file():
        local_cfg = load_config_file(local_path)
        if local_cfg.api_key:
            return local_cfg.api_key, "local_config"

    system_path = get_system_config_path()
    if system_path.is_file():
        system_cfg = load_config_file(system_path)
        if system_cfg.api_key:
            return system_cfg.api_key, "system_config"

    return None, None


def resolve_username(
    cli_username: str | None = None,
    cwd: Path | None = None,
) -> str | None:
    """Resolve the Setlist.fm username from CLI argument or config files."""
    if cli_username and cli_username.strip():
        return cli_username.strip()

    local_path = get_local_config_path(cwd=cwd)
    if local_path.is_file():
        local_cfg = load_config_file(local_path)
        if local_cfg.default_user:
            return local_cfg.default_user

    system_path = get_system_config_path()
    if system_path.is_file():
        system_cfg = load_config_file(system_path)
        if system_cfg.default_user:
            return system_cfg.default_user

    return None