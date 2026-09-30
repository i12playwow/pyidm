from __future__ import annotations

import json
import os
from pathlib import Path

# Windows user environment variables (HKCU\Environment) — written WITHOUT admin
# rights so that frozen GUI exes (which don't re-read ~/.idm/config.json changes
# made after they started, or run from anywhere) still see the API key.
_ENV_KEYS = {"OPENSUBTITLES_API_KEY", "IDM_OUT", "IDM_WORKERS"}


def _user_env_read(name: str) -> str | None:
    """Read one value from the user's persisted environment (HKCU\\Environment)."""
    if os.name != "nt":
        return os.environ.get(name)
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
            value, _ = winreg.QueryValueEx(k, name)
            return value
    except OSError:
        return None


def _user_env_write(name: str, value: str | None) -> None:
    """Persist one user environment variable and broadcast the change.

    Pass value=None to delete the variable. The broadcast (WM_SETTINGCHANGE)
    makes Explorer and newly launched processes pick it up without logoff.
    """
    os.environ[name] = value or ""  # current process sees it immediately
    if name in ("IDM_OUT", "IDM_WORKERS") and value == "":
        os.environ.pop(name, None)
    if os.name != "nt":
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
        if value is None or value == "":
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    try:
        import ctypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            SMTO_ABORTIFHUNG, 2000, None,
        )
    except Exception:
        pass  # broadcast is best-effort


def get_user_env(name: str) -> str | None:
    """Read a persisted user environment variable (never a full secret to stdout)."""
    return _user_env_read(name)


def set_user_env(name: str, value: str) -> None:
    """Persist a user environment variable (HKCU Environment key + broadcast)."""
    _user_env_write(name, value)


def clear_user_env(name: str) -> None:
    """Delete a persisted user environment variable."""
    _user_env_write(name, None)


def mask_secret(value: str | None) -> str:
    """abcd1234efgh5678 -> abcd************ (ASCII-only so legacy Windows
    consoles with non-UTF8 codepages never choke on it)."""
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * min(len(value) - 4, 16) + "..."

APP_DIR = Path.home() / ".idm"
USER_CONFIG_PATH = APP_DIR / "config.json"
LOCAL_CONFIG_NAME = "idm.json"

DEFAULTS: dict = {
    "out_dir": "downloads",
    "collector_port": 27492,
    "collector_autostart": False,
    "workers": 4,
    "segments": 8,
    "min_segmented_size": 4 * 1024 * 1024,
    "retries": 5,
    "html_guard": True,  # refuse to save web-page bytes as a download
    # part of html_guard: also refuse HLS/M3U playlists served as the media
    # itself (segment lists can never play as the video their name promises)
    "playlist_guard": True,
    "max_refreshes": 3,
    "timeout": 30,
    "chunk_size": 1 << 20,  # 1 MiB
    "opensubtitles_api_key": "",
    "opensubtitles_user_agent": "TemporaryUserAgent",
    "subtitle_languages": "en",
    "subs_autoplay": False,
    "subtitle_history_max_age_days": 0,  # 0 disables auto-prune
    "player": "",
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PyIDM/1.0",
        "Accept": "*/*",
    },
    # per-domain extra request headers for hotlink-protected sites; each
    # rule: {"match": "<url regex>", "headers": {"Referer": "…", "Cookie": "…"}}
    # — every matching rule is applied, later rules win on conflicting
    # keys; a rule without "match" applies to every URL.
    "domain_headers": [],
    # filenames the magic-byte verify scan should never warn about — the
    # escape hatch for files you have inspected and decided are fine (e.g.
    # HLS playlists deliberately saved under a media name). Plain names are
    # exact matches; "*.ext" entries ignore by extension; "prefix*" entries
    # ignore by prefix. Managed by 'idm ignore add|remove|list' and the
    # GUI's Tools menu (shared helpers below).
    "verify_ignore": [],
    "link_providers": [],
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_user_config() -> dict:
    return _read_json(USER_CONFIG_PATH)


def load_local_config() -> dict:
    return _read_json(Path(LOCAL_CONFIG_NAME))


