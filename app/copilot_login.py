"""
SeeStory Copilot sign-in launcher.

Runs the Windows-Copilot-API interactive login, but with two SeeStory-specific
fixes over a bare ``python -m copilot login``:

1. It launches your system **Google Chrome** (Playwright ``channel="chrome"``)
   instead of Playwright's bundled Chromium, falling back to the bundled engine
   only if Chrome isn't usable on this machine.
2. It runs from the SeeStory folder, so the saved session lands in
   ``SeeStory\\session\\`` — exactly the working directory the app uses at
   runtime, so the sign-in is actually found when generating images.

Invoked by setup_copilot.bat and login_copilot.bat as:  python -m app.copilot_login
"""

import os
import sys


def _find_repo():
    """Locate the Windows-Copilot-API checkout next to (or inside) SeeStory."""
    env = os.environ.get("SEESTORY_COPILOT_PATH")
    if env and os.path.isdir(os.path.join(env, "copilot")):
        return os.path.abspath(env)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # SeeStory root
    for cand in ("Windows-Copilot-API", os.path.join("..", "Windows-Copilot-API")):
        full = os.path.abspath(os.path.join(here, cand))
        if os.path.isdir(os.path.join(full, "copilot")):
            return full
    return None


def _prefer_chrome():
    """Make Playwright's chromium launches use the system Google Chrome.

    Wraps BrowserType.launch_persistent_context so it adds channel="chrome".
    If Chrome isn't installed/usable, it retries without the channel and you
    get the bundled Chromium — so this never blocks sign-in.
    """
    try:
        from playwright.sync_api import BrowserType
    except Exception:
        return  # playwright not importable; nothing to patch

    original = BrowserType.launch_persistent_context

    def patched(self, user_data_dir, **kwargs):
        wants_default_chromium = (
            getattr(self, "name", "") == "chromium"
            and "channel" not in kwargs
            and "executable_path" not in kwargs
        )
        if wants_default_chromium:
            try:
                return original(self, user_data_dir, channel="chrome", **kwargs)
            except Exception as exc:
                print(f"  (Google Chrome not usable here [{exc.__class__.__name__}] "
                      f"- falling back to the bundled browser)")
        return original(self, user_data_dir, **kwargs)

    BrowserType.launch_persistent_context = patched


def main():
    repo = _find_repo()
    if not repo:
        print("Windows-Copilot-API was not found. Run setup_copilot.bat first.")
        return 1
    if repo not in sys.path:
        sys.path.insert(0, repo)

    _prefer_chrome()

    try:
        from copilot.browser import BrowserCopilot
    except Exception as exc:
        print(f"Could not import the Copilot library: {exc}")
        print("Try running setup_copilot.bat again to (re)install its requirements.")
        return 1

    print("Opening Google Chrome for Microsoft / Copilot sign-in...\n")
    BrowserCopilot(headless=False).login()  # writes ./session relative to cwd
    print("\nSigned in. Session saved to: " + os.path.abspath("session"))
    print('The "copilot" badge in SeeStory should be green after a restart.')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
