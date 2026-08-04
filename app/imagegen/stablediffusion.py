"""Local Stable Diffusion XL image generation for SeeStory.

SeeStory deliberately uses repositories that publish a complete Diffusers
``fp16``/Safetensors layout.  This matters on the target 8 GB GPU / 16 GB RAM
laptop: Diffusers can stream the component files with low CPU-memory usage and
then offload model components between RAM and CUDA as needed.

* DreamShaper XL Lightning drives every illustrated style, including Cinematic.
* RealVisXL V5 Lightning is used only for the Photorealistic preset.

The former Juggernaut default was removed because its Hub repository currently
mixes a single-file Safetensors checkpoint with ``.bin`` Diffusers components.
Requesting Safetensors through ``from_pretrained`` therefore fails before the
first image is generated.
"""
from __future__ import annotations

import gc
import os
import sys
import threading
from typing import Any

_LOCK = threading.Lock()
_PIPE = None
_PIPE_KEY: str | None = None

# DreamShaper publishes a proper Diffusers fp16/Safetensors layout and is suited
# to both cinematic and painterly illustration.  Keeping one model for all
# illustrated styles also avoids several multi-gigabyte downloads.
DEFAULT_MODEL = os.environ.get(
    "SEESTORY_SD_MODEL", "Lykon/dreamshaper-xl-lightning"
)
ARTISTIC_MODEL = os.environ.get(
    "SEESTORY_SD_ARTISTIC_MODEL", DEFAULT_MODEL
)
PHOTOREAL_MODEL = os.environ.get(
    "SEESTORY_SD_PHOTOREAL_MODEL", "SG161222/RealVisXL_V5.0_Lightning"
)
DEFAULT_W = int(os.environ.get("SEESTORY_SD_W", "1024"))
DEFAULT_H = int(os.environ.get("SEESTORY_SD_H", "576"))

DEFAULT_NEGATIVE = os.environ.get(
    "SEESTORY_SD_NEGATIVE",
    "text, words, letters, title, book cover, captions, subtitle, watermark, "
    "signature, logo, frame, border, low quality, blurry, bad anatomy, bad "
    "proportions, deformed body, disfigured, mutation, malformed hands, bad "
    "hands, fused fingers, extra fingers, missing fingers, extra arms, extra "
    "legs, extra limbs, missing limbs, duplicate limbs, cloned body, duplicate "
    "person, extra face, extra head, deformed face, asymmetrical eyes, deformed "
    "eyes, deformed mouth",
)


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


def _cuda_total_gb() -> float:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    except Exception:
        pass
    return 0.0


def _lightning(model: str) -> bool:
    low = model.lower()
    return "lightning" in low or "turbo" in low or "lcm" in low


def _generation_defaults(model: str) -> tuple[int, float]:
    low = model.lower()
    if "realvisxl" in low and "lightning" in low:
        return 5, 1.8
    if "dreamshaper" in low and "lightning" in low:
        return 4, 2.0
    if _lightning(model):
        return 6, 2.0
    return 30, 6.0


def _empty_cache() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            try:
                torch.cuda.ipc_collect()
            except Exception:
                pass
    except Exception:
        pass


def _discard_pipe() -> None:
    global _PIPE, _PIPE_KEY
    _PIPE = None
    _PIPE_KEY = None
    gc.collect()
    _empty_cache()


def _from_pretrained(
    model: str, kwargs: dict[str, Any], *, allow_download: bool = False
):
    """Load a model from cache, optionally allowing the installer to download it.

    Flask/runtime calls always use ``allow_download=False``. Only
    ``install_models.py`` opts into network access, keeping multi-gigabyte model
    transfers out of the desktop app and making missing-model failures immediate
    and actionable.
    """
    from diffusers import AutoPipelineForText2Image

    errors: list[Exception] = []
    variants = [dict(kwargs)]
    if "variant" in kwargs:
        without_variant = dict(kwargs)
        without_variant.pop("variant", None)
        variants.append(without_variant)

    local_modes = (True, False) if allow_download else (True,)
    for local_only in local_modes:
        for attempt in variants:
            try:
                return AutoPipelineForText2Image.from_pretrained(
                    model, local_files_only=local_only, **attempt
                )
            except Exception as exc:
                errors.append(exc)

    detail = errors[-1] if errors else "unknown model-loading error"
    if not allow_download:
        kind = "Photorealistic" if model == PHOTOREAL_MODEL else "Default"
        raise RuntimeError(
            f"{kind} image model '{model}' is not installed or its cache is "
            "incomplete. Close SeeStory and run install_all.bat again. "
            "For the Photorealistic style, choose Yes when the installer asks "
            f"whether to install that optional model. Technical detail: {detail}"
        )
    raise RuntimeError(str(detail))


