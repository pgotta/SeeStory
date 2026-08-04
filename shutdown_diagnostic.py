"""Create a readable shutdown diagnostic without changing or stopping SeeStory."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAMP = time.strftime("%Y%m%d-%H%M%S")
OUT = ROOT / f"SeeStory-shutdown-diagnostic-{STAMP}.txt"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def run(command: list[str], timeout: int = 12) -> str:
    try:
        return subprocess.check_output(
            command, text=True, errors="replace", stderr=subprocess.STDOUT,
            timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        return f"<command failed: {exc}>"


def tail(path: Path, lines: int = 200) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(data[-lines:]) or "<empty>"
    except Exception as exc:
        return f"<unavailable: {exc}>"


def section(fh, title: str, body: str) -> None:
    fh.write(f"\n{'=' * 72}\n{title}\n{'=' * 72}\n{body.rstrip()}\n")


def main() -> int:
    with OUT.open("w", encoding="utf-8") as fh:
        fh.write("SeeStory shutdown diagnostic\n")
        fh.write(f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"Root: {ROOT}\n")
        fh.write(f"Python: {sys.executable}\n")
        fh.write(f"Platform: {platform.platform()}\n")
        section(fh, "SESSION STATE", tail(ROOT / "runtime" / "seestory-session.json", 100))
        section(fh, "PORT 5001", run(["netstat", "-ano", "-p", "tcp"]))
        section(fh, "RELEVANT PROCESSES", run(["tasklist", "/V"]))
        section(fh, "NVIDIA GPU", run(["nvidia-smi"]))
        section(fh, "LAUNCHER LOG", tail(ROOT / "logs" / "launcher.log"))
        section(fh, "DESKTOP RUNTIME LOG", tail(ROOT / "logs" / "desktop_runtime.log"))
        section(fh, "SEESTORY SERVER LOG", tail(ROOT / "logs" / "seestory.log", 300))
        section(fh, "LEGACY SERVER LOG", tail(ROOT / "seestory.log", 200))
    print(f"Diagnostic saved to:\n{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
