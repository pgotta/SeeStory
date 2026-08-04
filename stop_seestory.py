"""Stop a running SeeStory desktop session, with a process-tree fallback."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SESSION = ROOT / "runtime" / "seestory-session.json"
PORT = 5001
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.3):
            return True
    except OSError:
        return False


def load_session() -> dict:
    try:
        return json.loads(SESSION.read_text(encoding="utf-8"))
    except Exception:
        return {}


def request_stop(session: dict) -> bool:
    token = session.get("token", "")
    if not token:
        return False
    try:
        body = json.dumps({"token": token}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/api/desktop/shutdown", data=body,
            headers={"Content-Type": "application/json", "X-SeeStory-Token": token},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).read()
        return True
    except Exception:
        return False


def netstat_pids() -> set[int]:
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"], text=True, errors="ignore",
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        if f":{PORT}" in line and "LISTENING" in line.upper():
            try:
                pids.add(int(line.split()[-1]))
            except (ValueError, IndexError):
                pass
    return pids


def kill(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )


def main() -> int:
    if not port_open():
        print("SeeStory is not running.")
        return 0
    session = load_session()
    print("Stopping SeeStory...")
    request_stop(session)
    deadline = time.time() + 6
    while port_open() and time.time() < deadline:
        time.sleep(0.25)
    if port_open():
        pids = netstat_pids()
        if session.get("pid"):
            pids.add(int(session["pid"]))
        for pid in pids:
            kill(pid)
    print("SeeStory stopped." if not port_open() else "SeeStory may still be running. Run shutdown_diagnostic.bat.")
    return 0 if not port_open() else 1


if __name__ == "__main__":
    raise SystemExit(main())
