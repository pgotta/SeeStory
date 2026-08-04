"""Hidden Windows launcher for SeeStory's desktop app window."""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 5001
URL = f"http://127.0.0.1:{PORT}"
RUNTIME = ROOT / "runtime"
LOGS = ROOT / "logs"
SESSION = RUNTIME / "seestory-session.json"
RUNTIME.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
HIGH_PRIORITY_CLASS = getattr(subprocess, "HIGH_PRIORITY_CLASS", 0x00000080)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

_LAUNCH_MUTEX = None


def acquire_single_instance() -> bool:
    """Prevent accidental double-clicks from starting competing launchers."""
    global _LAUNCH_MUTEX
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        _LAUNCH_MUTEX = kernel32.CreateMutexW(None, False, "Local\\SeeStoryDesktopLauncher")
        if not _LAUNCH_MUTEX:
            return True
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception as exc:
        log(f"Single-instance guard unavailable: {exc}")
        return True


def log(message: str) -> None:
    with (LOGS / "launcher.log").open("a", encoding="utf-8") as fh:
        fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def message_box(text: str, title: str = "SeeStory") -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass


def port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.35):
            return True
    except OSError:
        return False


def read_session() -> dict:
    try:
        return json.loads(SESSION.read_text(encoding="utf-8"))
    except Exception:
        return {}

def listener_pid() -> int:
    """Return the PID listening on SeeStory's port, or 0 if none is found."""
    if os.name != "nt":
        return 0
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            text=True, stderr=subprocess.DEVNULL, timeout=3,
            creationflags=CREATE_NO_WINDOW,
        )
        marker = f":{PORT}"
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" and marker in parts[1] \
                    and parts[3].upper() == "LISTENING":
                try:
                    return int(parts[4])
                except ValueError:
                    continue
    except Exception as exc:
        log(f"Could not identify port owner: {exc}")
    return 0


def request_shutdown(session: dict, reason: str) -> bool:
    token = session.get("token", "")
    if not token:
        return False
    try:
        req = urllib.request.Request(
            f"{URL}/api/desktop/shutdown",
            data=json.dumps({"token": token, "reason": reason}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-SeeStory-Token": token},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2.0).read()
        return True
    except Exception as exc:
        log(f"Shutdown request failed: {exc}")
        return False


def kill_pid(pid: int) -> None:
    if not pid:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW, timeout=6,
        )
    except Exception as exc:
        log(f"taskkill failed for PID {pid}: {exc}")


def stop_stale_server() -> bool:
    """Stop an older SeeStory session without touching an unrelated process.

    A valid SeeStory session file is required before taskkill is used. If some
    unrelated program owns port 5001, startup aborts instead of killing it.
    """
    if not port_open():
        return True
    old = read_session()
    old_pid = int(old.get("pid") or 0)
    owner_pid = listener_pid()
    if not old_pid or not old.get("token") or (owner_pid and owner_pid != old_pid):
        log(
            f"Port {PORT} is occupied but does not match the recorded SeeStory "
            f"session (listener={owner_pid}, session={old_pid})."
        )
        return False
    log(f"Existing SeeStory listener found on port {PORT}; requesting a clean stop.")
    request_shutdown(old, "new_desktop_launch")
    deadline = time.time() + 5
    while port_open() and time.time() < deadline:
        time.sleep(0.25)
    if port_open():
        kill_pid(old_pid)
        deadline = time.time() + 3
        while port_open() and time.time() < deadline:
            time.sleep(0.2)
    return not port_open()


def browser_path() -> Path | None:
    candidates: list[Path] = []
    try:
        import winreg
        for exe in ("chrome.exe", "msedge.exe"):
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                        candidates.append(Path(winreg.QueryValue(key, None)))
                except OSError:
                    pass
    except Exception:
        pass

    for base in filter(None, (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA"))):
        base_path = Path(base)
        candidates.extend([
            base_path / "Google/Chrome/Application/chrome.exe",
            base_path / "Microsoft/Edge/Application/msedge.exe",
        ])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def wait_for_server(proc: subprocess.Popen, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"{URL}/api/health", timeout=1.2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def main() -> int:
    if not acquire_single_instance():
        message_box("SeeStory is already starting or running.", "SeeStory")
        return 0

    pythonw = ROOT / "venv/Scripts/pythonw.exe"
    if not pythonw.exists():
        message_box("SeeStory is not installed yet. Run install_all.bat first.")
        return 1

    if not stop_stale_server():
        message_box(
            f"Port {PORT} is already in use by another program. Close it, then start SeeStory again."
        )
        return 1
    token = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env.update({
        "SEESTORY_DESKTOP_SESSION": "1",
        "SEESTORY_SESSION_TOKEN": token,
        "SEESTORY_RUNTIME_DIR": str(RUNTIME),
        "PYTHONUNBUFFERED": "1",
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "CUDA_MODULE_LOADING": "LAZY",
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
    })

    server_log = (LOGS / "seestory.log").open("a", encoding="utf-8", buffering=1)
    log("Starting hidden SeeStory server in Windows performance mode.")
    try:
        server = subprocess.Popen(
            [str(pythonw), "-m", "app.server", "--no-browser"],
            cwd=str(ROOT), env=env, stdout=server_log, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW | HIGH_PRIORITY_CLASS | CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as exc:
        server_log.close()
        log(f"Server launch failed: {exc}")
        message_box(f"SeeStory could not start.\n\n{exc}\n\nSee logs\\launcher.log")
        return 2

    if not wait_for_server(server):
        code = server.poll()
        log(f"Server never became ready; exit code={code}")
        kill_pid(server.pid)
        server_log.close()
        message_box("SeeStory did not finish starting. Run shutdown_diagnostic.bat and check logs\\seestory.log.")
        return 3

    browser = browser_path()
    if not browser:
        log("Chrome/Edge not found; opening default browser. Heartbeat will manage shutdown.")
        os.startfile(URL)
        while server.poll() is None:
            time.sleep(1)
        server_log.close()
        return 0

    # Reuse one isolated app profile. Creating a brand-new Chrome/Edge profile
    # every launch triggers browser first-run/profile helper processes and can
    # briefly flash many windows. A persistent SeeStory-only profile avoids that
    # churn while still keeping the app separate from the user's normal browser.
    profile = Path(os.environ.get("LOCALAPPDATA", str(RUNTIME))) / "SeeStory" / "BrowserProfile"
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        str(browser),
        f"--app={URL}",
        f"--user-data-dir={profile}",
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-extensions",
        "--disable-sync",
        "--disable-component-update",
        "--disable-background-mode",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--disable-features=CalculateNativeWinOcclusion",
    ]
    log(f"Opening tracked app window with {browser.name}.")
    try:
        app_window = subprocess.Popen(
            args, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        app_window.wait()
        log(f"Desktop app window closed (browser exit={app_window.returncode}).")
    except Exception as exc:
        log(f"Browser tracking failed: {exc}")
        os.startfile(URL)
        while server.poll() is None:
            time.sleep(1)
        server_log.close()
        return 0

    # Closing the dedicated app window is intentionally equivalent to stop.bat.
    current = read_session() or {"token": token, "pid": server.pid}
    request_shutdown(current, "desktop_window_closed")
    deadline = time.time() + 7
    while server.poll() is None and time.time() < deadline:
        time.sleep(0.25)
    if server.poll() is None:
        log("Server did not exit after shutdown request; using process-tree fallback.")
        kill_pid(server.pid)

    server_log.close()
    log("SeeStory desktop session ended.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
