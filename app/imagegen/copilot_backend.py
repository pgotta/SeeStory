"""
Copilot backend (the premium, save-it-for-the-big-moments option).

Wraps the Windows-Copilot-API (https://github.com/sums001/
Windows-Copilot-API), which drives Microsoft Copilot's own image generation and
returns a hosted image URL. We download that URL to disk.

Because that API is a *personal* bridge processed one request at a time, this
backend is wrapped in:
  * a minimum spacing between calls (token bucket), and
  * a hard per-run cap,
so an account is never hammered. It's optional: if the library isn't found or
you haven't logged in, the router falls back to Stable Diffusion / placeholder.

Setup (one time): just run setup_copilot.bat, which downloads the repo into the
SeeStory folder, installs its deps, and signs you in via Google Chrome (saving the
session to SeeStory/session/). Re-sign-in later with login_copilot.bat. The folder
is auto-detected; override with SEESTORY_COPILOT_PATH if it lives elsewhere.
"""

import os
import sys
import time
import threading
import urllib.request

_LOCK = threading.Lock()
_CLIENT = None
_LAST_CALL = 0.0
_COUNT = 0

MIN_SPACING_S = float(os.environ.get("SEESTORY_COPILOT_SPACING", "8"))
HARD_CAP = int(os.environ.get("SEESTORY_COPILOT_CAP", "30"))


def _find_copilot_path():
    p = os.environ.get("SEESTORY_COPILOT_PATH")
    if p and os.path.isdir(p):
        return p
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for cand in ("Windows-Copilot-API", os.path.join("..", "Windows-Copilot-API")):
        full = os.path.abspath(os.path.join(here, cand))
        if os.path.isdir(os.path.join(full, "copilot")):
            return full
    return None


def is_available() -> bool:
    try:
        path = _find_copilot_path()
        if path and path not in sys.path:
            sys.path.insert(0, path)
        import copilot  # noqa: F401
        return True
    except Exception:
        return False


def _session_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "session")


def is_signed_in() -> bool:
    """True if a saved Copilot sign-in session exists on disk. (Whether it's
    still *valid* can only be known for sure by making a call — but a saved
    session is the right gate for the badge, and an expired one will surface a
    clear error at generation time.)"""
    sess = _session_dir()
    if not os.path.isdir(sess):
        return False
    if os.path.exists(os.path.join(sess, "token.json")):
        return True
    try:
        return len(os.listdir(sess)) > 0
    except OSError:
        return False


def _client():
    global _CLIENT
    if _CLIENT is None:
        path = _find_copilot_path()
        if path and path not in sys.path:
            sys.path.insert(0, path)
        from copilot import CopilotClient
        _CLIENT = CopilotClient()
    return _CLIENT


def remaining() -> int:
    return max(0, HARD_CAP - _COUNT)


def enrich_prompt(prompt: str, source_text: str = "") -> str:
    """Optional: let Copilot rewrite a richer image prompt. Best-effort."""
    try:
        ask = ("Rewrite the following into one vivid, concrete image-generation "
               "prompt (a single line, no preamble, no quotes, describe only "
               "what is visible): " + prompt)
        reply = _client().chat(ask)
        line = (reply.text or "").strip().splitlines()[0].strip().strip('"')
        return line or prompt
    except Exception:
        return prompt


_CONSEC_FAIL = 0
_DISABLED_THIS_RUN = False


def reset_run_state():
    """Call at the start of a generation run: clears the per-run image cap and
    the 'too many failures, stop trying' breaker."""
    global _COUNT, _CONSEC_FAIL, _DISABLED_THIS_RUN
    _COUNT = 0
    _CONSEC_FAIL = 0
    _DISABLED_THIS_RUN = False


def test() -> tuple:
    """Make ONE real end-to-end Copilot call to verify it works right now.
    Returns (ok: bool, detail: str) — the detail is already user-friendly."""
    if not is_available():
        return False, ("Copilot library not found, or its dependencies aren't "
                       "installed. Run setup.bat, then login_copilot.bat.")
    if not is_signed_in():
        return False, ("Not signed in. Run login_copilot.bat to sign in, then "
                       "restart SeeStory.")
    reset_run_state()
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "seestory_copilot_test.jpg")
    try:
        generate("a simple test illustration: a single blue circle on a white "
                 "background", tmp)
    except Exception as e:
        return False, str(e)   # already translated by generate()/_explain()
    ok = os.path.exists(tmp) and os.path.getsize(tmp) > 0
    try:
        os.remove(tmp)
    except OSError:
        pass
    if ok:
        return True, "Copilot is working — a test image generated successfully."
    return False, "Copilot returned no image (it may have declined the test prompt)."


