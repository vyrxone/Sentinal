"""Validated, persistent application settings (no secrets are ever stored)."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .database import Database

log = logging.getLogger("sentinel.settings")

MAX_IMPORT_BYTES = 64 * 1024


class SettingError(ValueError):
    pass


@dataclass(frozen=True)
class SettingDef:
    key: str
    default: Any
    kind: str  # bool | int | float | choice
    label: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()


DEFINITIONS: tuple[SettingDef, ...] = (
    SettingDef("animation_fps", 24, "choice", "Animation frame-rate cap", choices=(15, 24, 30)),
    SettingDef("animation_intensity", "medium", "choice", "Animation intensity",
               choices=("low", "medium", "high")),
    SettingDef("animation_paused", False, "bool", "Animation paused"),
    SettingDef("reduce_under_load", True, "bool", "Reduce effects under sustained load"),
    SettingDef("show_overlay", True, "bool", "Show compact monitoring overlay"),
    SettingDef("start_mode", "wallpaper", "choice", "Mode on launch", choices=("wallpaper", "panel")),
    SettingDef("desktop_integration", "auto", "choice", "Wallpaper placement",
               choices=("auto", "window_only")),
    SettingDef("open_panel_on_start", True, "bool", "Open control panel at launch"),
    SettingDef("close_to_tray", True, "bool", "Closing the panel keeps Sentinel in the tray"),
    SettingDef("monitoring_enabled", True, "bool", "System monitoring enabled"),
    SettingDef("security_enabled", True, "bool", "Folder watching enabled"),
    SettingDef("sample_interval_s", 2.0, "float", "Sampling interval (seconds)", 1.0, 10.0),
    SettingDef("cpu_warn_percent", 85, "int", "CPU warning threshold (%)", 10, 100),
    SettingDef("ram_warn_percent", 85, "int", "Memory warning threshold (%)", 10, 100),
    SettingDef("disk_warn_percent", 90, "int", "System disk warning threshold (%)", 10, 100),
    SettingDef("sustained_samples", 3, "int", "Samples above limit before warning", 1, 30),
    SettingDef("max_events", 50000, "int", "Maximum audit events kept", 1000, 500000),
    # ---- V2 ----
    SettingDef("appearance_preset", "sentinel_core", "choice", "Appearance preset",
               choices=("sentinel_core", "crimson_watch", "silent_guardian", "custom")),
    SettingDef("overlay_mode", "compact", "choice", "Overlay size", choices=("compact", "expanded")),
    SettingDef("emblem_size", 100, "int", "Emblem size (%)", 50, 150),
    SettingDef("particle_intensity", "medium", "choice", "Particle intensity",
               choices=("off", "low", "medium", "high")),
    SettingDef("pulse_intensity", "medium", "choice", "Ring and pulse intensity",
               choices=("off", "low", "medium", "high")),
    SettingDef("reduced_motion", False, "bool", "Reduced motion (static emblem, no continuous animation)"),
    SettingDef("dimmer_percent", 0, "int", "Wallpaper dimmer (%)", 0, 70),
    SettingDef("show_clock", True, "bool", "Show clock and date on the wallpaper"),
    SettingDef("monitor_mode", "primary", "choice", "Wallpaper monitors",
               choices=("primary", "selected", "all")),
    SettingDef("selected_monitor", "", "text", "Selected monitor id"),
    SettingDef("notifications_enabled", True, "bool", "Tray notifications for warnings"),
    SettingDef("notify_min_interval_s", 60, "int", "Minimum seconds between notifications", 5, 3600),
    SettingDef("process_interval_s", 6, "int", "Process list refresh interval (seconds)", 2, 60),
    # ---- V3 ----
    SettingDef("findings_enabled", True, "bool", "Analyse the audit log for security findings"),
    SettingDef("findings_sensitivity", "normal", "choice", "Findings sensitivity",
               choices=("low", "normal", "high")),
    # ---- V4 (AI is OFF by default; no key is ever a setting) ----
    SettingDef("ai_enabled", False, "bool", "AI Assistant enabled (off by default)"),
    SettingDef("ai_primary", "gemini", "choice", "Primary AI provider", choices=("gemini", "groq", "openrouter")),
    SettingDef("ai_fallback_order", "", "text", "Fallback providers in order (comma separated)"),
    SettingDef("ai_gemini_enabled", False, "bool", "Gemini enabled"),
    SettingDef("ai_gemini_model", "", "text", "Gemini model ID"),
    SettingDef("ai_gemini_timeout", 30, "int", "Gemini timeout (seconds)", 3, 120),
    SettingDef("ai_groq_enabled", False, "bool", "Groq enabled"),
    SettingDef("ai_groq_model", "", "text", "Groq model ID"),
    SettingDef("ai_groq_timeout", 30, "int", "Groq timeout (seconds)", 3, 120),
    SettingDef("ai_openrouter_enabled", False, "bool", "OpenRouter enabled"),
    SettingDef("ai_openrouter_model", "", "text", "OpenRouter model ID"),
    SettingDef("ai_openrouter_timeout", 30, "int", "OpenRouter timeout (seconds)", 3, 120),
    SettingDef("ai_max_response", 700, "int", "Maximum response length (tokens)", 64, 4096),
    SettingDef("ai_max_requests_hour", 20, "int", "Maximum AI requests per hour", 1, 200),
    SettingDef("ai_max_requests_session", 100, "int", "Maximum AI requests per session", 1, 1000),
    SettingDef("ai_history_enabled", False, "bool", "Keep conversations on this PC"),
    SettingDef("ai_history_messages", 6, "int", "Earlier messages offered with a new question", 0, 20),
    SettingDef("ai_consent_mode", "every_request", "choice", "Review before sending",
               choices=("every_request", "new_context_only")),
    SettingDef("ai_redact_paths", True, "bool", "Remove folder paths from AI context"),
    SettingDef("ai_redact_names", True, "bool", "Remove user, machine and e-mail names from AI context"),
    SettingDef("ai_redact_addresses", True, "bool", "Remove IP and MAC addresses from AI context"),
    SettingDef("ai_fallback_on_permanent", False, "bool", "Also try the next provider after key/model errors"),
    SettingDef("ai_context_max_events", 20, "int", "Maximum events offered as AI context", 1, 50),
    # ---- V5 ----
    SettingDef("ai_proxy_enabled", False, "bool", "Send AI requests through an HTTP proxy (off = direct connection)"),
    SettingDef("ai_proxy_host", "", "text", "Proxy host name or IP address"),
    SettingDef("ai_proxy_port", 8080, "int", "Proxy port", 1, 65535),
    SettingDef("ai_proxy_auth", False, "bool", "The proxy requires a user name and password"),
    SettingDef("lightweight_mode", False, "bool", "Lightweight mode for low-memory PCs (15 fps, fewest effects, slower sampling)"),
)
DEFS_BY_KEY = {d.key: d for d in DEFINITIONS}
V2_END = 30          # DEFINITIONS[17:30] are the V2 keys; [30:32] were added in V3; later ones in V4
V3_END = 32
V4_END = 55          # DEFINITIONS[32:55] are the V4 (AI) keys; later ones were added in V5
PROXY_ON_REJECTED = "The proxy can only be switched on in the AI Assistant page, never by an imported file."
AI_ON_REJECTED = "AI can only be switched on in the AI Assistant page, never by an imported file."


def coerce(defn: SettingDef, value: Any) -> Any:
    k = defn.key
    if defn.kind == "bool":
        if isinstance(value, bool):
            return value
        raise SettingError(f"'{k}' must be true or false.")
    if defn.kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingError(f"'{k}' must be a number.")
        if isinstance(value, float) and not math.isfinite(value):
            raise SettingError(f"'{k}' must be a finite number.")
        if defn.kind == "int":
            if isinstance(value, float) and not value.is_integer():
                raise SettingError(f"'{k}' must be a whole number.")
            value = int(value)
        else:
            value = float(value)
        if defn.minimum is not None and value < defn.minimum:
            raise SettingError(f"'{k}' must be at least {defn.minimum:g}.")
        if defn.maximum is not None and value > defn.maximum:
            raise SettingError(f"'{k}' must be at most {defn.maximum:g}.")
        return value
    if defn.kind == "text":
        if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 for c in value):
            raise SettingError(f"'{k}' must be short plain text.")
        if k == "ai_proxy_host" and value.strip():
            from ..ai.proxy import ProxyConfigError, validate_host
            try:
                return validate_host(value)
            except ProxyConfigError as exc:
                raise SettingError(str(exc)) from None
        return value
    if defn.kind == "choice":
        for choice in defn.choices:
            if value == choice and type(value) is type(choice):
                return choice
        raise SettingError(f"'{k}' must be one of {list(defn.choices)}.")
    raise SettingError(f"Unsupported setting type for '{k}'.")


PRESETS: dict[str, dict[str, Any]] = {
    "sentinel_core": dict(animation_intensity="medium", particle_intensity="medium", pulse_intensity="medium",
                          emblem_size=100, show_overlay=True, overlay_mode="compact", dimmer_percent=0,
                          reduced_motion=False, show_clock=True),
    "crimson_watch": dict(animation_intensity="high", particle_intensity="high", pulse_intensity="high",
                          emblem_size=115, show_overlay=True, overlay_mode="expanded", dimmer_percent=0,
                          reduced_motion=False, show_clock=True),
    "silent_guardian": dict(animation_intensity="low", particle_intensity="off", pulse_intensity="low",
                            emblem_size=85, show_overlay=False, overlay_mode="compact", dimmer_percent=35,
                            reduced_motion=True, show_clock=False),
}
PRESET_LABELS = {"sentinel_core": "Sentinel Core", "crimson_watch": "Crimson Watch",
                 "silent_guardian": "Silent Guardian", "custom": "Custom"}
PRESET_KEYS = tuple(PRESETS["sentinel_core"])

Listener = Callable[[str, Any], None]


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: dict[str, Any] = {}
        self._listeners: list[Listener] = []
        self.load_report: dict[str, list[str]] = {"invalid": [], "unknown": [], "missing_v2": [],
                                                  "missing_v3": [], "missing_v4": [], "missing_v5": []}
        self._load()

    def _load(self) -> None:
        """Invalid or unknown stored values never crash startup: defaults are used and the
        problem is recorded in ``load_report`` (and shown in the audit log by the controller)."""
        seen = set()
        for row in self._db.query("SELECT key, value FROM settings"):
            seen.add(row["key"])
            defn = DEFS_BY_KEY.get(row["key"])
            if defn is None:
                self.load_report["unknown"].append(row["key"])
                continue
            try:
                self._cache[defn.key] = coerce(defn, json.loads(row["value"]))
            except (ValueError, TypeError):
                self.load_report["invalid"].append(row["key"])
                log.warning("ignoring invalid stored value for setting %s", row["key"])
        v2 = [d.key for d in DEFINITIONS[17:V2_END]]
        self.load_report["missing_v3"] = [d.key for d in DEFINITIONS[V2_END:V3_END] if d.key not in seen]
        self.load_report["missing_v4"] = [d.key for d in DEFINITIONS[V3_END:V4_END] if d.key not in seen]
        self.load_report["missing_v5"] = [d.key for d in DEFINITIONS[V4_END:] if d.key not in seen]
        self.load_report["missing_v2"] = [k for k in v2 if k not in seen]   # V1 database: defaults apply

    def apply_preset(self, name: str) -> None:
        if name not in PRESETS:
            raise SettingError(f"Unknown preset '{name}'.")
        for k, v in PRESETS[name].items():
            self.set(k, v)
        self.set("appearance_preset", name)

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def get(self, key: str) -> Any:
        defn = DEFS_BY_KEY.get(key)
        if defn is None:
            raise SettingError(f"Unknown setting '{key}'.")
        return self._cache.get(key, defn.default)

    def all(self) -> dict[str, Any]:
        return {d.key: self.get(d.key) for d in DEFINITIONS}

    def set(self, key: str, value: Any) -> Any:
        defn = DEFS_BY_KEY.get(key)
        if defn is None:
            raise SettingError(f"Unknown setting '{key}'.")
        clean = coerce(defn, value)
        changed = self.get(key) != clean or key not in self._cache
        self._db.execute(
            "INSERT INTO settings(key, value, updated_ts) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ts=excluded.updated_ts",
            (key, json.dumps(clean), time.time()),
        )
        self._cache[key] = clean
        if changed:
            self._notify(key, clean)
        return clean

    def set_appearance(self, key: str, value: Any) -> Any:
        """Used by the Settings UI: a manual change to a preset-controlled key leaves the named preset."""
        clean = self.set(key, value)
        if key in PRESET_KEYS and self.get("appearance_preset") != "custom":
            self.set("appearance_preset", "custom")
        return clean

    def reset(self, key: str | None = None) -> None:
        keys = [key] if key else [d.key for d in DEFINITIONS]
        for k in keys:
            if k not in DEFS_BY_KEY:
                raise SettingError(f"Unknown setting '{k}'.")
            self._db.execute("DELETE FROM settings WHERE key=?", (k,))
            self._cache.pop(k, None)
            self._notify(k, DEFS_BY_KEY[k].default)

    def _notify(self, key: str, value: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(key, value)
            except Exception:  # a UI listener bug must not break storage
                log.exception("settings listener failed for %s", key)

    # -- import / export (the sample config file uses this format) -------------
    def export_json(self, path: str | Path) -> Path:
        path = Path(path)
        payload = {"format": "vyrxone-sentinel-settings", "schema": 2, "settings": self.all()}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def import_json(self, path: str | Path) -> tuple[list[str], dict[str, str]]:
        """Apply valid values from a JSON file. Returns (applied_keys, rejected{key: reason})."""
        path = Path(path)
        try:
            if path.stat().st_size > MAX_IMPORT_BYTES:
                raise SettingError("Settings file is unexpectedly large.")
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SettingError(f"Could not read settings file: {exc}") from exc
        if isinstance(data, dict) and isinstance(data.get("settings"), dict):
            data = data["settings"]
        if not isinstance(data, dict):
            raise SettingError("Settings file must contain a JSON object.")
        applied: list[str] = []
        rejected: dict[str, str] = {}
        for key, value in data.items():
            if key not in DEFS_BY_KEY:
                if key not in ("format", "schema", "_comment"):
                    rejected[key] = "unknown setting"
                continue
            if key == "ai_enabled" and value is True:        # an imported file can switch AI off, never on
                rejected[key] = AI_ON_REJECTED
                continue
            if key == "ai_proxy_enabled" and value is True:  # nor can it route traffic through a chosen proxy
                rejected[key] = PROXY_ON_REJECTED
                continue
            try:
                self.set(key, value)
                applied.append(key)
            except SettingError as exc:
                rejected[key] = str(exc)
        return applied, rejected
