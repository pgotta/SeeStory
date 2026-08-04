# SeeStory build and Windows launcher guide

This document is the source of truth for rebuilding the Windows helper files that are intentionally excluded from git. The release/test ZIP may contain these BAT files for convenience, but the repository keeps `*.bat`, `*.cmd`, and `*.lnk` ignored.

## Quick start

The GitHub repository intentionally does not include executable Windows launcher files. Use the downloadable release package, or recreate the required launchers from the exact code blocks in this document.

1. Place the complete SeeStory project in a normal writable folder.
2. Run **`install_all.bat`**. This is the supported first-time setup entry point. It creates the Python environment, installs and verifies CUDA, checks ffmpeg, downloads and GPU-tests the required local model, optionally installs the Photorealistic model, and creates the desktop shortcut.
3. After setup completes, start SeeStory from the desktop shortcut or with **`run.bat`**.
4. Close the dedicated SeeStory window to stop the background service. Use **`stop.bat`** only when an explicit manual stop is needed.

For non-interactive setup, `install_all.bat default` installs only the required illustration model, while `install_all.bat all` installs both the illustration and Photorealistic models.

## Build/runtime layout

- `launch_seestory.pyw` — hidden desktop launcher. Starts Flask with `pythonw.exe`, waits for readiness, opens the tracked Chrome/Edge app window, and shuts the server down when that window closes.
- `app/desktop_runtime.py` — lifecycle heartbeat, Windows HIGH priority/power-throttling safeguards, diagnostics state, and system monitor API.
- `stop_seestory.py` — explicit stop helper used by `stop.bat`.
- `shutdown_diagnostic.py` — writes a timestamped shutdown/process diagnostic.
- `install_models.py` — installer-only model download, warm-load, CUDA inference smoke test, and model-install logging.
- `build_icons.py` — recreates the high-resolution PNG and multi-size Windows ICO used by the shortcut.
- `assets/SeeStory.ico` — desktop shortcut icon.
- `output/`, `uploads/`, `logs/`, `runtime/`, `venv/` — local/generated state and never release-source content.

## Local image models

The app has one local image-generation path with automatic style routing: Cinematic/Storybook/Noir/Oil/Ink use `Lykon/dreamshaper-xl-lightning`, and Photorealistic uses `SG161222/RealVisXL_V5.0_Lightning`. Both repositories publish a complete Diffusers fp16/Safetensors layout.
The weights are not committed or bundled; each downloaded model remains under its own upstream license.

`install_all.bat` owns every large model transfer. It installs CUDA-enabled PyTorch and the image stack, downloads/resumes the required illustration model, warm-loads it, performs a real CUDA inference, records peak VRAM, and saves a smoke-test image. The optional Photorealistic model is offered and verified the same way. The desktop runtime is forced offline for Hugging Face/Transformers, so Flask never starts model downloads. ffmpeg is a separate system dependency and is checked by the installer. Pass `default` to skip the optional-model prompt or `all` to install both models non-interactively.

## Desktop startup and shutdown

The launcher reuses `%LOCALAPPDATA%\SeeStory\BrowserProfile` for the dedicated browser app. Do not switch back to a unique profile per launch: fresh browser profiles can trigger first-run/profile process churn and visible window flashes. The profile is outside the repository. A Windows named mutex also prevents multiple launcher instances from racing if the shortcut is double-clicked more than once.

Closing the tracked app window calls the token-protected local shutdown endpoint. A browser heartbeat is a fallback for crash/process handoff cases. The explicit `stop.bat` path remains available.

## Repository rules

Before releasing, `git status` should contain no BAT/CMD/shortcut, venv, model cache, runtime state, logs, uploads, generated project output, or shutdown-diagnostic files. `.gitignore` enforces these rules.

## Exact Windows BAT files

The following blocks are generated from the BAT files included in this package. When changing a BAT, update this document in the same release.

### `install_all.bat`

