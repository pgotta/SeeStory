"""Image-generation router: pick a backend per shot, fall back gracefully."""

import sys

from . import placeholder, stablediffusion, copilot_backend
from ..director import _scrub

# fallback order if a backend is unavailable or errors on a given shot
_FALLBACK = {
    "copilot": ["copilot", "stablediffusion", "placeholder"],
    "stablediffusion": ["stablediffusion", "placeholder"],
    "placeholder": ["placeholder"],
}

_LABEL = {"copilot": "copilot", "stablediffusion": "stable diffusion",
          "placeholder": "placeholder"}


def probe() -> dict:
    return {
        "stablediffusion": stablediffusion.is_available(),
        "cuda": stablediffusion.has_cuda(),
        "copilot": copilot_backend.is_available(),
        "copilot_signed_in": copilot_backend.is_signed_in(),
        "copilot_remaining": copilot_backend.remaining(),
    }


def generate_for(shot, out_path: str, *, sd_opts: dict = None) -> dict:
    """Generate one shot's image. Returns {'backend': used, 'note': str}."""
    sd_opts = sd_opts or {}
    # Scrub text-inducing / filter-tripping tokens even from already-stored
    # prompts (e.g. an old storyboard built with a "…dan brown book" style),
    # so regenerating a shot benefits from the fix too.
    prompt = _scrub(shot.prompt) or shot.prompt
    chain = _FALLBACK.get(shot.backend, ["placeholder"])
    last_err = ""
    for backend in chain:
        try:
            if backend == "copilot":
                copilot_backend.generate(prompt, out_path)
            elif backend == "stablediffusion":
                stablediffusion.generate(prompt, out_path, **sd_opts)
            else:
                placeholder.generate(prompt, out_path,
                                     label=_LABEL.get(shot.backend, "scene"))
            note = "" if backend == shot.backend else \
                f"{shot.backend} unavailable → {backend}"
            # carry the real reason so it's visible in the browser build log
            if note and last_err:
                note += f" — {last_err}"
            return {"backend": backend, "note": note, "error": last_err}
        except Exception as e:
            last_err = str(e)
            # Surface the reason on the console (→ seestory.log) so a backend
            # silently falling back (e.g. Copilot) is never invisible again.
            sys.stderr.write(
                f"[seestory] {backend} failed for shot "
                f"{getattr(shot, 'id', '?')}: {e}\n")
            sys.stderr.flush()
            continue
    # placeholder is last resort and shouldn't fail, but just in case:
    placeholder.generate(shot.prompt or "scene", out_path)
    return {"backend": "placeholder", "note": "all backends failed",
            "error": last_err}
