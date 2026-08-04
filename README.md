# SeeStory

**Bring your narrated audiobook to life.**

SeeStory turns a finished [Parroty](https://github.com/pgotta/Parroty) audiobook into a synchronized illustrated MP4. It reads the same ebook, aligns it to Parroty's narration timestamps, builds a timed storyboard, generates each scene locally on the GPU, adds slow Ken Burns motion, and assembles the result with audio, subtitles, and chapter bookmarks.

SeeStory runs locally at **http://127.0.0.1:5001**.

## Windows quick start

1. Extract the entire SeeStory folder to a normal writable location.
2. Double-click **`install_all.bat`**. It downloads, warm-loads, and performs a real GPU test with the required illustration model before the app is considered installed. It then asks whether to install the optional Photorealistic model too.
3. Start SeeStory from the desktop shortcut, or use **`run.bat`**.
4. Closing the dedicated SeeStory window stops the local server, just like **`stop.bat`**.

Have these Parroty outputs ready:

- The same ebook used to create the audiobook.
- Parroty's combined MP3.
- Parroty's `youtube-chapters-*.txt`, or equivalent timestamp lines.

## Local image generation

There is one image-generation path and it runs locally. The web page does not expose provider/source controls.

- **DreamShaper XL Lightning** is selected automatically for Cinematic, Storybook, Noir, Oil, Ink, and other illustrated looks.
- **RealVisXL V5 Lightning** is selected automatically for the Photorealistic style.
- Prompts include stronger scene-coherence and human-anatomy guidance, plus negative prompting for extra limbs/fingers, duplicate people, malformed faces, and accidental text.
- `install_all.bat` downloads the required DreamShaper model (roughly 7 GB), warm-loads it, and creates a real smoke-test image on CUDA. The installer optionally offers the separate Photorealistic model. Windows security software may scan new weight files during installation.
- The running Flask app is offline-only for model access. It never starts a multi-gigabyte download; a missing/incomplete model produces a clear instruction to rerun the installer.
- The weights are not bundled in the SeeStory ZIP/repository and remain subject to each model's upstream license.
- On lower-VRAM NVIDIA GPUs, SeeStory automatically uses model CPU offload and can retry at smaller resolutions if a generation runs out of VRAM.

No diffusion model can guarantee perfect hands or anatomy every time, so the storyboard keeps **Regenerate** manual and visible for individual scenes.

## Windows desktop behavior

- Opens maximized in a dedicated Chrome or Edge app window.
- Uses `pythonw.exe`, so no PowerShell/Python console remains visible.
- Generates the desktop PNG/ICO assets from `build_icons.py` during installation, so a repository clone retains the same high-resolution icon without tracking generated binaries.
- Reuses one isolated SeeStory browser profile instead of creating a fresh profile every launch. This avoids the burst of browser/profile windows seen during startup while keeping SeeStory separate from the normal browser profile.
- Uses a single-instance launcher guard so an accidental double-click cannot start competing desktop sessions.
- Uses a lightweight startup health check, and Windows subprocesses such as ffmpeg/ffprobe run with hidden console flags, so helper processes do not flash extra windows.
- Runs the SeeStory server at Windows **HIGH** process priority.
- Disables Windows execution-speed throttling and browser background throttling.
- Prevents sleep while a long SeeStory session is active.
- Displays CPU, system RAM, GPU usage, VRAM, and GPU temperature in the lower-left corner.
- Uses browser-process tracking plus a heartbeat fallback for reliable shutdown.
- Writes launcher/server/lifecycle logs under `logs/`.

If closing the app window ever leaves SeeStory running, run **`shutdown_diagnostic.bat`** before manually ending the process. It creates a timestamped report with the listener, PID, session state, GPU state, and recent logs.

## Requirements

- Windows 10 or Windows 11.
- Python 3.12 required by the Windows installer.
- NVIDIA CUDA GPU required for local image generation. The installer uses the CUDA 12.8 PyTorch path used by RTX 50-series cards and stops if real CUDA execution cannot be proven.
- ffmpeg for motion clips and final video assembly.
- Enough free disk space for the required model cache, optional Photorealistic cache, and generated images/video. The installer shows and verifies each model before completion.

The installer does not permit a silent CPU fallback.

## Workflow

1. Add the ebook, narration audio, and chapter timestamps.
2. Pick the art style and how often the picture should change.
3. Build and review the storyboard; edit prompts or motion where useful.
4. Generate the images. Completed shots are saved immediately and are skipped when a session resumes.
5. Stitch the complete storyboard with narration, subtitles, and chapters into the MP4.

Video assembly is blocked if any storyboard image is missing, preventing a partially generated book from being stitched out of sync.

## Output

Each project folder under `output/` can contain:

- Final chaptered SeeStory MP4.
- YouTube chapter timestamp file.
- Google Drive chapter page.
- Optional `.srt` subtitle file.
- Individual scene images and motion clips.
- `project.json` for resume/restoration.

## Repository and Windows launcher rules

Windows `.bat`, `.cmd`, and `.lnk` files are intentionally excluded from git. The test/release ZIP includes the BAT launchers, while **[BUILD.md](BUILD.md)** contains their exact contents so they can always be recreated from the repository.

The repository also excludes the virtual environment, runtime state, logs, generated output, uploads, and local model caches.

See **[CHANGELOG.md](CHANGELOG.md)** for the complete release history, fixes, and validation summary.

## Credits

Made to run beside [Parroty](https://github.com/pgotta/Parroty). SeeStory's book parsing, scene planning, local image generation, and video build all run on the local computer.


## Storyboard text and repeated scenes

SeeStory reads EPUB text in DOM/spine order and avoids counting wrapper HTML containers twice. Shot text is non-overlapping, and the prompt director avoids reusing the same recent scene when another concrete sentence is available. If a storyboard was created by an older build and already contains repeated prompts, rebuild that storyboard from the original ebook; saved old project JSON is intentionally not rewritten behind your back.