def _load(model: str, *, allow_download: bool = False):
    global _PIPE, _PIPE_KEY
    if _PIPE is not None and _PIPE_KEY == model:
        return _PIPE
    if _PIPE is not None:
        _discard_pipe()

    import torch
    from diffusers import DPMSolverMultistepScheduler

    use_cuda = torch.cuda.is_available()
    dtype = torch.float16 if use_cuda else torch.float32
    kwargs: dict[str, Any] = {
        "torch_dtype": dtype,
        "use_safetensors": True,
        "low_cpu_mem_usage": True,
    }
    if use_cuda:
        kwargs["variant"] = "fp16"

    print(f"[seestory] loading local image model: {model}", file=sys.stderr)
    pipe = _from_pretrained(model, kwargs, allow_download=allow_download)

    try:
        low_model = model.lower()
        if "realvisxl" in low_model:
            pipe.scheduler = DPMSolverMultistepScheduler.from_config(
                pipe.scheduler.config,
                algorithm_type="sde-dpmsolver++",
                use_karras_sigmas=True,
            )
        else:
            pipe.scheduler = DPMSolverMultistepScheduler.from_config(
                pipe.scheduler.config
            )
    except Exception:
        pass

    if use_cuda:
        try:
            pipe.enable_attention_slicing()
        except Exception:
            pass
        try:
            pipe.enable_vae_slicing()
            pipe.enable_vae_tiling()
        except Exception:
            pass

        # The user's RTX 5060 laptop has 8 GB VRAM. CPU offload is slower than
        # placing the whole pipeline on CUDA, but prevents immediate OOM failures.
        if _cuda_total_gb() <= 10.5:
            try:
                pipe.enable_model_cpu_offload()
            except Exception:
                pipe = pipe.to("cuda")
        else:
            pipe = pipe.to("cuda")

    try:
        pipe.set_progress_bar_config(disable=True)
    except Exception:
        pass

    _PIPE, _PIPE_KEY = pipe, model
    return pipe


def free() -> None:
    """Release the current pipeline and as much VRAM/RAM as possible."""
    with _LOCK:
        _discard_pipe()


def install_and_verify_model(model: str, smoke_path: str | os.PathLike[str]) -> dict[str, float]:
    """Download/resume, warm-load, and prove real CUDA inference for a model.

    This is installer-only. The normal Flask path never passes
    ``allow_download=True`` and is additionally launched in Hugging Face offline
    mode.
    """
    if not has_cuda():
        raise RuntimeError("CUDA is required for SeeStory model installation.")

    import torch

    with _LOCK:
        pipe = _load(model, allow_download=True)
        _empty_cache()
        try:
            torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass

        steps, guidance = _generation_defaults(model)
        generator = torch.Generator(device="cpu").manual_seed(20260802)
        prompt = (
            "a single open storybook beside a warm reading lamp, cinematic "
            "illustration, one coherent scene, detailed, no people, no text"
        )
        with torch.inference_mode():
            result = pipe(
                prompt=prompt,
                negative_prompt=DEFAULT_NEGATIVE,
                num_inference_steps=steps,
                guidance_scale=guidance,
                height=288,
                width=512,
                generator=generator,
            )
        image = result.images[0]
        smoke = os.fspath(smoke_path)
        os.makedirs(os.path.dirname(os.path.abspath(smoke)), exist_ok=True)
        image.save(smoke, "JPEG", quality=90)
        peak_mb = torch.cuda.max_memory_allocated() / (1024 ** 2)
        if peak_mb < 32:
            raise RuntimeError(
                f"Inference returned an image but CUDA use was not proven "
                f"(peak allocation only {peak_mb:.1f} MB)."
            )
        return {"peak_vram_mb": float(peak_mb)}


def generate(
    prompt: str,
    out_path: str,
    *,
    model: str | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    negative_prompt: str | None = None,
    w: int | None = None,
    h: int | None = None,
    seed: int | None = None,
    **_: Any,
) -> str:
    if not is_available():
        raise RuntimeError(
            "Local image generation is unavailable. Run install_all.bat to "
            "install CUDA PyTorch and the image-generation components."
        )

    model = model or DEFAULT_MODEL
    w = int(w or DEFAULT_W)
    h = int(h or DEFAULT_H)
    neg = DEFAULT_NEGATIVE if negative_prompt is None else negative_prompt
    default_steps, default_guidance = _generation_defaults(model)
    steps = int(steps or default_steps)
    guidance = float(default_guidance if guidance is None else guidance)

    with _LOCK:
        try:
            pipe = _load(model, allow_download=False)
        except Exception as exc:
            raise RuntimeError(f"Could not load image model '{model}': {exc}") from exc

        import torch
        generator = None
        if seed is not None:
            generator = torch.Generator(device="cpu").manual_seed(int(seed))

        sizes: list[tuple[int, int]] = [(w, h)]
        for fw, fh in ((896, 504), (768, 432), (640, 360)):
            if fw < w and (fw, fh) not in sizes:
                sizes.append((fw, fh))

        last_err: Exception | None = None
        image = None
        for tw, th in sizes:
            try:
                with torch.inference_mode():
                    result = pipe(
                        prompt=prompt,
                        negative_prompt=neg,
                        num_inference_steps=steps,
                        guidance_scale=guidance,
                        height=th,
                        width=tw,
                        generator=generator,
                    )
                image = result.images[0]
                break
            except torch.cuda.OutOfMemoryError as exc:
                last_err = exc
                _empty_cache()
            except RuntimeError as exc:
                if "out of memory" not in str(exc).lower():
                    raise
                last_err = exc
                _empty_cache()

        if image is None:
            raise RuntimeError(
                f"GPU memory was exhausted while generating: {last_err}"
            )

    image.save(out_path, "JPEG", quality=94, optimize=True)
    _empty_cache()
    return out_path