def effective_config_with_sources(path: str | None = None) -> tuple[dict, dict[str, str]]:
    """The merged config plus, for every top-level key, which layer last set it:
    "default" | "user (~/.idm/config.json)" | "local (idm.json)" |
    "extra (<path>)" | "environment". Used by the GUI About dialog."""
    cfg = dict(DEFAULTS)
    sources = dict.fromkeys(cfg, "default")

    user = load_user_config()
    for k, v in user.items():
        cfg[k] = _deep_merge(cfg[k], v) if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
        sources[k] = "user (~/.idm/config.json)"

    extra = _read_json(Path(path)) if path else load_local_config()
    if extra:
        label = f"extra ({path})" if path else "local (idm.json)"
        for k, v in extra.items():
            cfg[k] = _deep_merge(cfg[k], v) if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
            sources[k] = label

    # environment overrides win over everything
    if os.environ.get("IDM_OUT"):
        cfg["out_dir"] = os.environ["IDM_OUT"]
        sources["out_dir"] = "environment"
    if os.environ.get("IDM_WORKERS"):
        try:
            cfg["workers"] = int(os.environ["IDM_WORKERS"])
            sources["workers"] = "environment"
        except ValueError:
            pass
    if os.environ.get("OPENSUBTITLES_API_KEY"):
        cfg["opensubtitles_api_key"] = os.environ["OPENSUBTITLES_API_KEY"]
        sources["opensubtitles_api_key"] = "environment"
    return cfg, sources


def _env_layer() -> dict:
    """The environment layer as key/value pairs (same keys get_config uses)."""
    env: dict = {}
    if os.environ.get("IDM_OUT"):
        env["out_dir"] = os.environ["IDM_OUT"]
    if os.environ.get("IDM_WORKERS"):
        try:
            env["workers"] = int(os.environ["IDM_WORKERS"])
        except ValueError:
            pass
    if os.environ.get("OPENSUBTITLES_API_KEY"):
        env["opensubtitles_api_key"] = os.environ["OPENSUBTITLES_API_KEY"]
    return env


def config_layers(path: str | None = None) -> tuple[dict, dict[str, str], dict[str, list[str]]]:
    """Effective config + winner per key + EVERY layer defining each key.

    Returns (cfg, sources, layer_map) where layer_map[key] lists defining
    layers in merge order, e.g. ['default', 'user (~/.idm/config.json)',
    'environment'] — the last entry is what won. Used by 'idm doctor'.
    """
    cfg = dict(DEFAULTS)
    sources: dict[str, str] = dict.fromkeys(cfg, "default")
    layer_map: dict[str, list[str]] = {}

    def apply(layer_values: dict, label: str) -> None:
        for k, v in layer_values.items():
            if k in layer_map:
                layer_map[k].append(label)
            else:
                layer_map[k] = ["default", label]
            cfg[k] = _deep_merge(cfg[k], v) if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
            sources[k] = label

    apply(load_user_config(), "user (~/.idm/config.json)")
    apply(_read_json(Path(path)) if path else load_local_config(),
          f"extra ({path})" if path else "local (idm.json)")
    apply(_env_layer(), "environment")
    return cfg, sources, layer_map


def diagnose_config(path: str | None = None) -> list[dict]:
    """Find config keys whose value is silently overridden by a higher layer.

    Each finding: {severity: 'warn'|'info', key, winner, winner_value,
    loser, loser_value}. 'warn' = the layers disagree (a real override);
    'info' = they agree (shadowing is harmless but worth knowing).
    """
    cfg, _sources, layer_map = config_layers(path)
    findings = []
    for key, layers in layer_map.items():
        if len(layers) < 2:
            continue
        if key not in DEFAULTS:
            continue  # unknown keys are reported separately below
        loser_label = layers[-2]
        if loser_label == "default":
            continue  # a file/env layer overriding a default is normal usage
        # effective value (last layer won) and the value of the second-to-last
        winner_label = layers[-1]
        winner_value = cfg.get(key)
        # recompute the loser's value by replaying layers up to (not incl.) winner
        loser_value = _value_at_layer(key, layers[:-1])
        differs = loser_value != winner_value
        findings.append({
            "kind": "override",
            "severity": "warn" if differs else "info",
            "key": key,
            "winner": winner_label,
            "winner_value": winner_value,
            "loser": loser_label,
            "loser_value": loser_value,
        })
    # unknown keys in config files: silently ignored by the merge (likely typos)
    for label, data in (("user (~/.idm/config.json)", load_user_config()),
                        ("local (idm.json)", load_local_config()),
                        *((f"extra ({path})", _read_json(Path(path))) if path else ())) :
        for key in data:
            if key not in DEFAULTS:
                findings.append({
                    "kind": "unknown",
                    "severity": "warn",
                    "key": key,
                    "winner": "(ignored)",
                    "winner_value": data[key],
                    "loser": label,
                    "loser_value": data[key],
                })
    return findings


