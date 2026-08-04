#!/usr/bin/env python3
"""Install and GPU-verify SeeStory's local image models.

This script is intentionally run by install_all.bat, not by the Flask app.
Runtime image generation is offline-only: if a required model is missing or
incomplete, SeeStory tells the user to repair the installation instead of
starting a multi-gigabyte download from the browser window.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "model-install.log"


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return bool(self.streams and getattr(self.streams[0], "isatty", lambda: False)())


def _enable_logging():
    log = LOG_FILE.open("a", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.__stdout__, log)
    sys.stderr = _Tee(sys.__stderr__, log)
    return log


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download, warm-load, and GPU-test SeeStory image models."
    )
    parser.add_argument(
        "--model",
        choices=("default", "photoreal", "all"),
        default="default",
        help="model set to install and verify",
    )
    args = parser.parse_args()

    # The installer is the only process allowed to use the network for models.
    # Clear any offline flags inherited from a previous shell/session.
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "900")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "60")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    original_stdout, original_stderr = sys.stdout, sys.stderr
    log = _enable_logging()
    try:
        import torch
        from app.imagegen import stablediffusion as sd

        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA is not available. SeeStory will not install or test the "
                "image model on CPU. Check the NVIDIA driver and CUDA PyTorch."
            )

        print("\n" + "=" * 68)
        print(" SEESTORY MODEL INSTALL / GPU VERIFICATION")
        print("=" * 68)
        print(f"PyTorch: {torch.__version__}")
        print(f"CUDA build: {torch.version.cuda}")
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"Log: {LOG_FILE}")

        requested = []
        if args.model in ("default", "all"):
            requested.append(("default", sd.DEFAULT_MODEL))
        if args.model in ("photoreal", "all"):
            requested.append(("photoreal", sd.PHOTOREAL_MODEL))

        for label, model_id in requested:
            print("\n" + "-" * 68)
            print(f"Installing {label} model: {model_id}")
            print("A partial download is resumed. A complete cache is reused.")
            smoke = LOG_DIR / f"model-smoke-{label}.jpg"
            result = sd.install_and_verify_model(model_id, smoke)
            print(f"Model ready: {model_id}")
            print(f"Real CUDA inference passed; peak GPU memory: {result['peak_vram_mb']:.0f} MB")
            print(f"Smoke-test image: {smoke}")
            sd.free()

        print("\nAll requested models are installed and GPU-verified.")
        return 0
    except Exception as exc:
        print(f"\nMODEL INSTALL FAILED: {exc}", file=sys.stderr)
        print(f"See the log at: {LOG_FILE}", file=sys.stderr)
        return 1
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
