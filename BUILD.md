# SeeStory — Launcher Build Guide

The Windows `.bat` launchers are intentionally **kept out of version control** (they're in `.gitignore`). This guide documents every launcher and gives its full contents so you can recreate them after cloning.

## Table of Contents

- [Why the launchers aren't in the repo](#why-the-launchers-arent-in-the-repo)
- [How to recreate them](#how-to-recreate-them)
- [The launchers at a glance](#the-launchers-at-a-glance)
- [Optional: the Copilot library](#optional-the-copilot-library)
- [File contents](#file-contents)
  - [`setup.bat`](#setupbat)
  - [`run.bat`](#runbat)
  - [`stop.bat`](#stopbat)
  - [`check_gpu.bat`](#check-gpubat)
  - [`install_stable_diffusion.bat`](#install-stable-diffusionbat)
  - [`setup_copilot.bat`](#setup-copilotbat)
  - [`login_copilot.bat`](#login-copilotbat)
- [Notes (encoding & line endings)](#notes-encoding--line-endings)

## Why the launchers aren't in the repo

They're small, machine‑specific conveniences rather than application code, and downloaded `.bat` files can trip Windows SmartScreen/antivirus — so they're regenerated locally instead of committed. The application itself lives in `app/`; the launchers only wrap `python -m app.server` and the setup steps.

## How to recreate them

For each file in [File contents](#file-contents):

1. Create a new text file in the SeeStory folder with the **exact** name (e.g. `setup.bat`).
2. Paste the matching block below.
3. Save it with **Windows (CRLF) line endings** and ANSI/UTF‑8 encoding.
4. Double‑click `setup.bat` first, then `run.bat`.

> Tip: in Notepad, *Save As* → set *Encoding* to ANSI; CRLF is the Windows default. In VS Code, click the `LF`/`CRLF` indicator in the status bar and choose **CRLF**.

## The launchers at a glance

| File | What it does | When you run it |
|------|--------------|-----------------|
| `setup.bat` | First‑time setup: builds the Python venv, installs the app + Stable Diffusion, and installs the optional Copilot dependencies. Run once. `setup.bat nosd` skips the big SD download. | once, first |
| `run.bat` | Starts SeeStory and opens Chrome at http://127.0.0.1:5001. Run every time you use it; keep the window open while generating. | every time |
| `stop.bat` | Stops a running SeeStory server and frees port 5001. | as needed |
| `check_gpu.bat` | Prints your PyTorch version and whether the CUDA GPU is detected. | as needed |
| `install_stable_diffusion.bat` | (Re)installs the Stable Diffusion backend on its own. `install_stable_diffusion.bat force` does a clean PyTorch reinstall. | as needed |
| `setup_copilot.bat` | Optional. Installs the Copilot library's dependencies and runs the one‑time Microsoft sign‑in. | once (optional) |
| `login_copilot.bat` | Optional. Re‑runs the Copilot Microsoft sign‑in (refreshes the session). | as needed (optional) |

## Optional: the Copilot library

**Copilot is entirely optional** — SeeStory runs fully on Stable Diffusion and
placeholders without it. The `Windows-Copilot-API` library it needs is **not
committed to this repo** (it's a third‑party project, kept out of version control
just like the launchers), so a fresh clone won't have it. Add it only if you want
the premium Copilot image source.

To enable Copilot:

1. Get the library into a `Windows-Copilot-API` folder in the SeeStory root
   (next to `app/`):

   ```bat
   git clone https://github.com/sums001/Windows-Copilot-API
   ```

   (or download the repo ZIP and extract it so you have
   `SeeStory\Windows-Copilot-API\copilot\…`).
2. Build `setup_copilot.bat` and `login_copilot.bat` from
   [File contents](#file-contents) if you don't already have them.
3. Run **`setup_copilot.bat`** once — it installs the library's Python
   dependencies and the Playwright Chromium used for sign‑in, then opens a
   one‑time Microsoft sign‑in.
4. Later, **`login_copilot.bat`** re‑runs that sign‑in to refresh the session.

After signing in, restart SeeStory; the **copilot** badge turns green and the
**Test Copilot** button confirms it works. If Microsoft changes their protocol
and you hit an "invalid‑event" error, replace the `Windows-Copilot-API\copilot`
folder with the latest from the repo above.

> The `setup.bat` launcher also tries to install the Copilot dependencies if the
> `Windows-Copilot-API` folder is present; if it's absent, that step is skipped
> harmlessly and SeeStory still installs normally.

## File contents

### `setup.bat`

First‑time setup: builds the Python venv, installs the app + Stable Diffusion, and installs the optional Copilot dependencies. Run once. `setup.bat nosd` skips the big SD download.

```bat
@echo off
setlocal enableextensions
cd /d "%~dp0"
title SeeStory setup

echo.
echo ===== SeeStory setup =====
echo.

powershell -NoProfile -Command "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force" >nul 2>nul

REM --- find Python (prefer 3.12 via the launcher, else plain python) --------
set "PYLAUNCH=python"
where py >nul 2>nul
if not errorlevel 1 set "PYLAUNCH=py -3.12"
echo Using launcher: %PYLAUNCH%
echo.

REM --- create the venv (skip if present) -----------------------------------
if exist "venv\Scripts\python.exe" goto :have_venv
echo Creating virtual environment...
%PYLAUNCH% -m venv venv
if errorlevel 1 goto :venv_fail
goto :venv_ok
:venv_fail
echo.
echo ERROR: could not create the virtual environment.
echo Install Python 3.10 or newer first: https://www.python.org/downloads/
goto :end
:have_venv
echo Virtual environment already exists - reusing it.
:venv_ok
set "VPY=venv\Scripts\python.exe"

echo.
echo Upgrading pip...
"%VPY%" -m pip install --upgrade pip

echo.
echo Installing core requirements...
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto :core_fail
goto :core_ok
:core_fail
echo.
echo ERROR: core requirements failed to install. Scroll up to see why.
goto :end
:core_ok

if /I "%~1"=="nosd" goto :skip_sd

echo.
echo ============================================================
echo  Installing Stable Diffusion - local image generation.
echo  Large download, can take several minutes. To skip it,
echo  close this window and run:   setup.bat nosd
echo ============================================================
echo.
echo [1/2] Checking PyTorch / GPU...
"%VPY%" -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>nul
if not errorlevel 1 goto :torch_have
echo     Installing PyTorch with CUDA GPU support. Only 'torch' is replaced -
echo     shared packages like jinja2 and MarkupSafe are left untouched.
"%VPY%" -m pip uninstall -y torch >nul 2>nul
"%VPY%" -m pip install torch --index-url https://download.pytorch.org/whl/cu128
if not errorlevel 1 goto :torch_done
echo     CUDA 12.8 unavailable - trying CUDA 12.4...
"%VPY%" -m pip install torch --index-url https://download.pytorch.org/whl/cu124
if not errorlevel 1 goto :torch_done
echo     No GPU build available - installing CPU-only PyTorch ^(slower^).
"%VPY%" -m pip install torch
goto :torch_done
:torch_have
echo     PyTorch with a working CUDA GPU is already installed - skipping the download.
:torch_done

echo.
echo [2/2] diffusers stack...
"%VPY%" -m pip install diffusers transformers accelerate safetensors
if errorlevel 1 goto :sd_warn

echo.
echo Verifying GPU...
"%VPY%" -c "import torch; ok=torch.cuda.is_available(); print('  PyTorch', torch.__version__); print('  GPU available:', ok); print('  GPU:', torch.cuda.get_device_name(0) if ok else 'CPU only')"
goto :sd_done

:sd_warn
echo.
echo NOTE: Stable Diffusion did not fully install. SeeStory still runs with
echo placeholder frames and the Copilot backend. Retry any time with:
echo     install_stable_diffusion.bat
goto :sd_done

:skip_sd
echo.
echo Skipped Stable Diffusion. SeeStory will use placeholder frames until you
echo install it - run  install_stable_diffusion.bat  whenever you like.

:sd_done
echo.
echo ============================================================
echo  Copilot backend (optional) - premium images for big moments.
echo  The Windows-Copilot-API library is BUNDLED with SeeStory;
echo  installing its Python dependencies now...
echo ============================================================
"%VPY%" -m pip install -r "Windows-Copilot-API\requirements.txt"
if errorlevel 1 goto :copilot_warn
echo.
echo Installing the Playwright browser used for the one-time sign-in...
"%VPY%" -m playwright install chromium
if errorlevel 1 goto :copilot_warn
goto :copilot_done
:copilot_warn
echo.
echo NOTE: Copilot dependencies did not fully install. SeeStory still runs
echo       with Stable Diffusion / placeholder. Retry later with setup_copilot.bat.
:copilot_done

echo.
echo ============================================================
echo  Setup complete!
echo.
echo  Start SeeStory:  double-click  run.bat
echo                   opens Chrome at http://127.0.0.1:5001
echo.
echo  OPTIONAL - Copilot backend (premium images for the big moments):
echo    The library is bundled and its dependencies are installed.
echo    To enable it, sign in once:  double-click  login_copilot.bat
echo    Then restart SeeStory - the "copilot" badge turns green, and you can
echo    click "Test Copilot" in the header to confirm it works.
echo    Repo: https://github.com/sums001/Windows-Copilot-API
echo ============================================================

:end
echo.
pause
```

### `run.bat`

Starts SeeStory and opens Chrome at http://127.0.0.1:5001. Run every time you use it; keep the window open while generating.

```bat
@echo off
REM ============================================================
REM  SeeStory - start the app (VISIBLE console, foreground)
REM  Double-click to launch. Chrome opens at http://127.0.0.1:5001.
REM  Keep this window open while using SeeStory. Output is also
REM  saved to seestory.log.
REM ============================================================
cd /d "%~dp0"
title SeeStory

if not exist "venv\Scripts\python.exe" (
    echo. & echo No virtual environment found. Please run setup.bat first. & echo.
    pause & exit /b 1
)

REM Disable console QuickEdit so clicking the window doesn't pause GPU work.
powershell -NoProfile -Command "$sig='[DllImport(\"kernel32.dll\")]public static extern IntPtr GetStdHandle(int h);[DllImport(\"kernel32.dll\")]public static extern bool GetConsoleMode(IntPtr h,out uint m);[DllImport(\"kernel32.dll\")]public static extern bool SetConsoleMode(IntPtr h,uint m);'; $t=Add-Type -MemberDefinition $sig -Name K -Namespace W -PassThru; $h=$t::GetStdHandle(-10); $m=0; [void]$t::GetConsoleMode($h,[ref]$m); [void]$t::SetConsoleMode($h, ($m -bor 0x0080) -band (-bnot 0x0040))" 2>nul

echo Starting SeeStory...  (Chrome will open shortly)
echo.
echo   Keep this window open while generating. For full GPU speed,
echo   keep it in the foreground. Watch progress in the browser.
echo   A copy of all messages is saved to seestory.log
echo.
powershell -NoProfile -Command "$host.UI.RawUI.WindowTitle='SeeStory'; & '%CD%\venv\Scripts\python.exe' -m app.server 2>&1 | Tee-Object -FilePath '%CD%\seestory.log'"

echo.
echo ============================================================
echo  SeeStory has stopped. If unexpected, see seestory.log
echo ============================================================
pause
```

### `stop.bat`

Stops a running SeeStory server and frees port 5001.

```bat
@echo off
REM Stops any running SeeStory server (frees port 5001).
title SeeStory - stop
echo Stopping SeeStory...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5001" ^| findstr LISTENING') do (
    echo   killing PID %%P
    taskkill /PID %%P /F >nul 2>nul
)
echo Done.
timeout /t 2 >nul
```

### `check_gpu.bat`

Prints your PyTorch version and whether the CUDA GPU is detected.

```bat
@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" ( echo Run setup.bat first. & pause & exit /b 1 )
"venv\Scripts\python.exe" -c "import torch; ok=torch.cuda.is_available(); print('PyTorch', torch.__version__); print('GPU available:', ok); print('GPU:', torch.cuda.get_device_name(0) if ok else '(CPU only)')" 2>nul || echo Stable Diffusion not installed yet ^(placeholder + Copilot still work^).
pause
```

### `install_stable_diffusion.bat`

(Re)installs the Stable Diffusion backend on its own. `install_stable_diffusion.bat force` does a clean PyTorch reinstall.

```bat
@echo off
setlocal enableextensions
cd /d "%~dp0"
title SeeStory - install Stable Diffusion
if not exist "venv\Scripts\python.exe" (
    echo Run setup.bat first to create the environment.
    echo.
    pause
    exit /b 1
)
set "VPY=venv\Scripts\python.exe"

if /I "%~1"=="force" goto :torch_force
echo Checking PyTorch / GPU...
"%VPY%" -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>nul
if not errorlevel 1 goto :torch_have
echo Installing PyTorch with CUDA GPU support ^(only 'torch' is replaced^)...
"%VPY%" -m pip uninstall -y torch >nul 2>nul
goto :torch_attempt
:torch_force
echo Forcing a clean PyTorch reinstall...
"%VPY%" -m pip install --force-reinstall --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu128
if not errorlevel 1 goto :torch_done
"%VPY%" -m pip install --force-reinstall --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu124
if not errorlevel 1 goto :torch_done
"%VPY%" -m pip install torch
goto :torch_done
:torch_attempt
"%VPY%" -m pip install torch --index-url https://download.pytorch.org/whl/cu128
if not errorlevel 1 goto :torch_done
echo CUDA 12.8 unavailable - trying CUDA 12.4...
"%VPY%" -m pip install torch --index-url https://download.pytorch.org/whl/cu124
if not errorlevel 1 goto :torch_done
echo Installing CPU-only PyTorch ^(slower^)...
"%VPY%" -m pip install torch
goto :torch_done
:torch_have
echo PyTorch with a working CUDA GPU already present - skipping it.
echo ^(To force a clean reinstall: install_stable_diffusion.bat force^)
:torch_done

echo.
echo Installing diffusers stack...
"%VPY%" -m pip install diffusers transformers accelerate safetensors

echo.
"%VPY%" -c "import torch; ok=torch.cuda.is_available(); print('PyTorch', torch.__version__); print('GPU available:', ok); print('GPU:', torch.cuda.get_device_name(0) if ok else 'CPU only')"
echo.
echo Done. Start SeeStory with run.bat.
pause
```

### `setup_copilot.bat`

Optional. Installs the Copilot library's dependencies and runs the one‑time Microsoft sign‑in.

```bat
@echo off
setlocal enableextensions
cd /d "%~dp0"
title SeeStory - set up Copilot backend

set "REPO=Windows-Copilot-API"

echo.
echo =====================================================================
echo  SeeStory: Copilot backend setup
echo  Adds Microsoft Copilot image generation as the premium source for the
echo  standout moments. Optional - SeeStory works fine without it.
echo  The library is bundled with SeeStory; this just installs its Python
echo  dependencies and signs you in.
echo  Repo: https://github.com/sums001/Windows-Copilot-API
echo =====================================================================
echo.

if not exist "venv\Scripts\python.exe" goto :no_venv
set "VPY=venv\Scripts\python.exe"

if exist "%REPO%\copilot" goto :have_repo
echo ERROR: the bundled Windows-Copilot-API folder is missing. Re-extract the
echo SeeStory download (it should contain a "Windows-Copilot-API" folder).
goto :end
:have_repo
echo [1/3] Library found: %CD%\%REPO%
echo.

echo [2/3] Installing its Python requirements (already-installed ones are skipped)...
"%VPY%" -m pip install -r "%REPO%\requirements.txt"
if errorlevel 1 goto :pip_fail
echo.
echo       Installing the Playwright Chromium browser for sign-in (skipped if present)...
"%VPY%" -m playwright install chromium
echo.

if exist "session\token.json" goto :login_skip
echo [3/3] Microsoft sign-in
echo.
echo   A Google Chrome window will open at copilot.microsoft.com.
echo   Sign in to your Microsoft account and pass any "verify you're human"
echo   check. The window CLOSES BY ITSELF once sign-in is detected - you do
echo   not need to press anything here.
echo.
echo   Press a key when you're ready to open the sign-in window...
pause >nul
"%VPY%" -m app.copilot_login
goto :ready
:login_skip
echo [3/3] Already signed in - existing session found, skipping sign-in.
echo       To sign in again later, run  login_copilot.bat
:ready

echo.
echo =====================================================================
echo  Copilot backend is ready.
echo  Start (or restart) SeeStory with run.bat - the "copilot" badge in the
echo  header should turn green. Click "Test Copilot" there to confirm.
echo.
echo  Remember: Copilot is auto-capped and spaced so your account is never
echo  hammered. Choose "Both" or "Copilot only" mode in the app to use it.
echo =====================================================================
goto :end

:no_venv
echo Please run setup.bat first to create the SeeStory environment.
goto :end
:pip_fail
echo.
echo ERROR: installing the API's requirements failed. Scroll up for the reason.
goto :end

:end
echo.
pause
```

### `login_copilot.bat`

Optional. Re‑runs the Copilot Microsoft sign‑in (refreshes the session).

```bat
@echo off
setlocal enableextensions
cd /d "%~dp0"
title SeeStory - Copilot sign-in

if not exist "venv\Scripts\python.exe" (
    echo Run setup.bat first to create the SeeStory environment.
    echo.
    pause
    exit /b 1
)
if not exist "Windows-Copilot-API\copilot" (
    echo The bundled Windows-Copilot-API folder is missing - re-extract the
    echo SeeStory download, then run setup.bat.
    echo.
    pause
    exit /b 1
)
echo Making sure the Copilot dependencies are installed...
"venv\Scripts\python.exe" -m pip install -q -r "Windows-Copilot-API\requirements.txt" >nul 2>nul
"venv\Scripts\python.exe" -m playwright install chromium >nul 2>nul

echo Opening Google Chrome for Microsoft / Copilot sign-in.
echo The window closes by itself once you're signed in - nothing to press here.
echo.
"venv\Scripts\python.exe" -m app.copilot_login

echo.
echo If SeeStory is open, restart it with run.bat so it picks up the session.
pause
```

## Notes (encoding & line endings)

- **Line endings:** save as **CRLF**. `cmd.exe` mostly tolerates LF, but a few constructs (labels, multi‑line blocks) are happier with CRLF.
- **Encoding:** ANSI or UTF‑8 without BOM. A UTF‑8 BOM can make `cmd` choke on the first line.
- **SmartScreen:** the first time you double‑click a `.bat`, Windows may warn. Choose *More info → Run anyway* (or unblock it in the file's Properties).
- **Paths:** every launcher does `cd /d "%~dp0"`, so they work from wherever the SeeStory folder lives — just keep them in the SeeStory root next to `app/`.