def _explain(err: str) -> str:
    """Turn a raw library error into something the user can act on."""
    e = (err or "").lower()
    if "invalid-event" in e:
        return ("Copilot rejected the request (invalid-event). The "
                "Windows-Copilot-API library's chat handshake is out of step with "
                "Microsoft's current Copilot protocol — update that library (or let "
                "its author know). Using Stable Diffusion meanwhile.")
    if "clearance" in e or "turnstile" in e or "cf_clearance" in e or "503" in e:
        return ("Copilot needs fresh Cloudflare clearance — run login_copilot.bat "
                "to refresh it, then retry.")
    if "chat-service-unavailable" in e:
        return ("Copilot's chat backend is geo-restricted / unavailable right now; "
                "using Stable Diffusion.")
    if "no active socket" in e or "websocket" in e or "connection" in e:
        return ("Couldn't reach Copilot (connection issue); using Stable Diffusion. "
                "If it persists, re-run login_copilot.bat.")
    return "Copilot request failed: " + str(err)


def generate(prompt: str, out_path: str, **_) -> str:
    global _LAST_CALL, _COUNT, _CONSEC_FAIL, _DISABLED_THIS_RUN
    if not is_available():
        raise RuntimeError("Copilot backend unavailable (library not found / "
                           "not logged in). Falling back.")
    if _DISABLED_THIS_RUN:
        raise RuntimeError("Copilot turned off for the rest of this run after "
                           "repeated failures (see the earlier Copilot error). "
                           "Using Stable Diffusion.")
    with _LOCK:
        if _COUNT >= HARD_CAP:
            raise RuntimeError(f"Copilot hard cap reached ({HARD_CAP}). "
                               "Using Stable Diffusion instead.")
        wait = MIN_SPACING_S - (time.time() - _LAST_CALL)
        if wait > 0:
            time.sleep(wait)

        reply = None
        err = None
        ask = ("Generate a single illustration of the following scene. Output only "
               "the image — do not reply with text, and put no text, words, "
               "captions or watermark inside the picture. Scene: " + prompt)
        for attempt in range(2):     # one retry — invalid-event can be a transient race
            try:
                reply = _client().chat(ask)
                err = None
                break
            except Exception as e:
                err = e
                if attempt == 0:
                    time.sleep(2.0)
        _LAST_CALL = time.time()

        def _trip():
            global _CONSEC_FAIL, _DISABLED_THIS_RUN
            _CONSEC_FAIL += 1
            if _CONSEC_FAIL >= 2:    # two bad shots in a row -> stop trying for this run
                _DISABLED_THIS_RUN = True

        if err is not None:
            _trip()
            raise RuntimeError(_explain(str(err)))

        if not getattr(reply, "images", None):
            _trip()
            attrs = ", ".join(a for a in dir(reply) if not a.startswith("_"))[:160] \
                if reply is not None else "None"
            raise RuntimeError(
                "Copilot returned no image (it may have declined this prompt, or "
                f"the session isn't signed in). reply type={type(reply).__name__}; "
                f"fields: {attrs}")

        url = reply.images[0].url
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        with open(out_path, "wb") as f:
            f.write(data)
        _COUNT += 1
        _CONSEC_FAIL = 0             # a success clears the breaker
    return out_path


def reset_count():
    global _COUNT
    _COUNT = 0


def diagnose() -> str:
    """A short human-readable status line for the console / build log, so it's
    obvious WHY Copilot is or isn't usable before a run starts."""
    path = _find_copilot_path()
    if not path:
        return ("library folder not found — expected 'Windows-Copilot-API' next "
                "to the SeeStory folder. Run setup_copilot.bat.")
    bits = [f"found at {path}"]
    if is_signed_in():
        bits.append("session present")
    else:
        bits.append("NO saved session — run login_copilot.bat to sign in")
    try:
        if path not in sys.path:
            sys.path.insert(0, path)
        import copilot  # noqa: F401
        bits.append("library imports OK")
    except Exception as e:
        bits.append(f"import failed: {e}")
    return "; ".join(bits)
