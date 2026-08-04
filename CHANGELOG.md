# SeeStory changelog

## 2026-08-03 — Local GPU desktop release

This release turns SeeStory into a local-only Windows desktop-style application, removes the retired cloud image experiment, improves model quality and startup behavior, and hardens the complete ebook-to-video workflow.

### Installation and local models

- Added `install_all.bat` as the complete Windows installation path.
- Moved all multi-gigabyte model downloads out of the Flask application and into the installer.
- The installer now downloads or resumes the required DreamShaper XL Lightning model, warm-loads it, performs a real CUDA inference, records peak VRAM use, and writes `logs/model-smoke-default.jpg`.
- The optional RealVisXL V5 Lightning Photorealistic model can be installed and GPU-tested during the same installation.
- Added non-interactive installer modes: `install_all.bat default` installs the required model only, while `install_all.bat all` installs both models.
- Added `install_stable_diffusion.bat` as a repair/reverification path for the local model stack.
- Runtime model access is now cache-only. The running app cannot silently begin a large Hugging Face download.
- Missing or incomplete model caches now produce an immediate repair instruction instead of leaving the browser waiting on an unexpected download.
- The installer requires Python 3.12, CUDA-enabled PyTorch, and a real NVIDIA GPU inference. Silent CPU fallback is not accepted.
- Added model-install logging and resumable Hugging Face download settings.

### Image generation

- Simplified SeeStory to one local image-generation path with automatic style routing.
- DreamShaper XL Lightning is used for Cinematic, Storybook, Noir, Oil, Ink, and other illustrated styles.
- RealVisXL V5 Lightning is used for Photorealistic output when that optional model is installed.
- Removed the incompatible Juggernaut XL repository route that failed under Safetensors-only Diffusers loading.
- Strengthened human-anatomy, scene-coherence, negative-prompt, malformed-face, duplicate-person, extra-limb, extra-finger, and accidental-text suppression.
- Added model-specific DPM-Solver scheduling and VRAM-aware model CPU offload.
- Retained manual per-scene regeneration because local diffusion models cannot guarantee perfect anatomy on every image.

### Interface cleanup

- Removed all references to the retired cloud image provider from the Flask page, application code, requirements, documentation, and tests.
- Removed image-provider/source choices from setup and storyboard cards.
- Removed source badges and the green readiness strip.
- Removed the nonfunctional SeeStory process row from the system monitor.
- The monitor now shows only CPU, system RAM, GPU usage, VRAM, and GPU temperature.
- Removed first-run model-download wording from sample generation. Model installation is an installer responsibility.
- Corrected subtitle upload handling to advertise the `.srt` format the backend actually processes.

### Windows desktop behavior

- Added a hidden `pythonw.exe` desktop launcher and a high-resolution SeeStory icon for the desktop shortcut and web page.
- Added `build_icons.py` so repository clones generate the PNG/ICO assets during installation instead of requiring generated binary assets in git.
- SeeStory now opens maximized in a dedicated Chrome or Edge application window.
- Reuses one isolated browser profile instead of creating a new profile on each launch, avoiding first-run browser/profile window bursts.
- Added a Windows named single-instance guard to prevent competing launches after accidental double-clicks.
- Added lightweight startup health checking so opening the window does not initialize the image stack.
- Hidden-process flags are used for ffmpeg, ffprobe, antiword, and other helper processes to prevent console-window flashes.
- Runs the server at Windows HIGH process priority, disables execution-speed/background throttling, and prevents sleep during long jobs.
- Closing the dedicated app window now triggers the same controlled shutdown as `stop.bat`.
- Added browser-process tracking, a token-protected shutdown endpoint, and a heartbeat fallback.
- Added persistent launcher, server, lifecycle, model-install, and shutdown diagnostics under `logs/`.
- Added `shutdown_diagnostic.bat` and `shutdown_diagnostic.py` for cases where a Python process remains after the browser closes.

### Storyboard and ebook fixes

- Fixed EPUB extraction that could count wrapper `<div>` text and its nested paragraphs twice.
- Applied the same nonduplicating block extraction rules to HTML documents.
- Shot text is now non-overlapping and short chapters naturally produce fewer scenes instead of duplicated filler.
- The prompt director tracks recently selected scenes within a chapter and chooses another concrete sentence when a near-identical scene would otherwise repeat.
- Existing saved storyboards are not silently rewritten. Older projects with repeated scenes should be rebuilt from the original ebook.
- Upload scratch files now use unique temporary names and are reliably cleaned up.

### Generation, assembly, and resume reliability

- Fixed a browser event-stream handler that could silently swallow server-side generation errors.
- Failed image generation no longer leaves a fake image path in browser state.
- Regeneration failures are displayed on the affected storyboard card.
- Final assembly is blocked until every required storyboard image exists, preventing partial or out-of-sync videos.
- Completed scene images are saved immediately and skipped when a session resumes.
- Recent-session video status now checks that the MP4 still exists on disk.
- Startup no longer terminates an unrelated program merely because port 5001 is occupied.
- Project, image, and download paths are validated before filesystem access.
- Storyboard and resume text inserted into HTML is escaped.
- Manual prompts and motion values are validated and clamped server-side.
- System-monitor GPU polling is cached briefly to reduce repeated `nvidia-smi` launches.
- Consolidated Windows priority and power behavior into the desktop runtime.

### Packaging and repository hygiene

- Added `BUILD.md` as the source of truth for every Windows BAT file.
- The release ZIP includes the BAT files for convenience, but GitHub ignores `*.bat`, `*.cmd`, and `*.lnk`.
- Added `.gitignore` coverage for virtual environments, model caches, runtime state, logs, generated output, uploads, shortcuts, and diagnostics.
- Added Windows CI for Python compilation and unit/regression tests.
- Added tests covering release hygiene, model configuration, installer/runtime download separation, EPUB duplication, repeated-scene avoidance, validation, and timeline behavior.

### Validation completed before publication

- Python source compilation completed successfully.
- JavaScript syntax validation completed successfully.
- Unit and regression tests completed successfully.
- Every packaged BAT file exactly matches its corresponding code block in `BUILD.md`.
- Release-hygiene checks confirm the retired provider name is absent and BAT files remain ignored.
- ZIP integrity and packaged file checks completed successfully.

### Upgrade notes

- Install into a fresh SeeStory folder and run `install_all.bat`.
- The required local model is installed and GPU-tested before SeeStory is considered ready.
- Choose Yes during installation to install the optional Photorealistic model.
- Rebuild storyboards created with older builds when they contain repeated text or repeated prompts.
- Model weights are not included in GitHub or the release ZIP and remain governed by their upstream licenses.