```bat
@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title SeeStory - Install All

set "PYLAUNCH="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYLAUNCH=py -3.12"
)
if not defined PYLAUNCH (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYLAUNCH=python"
  )
)

set "HF_HUB_DISABLE_TELEMETRY=1"
set "HF_HUB_DOWNLOAD_TIMEOUT=900"
set "HF_HUB_ETAG_TIMEOUT=60"
set "TOKENIZERS_PARALLELISM=false"
set "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True"

if not exist logs mkdir logs

echo ================================================================
echo  SEESTORY - INSTALL ALL
echo ================================================================
echo.
echo This creates the local environment, installs and GPU-tests the
echo required image model, checks ffmpeg, and creates the shortcut.
echo.
echo IMPORTANT: The required model is about 7 GB. It is downloaded here,
echo not later from the SeeStory web page. Windows Defender may scan the
echo new files during this installation, so the model step can take time.
echo.

if not defined PYLAUNCH (
  echo Python 3.12 was not found.
  echo Install Python 3.12 from python.org, then run this file again.
  goto :fail
)

if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>nul
  if errorlevel 1 (
    echo [1/8] Existing venv uses the wrong Python version - rebuilding it...
    rmdir /s /q venv
  )
)
if not exist "venv\Scripts\python.exe" (
  echo [1/8] Creating Python 3.12 virtual environment...
  %PYLAUNCH% -m venv venv
  if errorlevel 1 goto :fail
) else (
  echo [1/8] Existing Python 3.12 virtual environment found - reusing it.
)
set "PY=venv\Scripts\python.exe"

echo.
echo [2/8] Updating pip and installing core requirements...
"%PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :fail
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo [3/8] Checking NVIDIA PyTorch / CUDA...
"%PY%" -c "import torch,sys; ok=torch.cuda.is_available(); print('Current PyTorch:',torch.__version__); print('CUDA available:',ok); sys.exit(0 if ok else 1)" 2>nul
if errorlevel 1 (
  echo Installing CUDA 12.8 PyTorch for RTX 50-series and other NVIDIA GPUs...
  "%PY%" -m pip uninstall -y torch torchvision torchaudio >nul 2>nul
  "%PY%" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
  if errorlevel 1 goto :fail
) else (
  echo CUDA-enabled PyTorch is already working.
)

echo.
echo [4/8] Verifying the GPU image-generation stack...
"%PY%" -c "import torch,diffusers,transformers,accelerate,safetensors,sys; ok=torch.cuda.is_available(); print('PyTorch:',torch.__version__); print('Diffusers:',diffusers.__version__); print('Transformers:',transformers.__version__); print('CUDA:',ok); print('GPU:',torch.cuda.get_device_name(0) if ok else 'NOT AVAILABLE'); sys.exit(0 if ok else 1)"
if errorlevel 1 goto :gpu_fail

echo.
echo [5/8] Checking ffmpeg...
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo ffmpeg was not found. Attempting a winget install...
  where winget >nul 2>nul
  if not errorlevel 1 winget install --id Gyan.FFmpeg --exact --accept-package-agreements --accept-source-agreements
  where ffmpeg >nul 2>nul
  if errorlevel 1 echo WARNING: ffmpeg is still missing. Install it, then restart Windows or sign out/in.
) else (
  ffmpeg -version | findstr /B /C:"ffmpeg version"
)

echo.
echo [6/8] Installing and GPU-testing the required illustration model...
"%PY%" "install_models.py" --model default
if errorlevel 1 goto :model_fail

echo.
echo [7/8] Optional Photorealistic model...
set "INSTALL_PHOTO="
if /I "%~1"=="all" set "INSTALL_PHOTO=Y"
if /I "%~1"=="default" set "INSTALL_PHOTO=N"
if not defined INSTALL_PHOTO (
  choice /C YN /N /M "Install and GPU-test the optional Photorealistic model too? [Y/N] "
  if errorlevel 2 (set "INSTALL_PHOTO=N") else (set "INSTALL_PHOTO=Y")
)
if /I "%INSTALL_PHOTO%"=="Y" (
  "%PY%" "install_models.py" --model photoreal
  if errorlevel 1 goto :model_fail
) else (
  echo Skipped. Run install_all.bat again and choose Yes before using Photorealistic.
)

echo.
echo [8/8] Creating app icons and desktop shortcut...
"%PY%" "build_icons.py" --quiet
if errorlevel 1 echo WARNING: App icon generation failed.
if not exist "assets\SeeStory.ico" goto :icon_missing
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=(Resolve-Path '.').Path; $desktop=[Environment]::GetFolderPath('Desktop'); $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut((Join-Path $desktop 'SeeStory.lnk')); $s.TargetPath=(Join-Path $root 'venv\Scripts\pythonw.exe'); $s.Arguments='\"'+(Join-Path $root 'launch_seestory.pyw')+'\"'; $s.WorkingDirectory=$root; $s.IconLocation=(Join-Path $root 'assets\SeeStory.ico')+',0'; $s.Description='SeeStory - bring narrated audiobooks to life'; $s.Save()"
if errorlevel 1 echo WARNING: The desktop shortcut could not be created. run.bat still works.
goto :complete

:icon_missing
echo WARNING: assets\SeeStory.ico is missing. Shortcut was not created.

:complete
echo.
echo ================================================================
echo  INSTALL COMPLETE
echo ================================================================
echo  The required local model is downloaded, warm-loaded, and GPU-tested.
if /I "%INSTALL_PHOTO%"=="Y" echo  The optional Photorealistic model also passed its GPU test.
echo  Start SeeStory with the desktop shortcut or run.bat.
echo  The app will not download model weights while it is running.
echo.
echo  Model install log: logs\model-install.log
echo  Smoke images:      logs\model-smoke-*.jpg
echo  Shutdown problems: run shutdown_diagnostic.bat
echo ================================================================
echo.
pause
exit /b 0

:gpu_fail
echo.
echo CUDA could not be proven after installation. SeeStory will not silently
echo use CPU for the image model. Update the NVIDIA driver and rerun this file.
goto :fail

:model_fail
echo.
echo The model download, warm-load, or real GPU inference test failed.
echo Review logs\model-install.log, then rerun install_all.bat to resume.
goto :fail

:fail
echo.
echo ================================================================
echo  INSTALL FAILED
echo ================================================================
echo Review the error above. Nothing was committed or uploaded.
echo.
pause
exit /b 1
```

