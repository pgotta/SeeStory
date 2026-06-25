"""
Local Stable Diffusion backend (the every-page workhorse).

Runs on your own GPU via 🤗 diffusers — free, private, no account, and fast
enough to illustrate a whole book. Defaults target an 8 GB laptop card (fp16,
attention slicing, VAE tiling) and a turbo model so each image is a few steps.

The pipeline is loaded once, lazily, on first use. If torch/diffusers or the
model aren't available, generate() raises a clear error and the router falls
back to the placeholder backend so the app keeps working.
"""

import os
import sys
import threading

_LOCK = threading.Lock()
_PIPE = None
_PIPE_KEY = None

# Override via environment (set in run.bat or the UI later).
DEFAULT_MODEL = os.environ.get("SEESTORY_SD_MODEL", "stabilityai/sdxl-turbo")
# Photorealistic model used when the "Photorealistic" art style is chosen. It's a
# Lightning checkpoint, so it stays in the fast few-steps path. Override with
# SEESTORY_SD_PHOTOREAL_MODEL (any diffusers-compatible SDXL repo).
PHOTOREAL_MODEL = os.environ.get("SEESTORY_SD_PHOTOREAL_MODEL",
                                 "SG161222/RealVisXL_V4.0_Lightning")
DEFAULT_W = int(os.environ.get("SEESTORY_SD_W", "1024"))
DEFAULT_H = int(os.environ.get("SEESTORY_SD_H", "576"))

# Things we never want IN the picture. These go in the model's negative prompt
# (not tacked onto the positive prompt, where CLIP would truncate them on long
# prompts). Override with SEESTORY_SD_NEGATIVE.
DEFAULT_NEGATIVE = os.environ.get(
    "SEESTORY_SD_NEGATIVE",
    "text, words, letters, title, book cover, captions, watermark, signature, "
    "logo, frame, border, duplicate, cloned face, two faces, extra face, "
    "extra head, deformed, disfigured, extra limbs, extra fingers, bad anatomy, "
    "blurry, low quality")

# Negative prompts only bite when classifier-free guidance is on (guidance > 0).
# SDXL-Turbo runs at guidance 0 by design, so negatives are inert there. Set
# SEESTORY_SD_GUIDANCE (e.g. 1.5) to force a little guidance and make the
# negative prompt actually suppress text/watermarks on turbo, at some speed cost.
_GUIDANCE_ENV = os.environ.get("SEESTORY_SD_GUIDANCE")


def is_available() -> bool:
    try:
        import torch  # noqa: F401
        import diffusers  # noqa: F401
        return True
    except Exception:
        return False


def has_cuda() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _turbo(model: str) -> bool:
    m = model.lower()
    return "turbo" in m or "lightning" in m or "lcm" in m


def _load(model: str):
    global _PIPE, _PIPE_KEY
    if _PIPE is not None and _PIPE_KEY == model:
        return _PIPE
    import torch
    from diffusers import AutoPipelineForText2Image

    use_cuda = torch.cuda.is_available()
    dtype = torch.float16 if use_cuda else torch.float32
    try:
        pipe = AutoPipelineForText2Image.from_pretrained(
            model, torch_dtype=dtype, variant="fp16" if use_cuda else None,
            use_safetensors=True)
    except Exception:
        # Many community photoreal checkpoints don't ship a separate "fp16"
        # variant — retry without it rather than failing the whole render.
        pipe = AutoPipelineForText2Image.from_pretrained(
            model, torch_dtype=dtype, use_safetensors=True)
    if use_cuda:
        pipe = pipe.to("cuda")
        try:
            pipe.enable_attention_slicing()
            pipe.enable_vae_tiling()
        except Exception:
            pass
        # Frees VRAM on tight (8 GB) cards by streaming weights from CPU.
        try:
            pipe.enable_model_cpu_offload()
        except Exception:
            pass
    try:
        pipe.set_progress_bar_config(disable=True)
    except Exception:
        pass
    _PIPE, _PIPE_KEY = pipe, model
    return pipe


def _empty_cache():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass


def free():
    """Release the pipeline and free VRAM (used on shutdown / memory pressure)."""
    global _PIPE, _PIPE_KEY
    with _LOCK:
        _PIPE, _PIPE_KEY = None, None
        import gc
        gc.collect()
        _empty_cache()


def generate(prompt: str, out_path: str, *, model: str = None,
             steps: int = None, guidance: float = None,
             negative_prompt: str = None,
             w: int = None, h: int = None, seed: int = None, **_) -> str:
    if not is_available():
        raise RuntimeError(
            "Stable Diffusion backend unavailable: install torch + diffusers "
            "(see setup.bat / README). Falling back to placeholder.")
    model = model or DEFAULT_MODEL
    w = w or DEFAULT_W
    h = h or DEFAULT_H
    neg = DEFAULT_NEGATIVE if negative_prompt is None else negative_prompt
    with _LOCK:                      # one generation at a time on the GPU
        try:
            pipe = _load(model)
        except Exception as e:
            # A custom/photoreal checkpoint that can't be fetched or loaded
            # shouldn't doom the whole render to placeholders — fall back to the
            # known-good default model so we still produce a real image.
            if model != DEFAULT_MODEL:
                print(f"[seestory] model '{model}' failed to load ({e}); "
                      f"falling back to {DEFAULT_MODEL}", file=sys.stderr)
                model = DEFAULT_MODEL
                pipe = _load(model)
            else:
                raise
        import torch
        # Resolve guidance: an explicit env override wins; otherwise use the
        # value passed in (from the app's "image cleanup" setting) or the default.
        if _GUIDANCE_ENV:
            try:
                guidance = float(_GUIDANCE_ENV)
            except ValueError:
                pass
        if _turbo(model):
            # Pure turbo runs at guidance 0, where the negative prompt is ignored
            # (text + double-faces slip through). A mild guidance ( >1 ) with a
            # couple more steps lets the negative prompt suppress them, still fast.
            g = 1.6 if guidance is None else guidance
            steps = steps or (4 if g <= 0 else 6)   # classic turbo vs turbo+negatives
            guidance = g
        else:
            steps = steps or 28
            guidance = 7.0 if guidance is None else guidance
        gen = None
        if seed is not None:
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            gen = torch.Generator(device=dev).manual_seed(int(seed))

        # Try at requested size; on out-of-memory, clear the cache and retry at
        # progressively smaller sizes so one heavy image never kills the run.
        sizes = [(int(w), int(h))]
        for fw, fh in ((768, 432), (512, 288)):
            if fw < w:
                sizes.append((fw, fh))
        last_err = None
        img = None
        for (tw, th) in sizes:
            try:
                result = pipe(prompt=prompt, negative_prompt=neg,
                              num_inference_steps=int(steps),
                              guidance_scale=float(guidance), height=th,
                              width=tw, generator=gen)
                img = result.images[0]
                break
            except torch.cuda.OutOfMemoryError as e:
                last_err = e
                _empty_cache()
                continue
            except RuntimeError as e:
                # CUDA OOM sometimes surfaces as a generic RuntimeError.
                if "out of memory" in str(e).lower():
                    last_err = e
                    _empty_cache()
                    continue
                raise
        if img is None:
            _empty_cache()
            raise RuntimeError(f"Stable Diffusion ran out of GPU memory: {last_err}")

    img.save(out_path, "JPEG", quality=92)
    _empty_cache()                  # release VRAM between shots
    return out_path
