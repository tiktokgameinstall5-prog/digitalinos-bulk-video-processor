"""Persist user presets (watermark + upscale settings) as JSON on disk.

Presets are stored as JSON files whose file *names* are sanitized, but whose
`name` field inside the JSON body preserves the user-supplied display name.
`list_presets()` returns display names; `load_preset(name)` resolves a display
name back to its file by matching the stored `name` field (case-insensitive,
falls back to the sanitized filename for backwards compatibility).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from ..processing.ffmpeg_handler import WatermarkSettings
from ..processing.upscale_handler import UpscaleOptions


def default_presets_dir() -> str:
    """Return the directory where presets are stored.

    Respects `VIDEO_BATCH_PRO_PRESETS_DIR` for tests and advanced users.
    """
    env = os.environ.get("VIDEO_BATCH_PRO_PRESETS_DIR")
    if env:
        return env
    return os.path.join(str(Path.home()), ".video_batch_pro", "presets")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _sanitize(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name).strip("._")
    return safe or "preset"


def _resolve_preset_file(name: str, directory: str) -> Optional[str]:
    """Find the JSON file whose stored display name matches `name`.

    Matching is done on: (1) exact stored `name`, (2) case-insensitive stored
    name, (3) sanitized filename. Returns None if not found.
    """
    if not os.path.isdir(directory):
        return None
    target_lower = name.lower()
    sanitized_match = os.path.join(directory, f"{_sanitize(name)}.json")
    for entry in sorted(os.listdir(directory)):
        if not entry.endswith(".json"):
            continue
        path = os.path.join(directory, entry)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        stored = str(payload.get("name", "")).strip()
        if stored == name or stored.lower() == target_lower:
            return path
    if os.path.isfile(sanitized_match):
        return sanitized_match
    return None


def save_preset(
    name: str,
    watermark: WatermarkSettings,
    upscale: UpscaleOptions,
    directory: str | None = None,
) -> str:
    """Serialize and write a preset to disk. Returns the written path."""
    directory = directory or default_presets_dir()
    _ensure_dir(directory)
    payload: dict[str, Any] = {
        "name": name,
        "watermark": asdict(watermark),
        "upscale": asdict(upscale),
    }
    path = os.path.join(directory, f"{_sanitize(name)}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return path


def load_preset(
    name: str,
    directory: str | None = None,
) -> tuple[WatermarkSettings, UpscaleOptions]:
    directory = directory or default_presets_dir()
    path = _resolve_preset_file(name, directory)
    if not path:
        raise FileNotFoundError(f"Preset not found: {name!r}")
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    wm = WatermarkSettings(**payload.get("watermark", {}))
    up = UpscaleOptions(**payload.get("upscale", {}))
    return wm, up


def list_presets(directory: str | None = None) -> list[str]:
    """Return display names of all presets in `directory`."""
    directory = directory or default_presets_dir()
    if not os.path.isdir(directory):
        return []
    names: list[str] = []
    for entry in sorted(os.listdir(directory)):
        if not entry.endswith(".json"):
            continue
        path = os.path.join(directory, entry)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            stored = str(payload.get("name", "")).strip()
            names.append(stored or entry[:-5])
        except (OSError, json.JSONDecodeError):
            names.append(entry[:-5])
    return names


def delete_preset(name: str, directory: str | None = None) -> bool:
    directory = directory or default_presets_dir()
    path = _resolve_preset_file(name, directory)
    if path and os.path.isfile(path):
        os.remove(path)
        return True
    return False
