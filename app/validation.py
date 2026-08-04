"""Small validation/path helpers shared by the Flask layer and unit tests."""
from __future__ import annotations

import os
import tempfile
from typing import Mapping


def safe_session_dir(output_root: str, job: str | None) -> str | None:
    """Return a direct child session folder, rejecting traversal/reserved names."""
    raw = str(job or "")
    name = os.path.basename(os.path.normpath(raw))
    if raw != name or not name or name.startswith("_") or name.startswith("."):
        return None
    root = os.path.abspath(output_root)
    candidate = os.path.abspath(os.path.join(root, name))
    if os.path.dirname(candidate) != root:
        return None
    return candidate if os.path.isdir(candidate) else None


def temp_upload_path(uploads_root: str, prefix: str, filename: str | None) -> str:
    """Create a unique closed scratch file path suitable for Flask FileStorage.save."""
    suffix = os.path.splitext(filename or "")[1].lower()[:12]
    fd, path = tempfile.mkstemp(prefix=f"{prefix}-", suffix=suffix, dir=uploads_root)
    os.close(fd)
    return path


def clean_motion(raw, defaults: Mapping[str, object]) -> dict:
    """Validate/clamp the motion schema while preserving supplied defaults."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(defaults)
    if raw.get("zoom") in {"none", "in", "out"}:
        out["zoom"] = raw["zoom"]
    if raw.get("pan") in {"none", "left", "right", "up", "down"}:
        out["pan"] = raw["pan"]
    limits = {
        "intensity": (0.0, 100.0), "speed": (0.0, 100.0),
        "fade_in": (0.0, 2.5), "fade_out": (0.0, 2.5),
        "opacity": (20.0, 100.0),
    }
    for key, (lo, hi) in limits.items():
        if key not in raw:
            continue
        try:
            value = max(lo, min(hi, float(raw[key])))
            out[key] = int(value) if key in {"intensity", "speed", "opacity"} else value
        except (TypeError, ValueError):
            pass
    return out
