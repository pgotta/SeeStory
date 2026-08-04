"""Windows desktop runtime support for SeeStory.

This module is intentionally separate from the audiobook/video pipeline. It only
handles desktop lifecycle, diagnostics, Windows performance settings, and the
small system-monitor endpoint used by the web UI.
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from flask import jsonify, request

_BASE = Path.cwd()
_PORT = 5001
_DESKTOP = os.environ.get("SEESTORY_DESKTOP_SESSION") == "1"
_TOKEN = os.environ.get("SEESTORY_SESSION_TOKEN", "")
_STARTED = time.time()
_LAST_HEARTBEAT = 0.0
_HEARTBEAT_SEEN = False
_SHUTTING_DOWN = False
_LOCK = threading.Lock()
_CPU_LAST: tuple[int, int] | None = None
_GPU_LAST = 0.0
_GPU_CACHE: dict[str, Any] = {"available": False}


def _runtime_dir() -> Path:
    path = Path(os.environ.get("SEESTORY_RUNTIME_DIR", str(_BASE / "runtime")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _logs_dir() -> Path:
    path = _BASE / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _event(name: str, **details: Any) -> None:
    payload = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": name,
        "pid": os.getpid(),
        **details,
    }
    try:
        with (_logs_dir() / "desktop_runtime.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _write_session(state: str = "running", **extra: Any) -> None:
    payload = {
        "pid": os.getpid(),
        "port": _PORT,
        "token": _TOKEN,
        "desktop": _DESKTOP,
        "state": state,
        "started": _STARTED,
        "updated": time.time(),
        **extra,
    }
    try:
        path = _runtime_dir() / "seestory-session.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _is_local_request() -> bool:
    return request.remote_addr in {"127.0.0.1", "::1", None}


def _token_matches() -> bool:
    supplied = request.headers.get("X-SeeStory-Token", "")
    if not supplied and request.is_json:
        supplied = (request.get_json(silent=True) or {}).get("token", "")
    return bool(_TOKEN) and supplied == _TOKEN


def request_shutdown(reason: str) -> bool:
    global _SHUTTING_DOWN
    with _LOCK:
        if _SHUTTING_DOWN:
            return False
        _SHUTTING_DOWN = True
    _event("shutdown_requested", reason=reason)
    _write_session("stopping", reason=reason)

    def terminate() -> None:
        time.sleep(0.45)
        try:
            # This mirrors stop.bat semantics: end the local Flask process and
            # all of its worker threads immediately after state is persisted.
            os._exit(0)
        except Exception:
            os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=terminate, name="SeeStoryShutdown", daemon=True).start()
    return True


def install(app, *, base_dir: str, port: int) -> None:
    """Register desktop-only support routes without changing app behavior."""
    global _BASE, _PORT
    _BASE = Path(base_dir)
    _PORT = int(port)

    @app.post("/api/desktop/heartbeat")
    def desktop_heartbeat():
        global _LAST_HEARTBEAT, _HEARTBEAT_SEEN
        if not _is_local_request():
            return jsonify({"ok": False}), 403
        _LAST_HEARTBEAT = time.monotonic()
        _HEARTBEAT_SEEN = True
        return jsonify({"ok": True, "desktop": _DESKTOP})

    @app.post("/api/desktop/shutdown")
    def desktop_shutdown():
        if not _is_local_request() or not _token_matches():
            return jsonify({"ok": False}), 403
        body = request.get_json(silent=True) or {}
        reason = str(body.get("reason") or "desktop_window_closed")[:80]
        changed = request_shutdown(reason)
        return jsonify({"ok": True, "already_stopping": not changed})

    @app.get("/api/system")
    def desktop_system():
        if not _is_local_request():
            return jsonify({"ok": False}), 403
        return jsonify(system_snapshot())


def start() -> None:
    """Apply Windows performance safeguards and start lifecycle diagnostics."""
    apply_windows_performance_mode()
    snap = system_snapshot()
    _event("startup", desktop=_DESKTOP, port=_PORT, system=snap)
    _write_session("running", system=snap)
    if _DESKTOP:
        threading.Thread(target=_watchdog, name="SeeStoryHeartbeat", daemon=True).start()


def _watchdog() -> None:
    # The tracked browser process normally requests shutdown immediately. This
    # heartbeat is a second line of defense for browser crashes and odd Chrome
    # process hand-offs. It intentionally waits for the first successful page
    # heartbeat, so slow startup or a missing browser does not kill the server.
    while not _SHUTTING_DOWN:
        time.sleep(1.0)
        if _HEARTBEAT_SEEN and time.monotonic() - _LAST_HEARTBEAT > 12.0:
            _event("heartbeat_timeout", seconds=round(time.monotonic() - _LAST_HEARTBEAT, 1))
            request_shutdown("browser_heartbeat_lost")
            return


def apply_windows_performance_mode() -> None:
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetCurrentProcess()
        # HIGH_PRIORITY_CLASS. This is the same intent as the prior launcher but
        # no console window needs to remain focused for it to stay active.
        kernel32.SetPriorityClass(handle, 0x00000080)

        # Keep Windows from applying execution-speed power throttling when the
        # app window is covered or unfocused.
        class POWER_THROTTLING_STATE(ctypes.Structure):
            _fields_ = [
                ("Version", ctypes.c_uint32),
                ("ControlMask", ctypes.c_uint32),
                ("StateMask", ctypes.c_uint32),
            ]

        state = POWER_THROTTLING_STATE(1, 0x1, 0)
        kernel32.SetProcessInformation(
            handle, 4, ctypes.byref(state), ctypes.sizeof(state)
        )

        # Keep the machine awake while a long book is generating. Windows
        # automatically clears this when the SeeStory process exits.
        kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    except Exception as exc:
        _event("performance_mode_warning", error=str(exc))


def _windows_cpu_percent() -> float | None:
    global _CPU_LAST
    if os.name != "nt":
        try:
            load = os.getloadavg()[0]
            return round(min(100.0, load * 100.0 / max(1, os.cpu_count() or 1)), 1)
        except Exception:
            return None
    try:
        class FILETIME(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )

        def val(ft: FILETIME) -> int:
            return (ft.high << 32) | ft.low

        idle_now = val(idle)
        total_now = val(kernel) + val(user)
        if _CPU_LAST is None:
            _CPU_LAST = (idle_now, total_now)
            return 0.0
        idle_prev, total_prev = _CPU_LAST
        _CPU_LAST = (idle_now, total_now)
        total_delta = max(1, total_now - total_prev)
        busy = total_delta - max(0, idle_now - idle_prev)
        return round(max(0.0, min(100.0, 100.0 * busy / total_delta)), 1)
    except Exception:
        return None


def _windows_memory() -> tuple[float | None, float | None]:
    if os.name != "nt":
        return None, None
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_uint32),
                ("memory_load", ctypes.c_uint32),
                ("total_phys", ctypes.c_uint64),
                ("avail_phys", ctypes.c_uint64),
                ("total_page", ctypes.c_uint64),
                ("avail_page", ctypes.c_uint64),
                ("total_virtual", ctypes.c_uint64),
                ("avail_virtual", ctypes.c_uint64),
                ("avail_extended_virtual", ctypes.c_uint64),
            ]

        stat = MEMORYSTATUSEX()
        stat.length = ctypes.sizeof(stat)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        used_gb = (stat.total_phys - stat.avail_phys) / (1024 ** 3)
        total_gb = stat.total_phys / (1024 ** 3)
        return round(used_gb, 1), round(total_gb, 1)
    except Exception:
        return None, None



def _gpu_snapshot() -> dict[str, Any]:
    """Read GPU stats with a short cache so the UI does not spawn nvidia-smi
    every time multiple browser requests land close together."""
    global _GPU_LAST, _GPU_CACHE
    now = time.monotonic()
    if now - _GPU_LAST < 1.5:
        return dict(_GPU_CACHE)

    cmd = [
        "nvidia-smi",
        "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,name",
        "--format=csv,noheader,nounits",
    ]
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        out = subprocess.check_output(
            cmd, text=True, stderr=subprocess.DEVNULL, timeout=2.0,
            creationflags=flags,
        ).strip().splitlines()
        if not out:
            snap = {"available": False}
        else:
            util, used, total, temp, name = [x.strip() for x in out[0].split(",", 4)]
            snap = {
                "available": True,
                "util": float(util),
                "memory_used_mb": float(used),
                "memory_total_mb": float(total),
                "temperature_c": float(temp),
                "name": name,
            }
    except Exception:
        snap = {"available": False}
    _GPU_CACHE = snap
    _GPU_LAST = now
    return dict(snap)


def system_snapshot() -> dict[str, Any]:
    ram_used, ram_total = _windows_memory()
    return {
        "cpu_percent": _windows_cpu_percent(),
        "ram_used_gb": ram_used,
        "ram_total_gb": ram_total,
        "gpu": _gpu_snapshot(),
        "pid": os.getpid(),
        "uptime_seconds": round(time.time() - _STARTED),
        "priority": "HIGH" if os.name == "nt" else "normal",
        "desktop_session": _DESKTOP,
    }