### `setup.bat`

```bat
@echo off
cd /d "%~dp0"
call install_all.bat %*
```

### `run.bat`

```bat
@echo off
setlocal
cd /d "%~dp0"
if not exist "venv\Scripts\pythonw.exe" (
  echo SeeStory is not installed yet.
  echo Run install_all.bat first.
  pause
  exit /b 1
)
start "" /b "venv\Scripts\pythonw.exe" "launch_seestory.pyw"
exit /b 0
```

### `stop.bat`

```bat
@echo off
setlocal
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "stop_seestory.py"
) else (
  py -3.12 "stop_seestory.py" 2>nul || python "stop_seestory.py"
)
timeout /t 2 >nul
```

### `shutdown_diagnostic.bat`

```bat
@echo off
setlocal
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" "shutdown_diagnostic.py"
) else (
  py -3.12 "shutdown_diagnostic.py" 2>nul || python "shutdown_diagnostic.py"
)
echo.
pause
```

### `check_gpu.bat`

```bat
@echo off
setlocal
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
  echo Run install_all.bat first.
  pause
  exit /b 1
)
"venv\Scripts\python.exe" -c "import torch; ok=torch.cuda.is_available(); print('PyTorch:',torch.__version__); print('CUDA available:',ok); print('GPU:',torch.cuda.get_device_name(0) if ok else '(CPU only)'); print('CUDA build:',torch.version.cuda)"
echo.
nvidia-smi 2>nul
pause
```

### `install_stable_diffusion.bat`

```bat
@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title SeeStory - Repair Local Models
if not exist "venv\Scripts\python.exe" (
  echo Run install_all.bat first.
  pause
  exit /b 1
)
set "PY=venv\Scripts\python.exe"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "HF_HUB_DOWNLOAD_TIMEOUT=900"
set "HF_HUB_ETAG_TIMEOUT=60"
set "TOKENIZERS_PARALLELISM=false"
set "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True"

if /I "%~1"=="force" "%PY%" -m pip uninstall -y torch torchvision torchaudio
"%PY%" -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>nul
if errorlevel 1 (
  "%PY%" -m pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
  if errorlevel 1 goto :fail
)
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
"%PY%" -c "import torch,sys; ok=torch.cuda.is_available(); print('PyTorch',torch.__version__); print('CUDA',ok); print('GPU',torch.cuda.get_device_name(0) if ok else 'NOT AVAILABLE'); sys.exit(0 if ok else 1)"
if errorlevel 1 goto :fail

"%PY%" "install_models.py" --model default
if errorlevel 1 goto :fail
choice /C YN /N /M "Install/repair the optional Photorealistic model too? [Y/N] "
if errorlevel 2 goto :done
"%PY%" "install_models.py" --model photoreal
if errorlevel 1 goto :fail

:done
echo.
echo Local model repair and GPU verification completed.
echo See logs\model-install.log for details.
pause
exit /b 0

:fail
echo.
echo Model repair failed. Review logs\model-install.log.
pause
exit /b 1
```

## Validation before release

Review `CHANGELOG.md`, then run:

```text
python -m compileall app tests launch_seestory.pyw stop_seestory.py shutdown_diagnostic.py
python -m unittest discover -s tests -v
```

On Windows, also run `check_gpu.bat`, launch from the desktop shortcut, verify a real sample image, build a short MP4, and verify that closing the app window leaves no SeeStory Python process behind.