def _value_at_layer(key: str, layers: list[str]):
    """Replay merges for one key up to (not including) the final layer."""
    value = DEFAULTS.get(key)
    for label in layers[1:]:  # skip 'default'
        if label == "user (~/.idm/config.json)":
            data = load_user_config()
        elif label.startswith("extra ("):
            data = _read_json(Path(label[len("extra ("):-1]))
        elif label == "local (idm.json)":
            data = load_local_config()
        elif label == "environment":
            data = _env_layer()
        else:
            continue
        if key in data:
            v = data[key]
            value = _deep_merge(value, v) if isinstance(v, dict) and isinstance(value, dict) else v
    return value


def get_config(path: str | None = None) -> dict:
    """Merge defaults <- ~/.idm/config.json <- ./idm.json <- environment."""
    cfg, _ = effective_config_with_sources(path)
    return cfg


# Keys whose values must stay strings even when they look like numbers/bools
# (e.g. an all-digit OpenSubtitles API key would otherwise be coerced to int).
STRING_KEYS = {
    "opensubtitles_api_key", "opensubtitles_user_agent", "player",
    "subtitle_languages", "out_dir", "subtitle_providers",
}


def _coerce(key: str, value: str):
    """Coerce CLI/GUI string input per key: STRING_KEYS stay verbatim strings
    (an all-digit API key must never become an int); everything else parses as
    JSON when possible (numbers, bools, nested dicts/lists), else verbatim."""
    if key in STRING_KEYS:
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def set_config_value(key: str, value: str) -> dict:
    """Persist one key into ~/.idm/config.json, preserving other keys."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_user_config()
    cfg[key] = _coerce(key, value)
    USER_CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def remove_config_value(key: str) -> bool:
    """Delete one key from ~/.idm/config.json (falls back to lower layers).
    Returns True if the key existed."""
    cfg = load_user_config()
    if key not in cfg:
        return False
    del cfg[key]
    APP_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return True


def normalize_verify_ignore(value) -> list[str]:
    """Coerce a verify_ignore value into a clean list of patterns: a bare
    string becomes one entry, None/anything else becomes a list, entries
    are stringified, whitespace-stripped, and deduplicated (order kept).
    Defensive on purpose — the key is user-edited JSON, and the verify
    scan must never die over a malformed list."""
    items = [value] if isinstance(value, str) else (value or [])
    if not isinstance(items, (list, tuple)):
        items = []
    out: list[str] = []
    for item in items:
        entry = str(item).strip()
        if entry and entry not in out:
            out.append(entry)
    return out


def verify_ignore_list(cfg: dict | None = None) -> list[str]:
    """The effective verify_ignore patterns (normalized) from the merged
    config — what the verify scan and the ignore manager read."""
    return normalize_verify_ignore((cfg if cfg is not None else get_config())
                                   .get("verify_ignore"))


def verify_ignore_add(cfg: dict | None, patterns: list[str]) -> list[str]:
    """Add patterns to verify_ignore in ~/.idm/config.json (deduplicated,
    keeping existing order, new ones appended). Returns the new list."""
    current = normalize_verify_ignore((cfg or {}).get("verify_ignore"))
    for pattern in patterns:
        entry = str(pattern).strip()
        if entry and entry not in current:
            current.append(entry)
    set_config_value("verify_ignore", json.dumps(current))
    return current


def verify_ignore_remove(cfg: dict | None, patterns: list[str]) -> tuple[list[str], list[str]]:
    """Remove patterns from verify_ignore in ~/.idm/config.json (exact
    matches only). Returns (new_list, removed) — removed lists the
    patterns that were actually present."""
    current = normalize_verify_ignore((cfg or {}).get("verify_ignore"))
    wanted = {str(p).strip() for p in patterns}
    removed = [p for p in current if p in wanted]
    current = [p for p in current if p not in wanted]
    set_config_value("verify_ignore", json.dumps(current))
    return current, removed
