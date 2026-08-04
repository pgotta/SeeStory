"""Local image-generation entry point.

SeeStory intentionally has one image path: local diffusion generation. The UI
does not expose implementation choices, and generation failures are surfaced
instead of silently substituting different artwork.
"""

import sys

from . import stablediffusion
from ..director import _scrub


def probe() -> dict:
    """Small readiness snapshot used by the launcher and diagnostics."""
    return {
        "ready": stablediffusion.is_available(),
        "cuda": stablediffusion.has_cuda(),
        "model": stablediffusion.DEFAULT_MODEL,
    }


def generate_for(shot, out_path: str, *, sd_opts: dict | None = None) -> str:
    """Generate one storyboard image locally and return its output path."""
    sd_opts = sd_opts or {}
    prompt = _scrub(getattr(shot, "prompt", "")) or getattr(shot, "prompt", "")
    try:
        return stablediffusion.generate(prompt, out_path, **sd_opts)
    except Exception as exc:
        sys.stderr.write(
            f"[seestory] local image generation failed for shot "
            f"{getattr(shot, 'id', '?')}: {exc}\n"
        )
        sys.stderr.flush()
        raise
