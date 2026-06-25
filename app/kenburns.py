"""
Ken Burns motion: a still image becomes a slow-drifting clip.

The clip's length is locked to the shot's slice of the narration, so the audio
never drifts out of sync. Within that fixed length the user controls the feel:

  zoom       in / out / none
  pan        none / left / right / up / down
  intensity  how far it travels (0–100)
  speed      how fast the drift completes; it then holds the final frame (0–100)
  fade_in    seconds the clip fades up from black
  fade_out   seconds the clip fades down to black

It's motion on a still (ffmpeg zoompan), not generative video — exactly the
slow drift-and-fade described, and fast/free enough to run on a whole book.
"""

import os
import subprocess

DEFAULT_MOTION = {
    "zoom": "in", "pan": "none", "intensity": 35, "speed": 50,
    "fade_in": 0.6, "fade_out": 0.6, "opacity": 100,
}

PRESETS = {
    "gentle_drift": {"zoom": "in", "pan": "none", "intensity": 22, "speed": 45,
                     "fade_in": 0.8, "fade_out": 0.8},
    "slow_reveal":  {"zoom": "out", "pan": "none", "intensity": 38, "speed": 35,
                     "fade_in": 1.0, "fade_out": 0.8},
    "pan_right":    {"zoom": "in", "pan": "right", "intensity": 40, "speed": 50,
                     "fade_in": 0.6, "fade_out": 0.6},
    "pan_left":     {"zoom": "in", "pan": "left", "intensity": 40, "speed": 50,
                     "fade_in": 0.6, "fade_out": 0.6},
    "dramatic_push": {"zoom": "in", "pan": "up", "intensity": 60, "speed": 70,
                      "fade_in": 0.4, "fade_out": 0.5},
    "still":        {"zoom": "none", "pan": "none", "intensity": 0, "speed": 50,
                     "fade_in": 0.5, "fade_out": 0.5},
}


def _no_window_kwargs():
    if os.name == "nt":
        return {"creationflags": (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) |
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))}
    return {}


def _merge(motion: dict) -> dict:
    m = dict(DEFAULT_MOTION)
    if motion:
        m.update({k: v for k, v in motion.items() if v is not None})
    return m


def motion_seconds(speed, clip_dur: float) -> float:
    """How long the drift takes before it settles and holds the final frame.

    speed 0  -> slow, ~18 s of drift
    speed 50 -> ~11 s
    speed 100-> brisk, ~4 s
    Never longer than the clip itself.
    """
    sp = max(0.0, min(100.0, float(speed)))
    md = 18.0 - (sp / 100.0) * 14.0
    return max(2.0, min(md, float(clip_dur)))


def _is_still(m: dict) -> bool:
    return m.get("zoom", "none") == "none" and m.get("pan", "none") == "none"


def build_vf(motion: dict, dur_s: float, w: int, h: int, fps: int, sf: int = 1) -> str:
    """Build the per-frame ffmpeg filter chain for one shot.

    `render_clip` pre-sizes the source image to (w*sf, h*sf), so this chain does
    NOT upscale per frame — it just runs zoompan (cropping from the big image and
    downscaling to w×h, which averages away the integer-pixel stair-stepping that
    makes slow zooms look jittery), then fades/opacity.

    The drift plays over `motion_seconds` (set by `speed`) then HOLDS the final
    frame. Fade-in at the very start, fade-out at the very end, once.
    """
    m = _merge(motion)
    n = max(1, int(round(dur_s * fps)))                  # total output frames
    chain = []

    if not _is_still(m):
        amount = max(0.0, min(1.0, float(m["intensity"]) / 100.0)) * 0.5  # up to +0.5x
        mdur = motion_seconds(m["speed"], dur_s)
        mf = max(1, int(round(mdur * fps)))              # frames the drift spans
        # progress 0..1 across the drift, clamped to 1 afterwards so the image
        # settles and HOLDS the final frame; eased out so it decelerates in.
        t = f"(1-pow(1-min(1,on/{mf}),2))"

        pan, zoom = m["pan"], m["zoom"]
        base = 1.0 + (0.14 if (pan != "none" and zoom == "none") else 0.0)
        if zoom == "in":
            z = f"({base}+{amount:.4f}*{t})"
        elif zoom == "out":
            z = f"({base + amount}-{amount:.4f}*{t})"
        else:
            z = f"{base + 0.0001:.4f}"

        rx, ry = "(iw-iw/zoom)", "(ih-ih/zoom)"
        if pan == "right":
            x, y = f"{rx}*{t}", f"{ry}/2"
        elif pan == "left":
            x, y = f"{rx}*(1-{t})", f"{ry}/2"
        elif pan == "down":
            x, y = f"{rx}/2", f"{ry}*{t}"
        elif pan == "up":
            x, y = f"{rx}/2", f"{ry}*(1-{t})"
        else:
            x, y = f"{rx}/2", f"{ry}/2"

        chain.append(f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={w}x{h}:fps={fps}")

    fi, fo = float(m["fade_in"]), float(m["fade_out"])
    if fi > 0:
        chain.append(f"fade=t=in:st=0:d={fi:.2f}")
    if fo > 0:
        chain.append(f"fade=t=out:st={max(0.0, dur_s - fo):.2f}:d={fo:.2f}")
    op = max(20.0, min(100.0, float(m.get("opacity", 100)))) / 100.0
    if op < 0.999:
        chain.append(f"colorchannelmixer=rr={op:.3f}:gg={op:.3f}:bb={op:.3f}")
    chain.append("format=yuv420p")
    return ",".join(chain)


# How many times the working resolution we render the moving image at before
# downscaling. Higher = smoother slow zooms (less integer-pixel stair-stepping),
# at some cost in render time. 4 is a good balance for a laptop GPU/CPU; set
# SEESTORY_SUPERSAMPLE=3 (or 2) if you'd rather trade a little smoothness for a
# faster assemble on very long books.
try:
    SUPERSAMPLE = max(1, min(8, int(os.environ.get("SEESTORY_SUPERSAMPLE", "4"))))
except ValueError:
    SUPERSAMPLE = 4


def render_clip(image_path: str, out_path: str, dur_s: float, motion: dict,
                w: int = 1280, h: int = 720, fps: int = 30) -> str:
    """Render one still -> a motion clip of exactly `dur_s` seconds.

    For moving shots the source is pre-sized once to SUPERSAMPLE× the target so
    the zoom/pan downscale smooths the motion; still shots skip all of that.
    """
    dur_s = max(0.2, float(dur_s))
    m = _merge(motion)
    sf = 1 if _is_still(m) else SUPERSAMPLE

    # Pre-size the source ONCE (cover-fit) so ffmpeg doesn't rescale every frame
    # and the zoom has sub-pixel headroom. Falls back to in-ffmpeg scaling if
    # Pillow can't open the image for some reason.
    src = image_path
    tmp = None
    prescaled = False
    try:
        from PIL import Image, ImageOps
        img = ImageOps.fit(Image.open(image_path).convert("RGB"),
                           (w * sf, h * sf), Image.LANCZOS)
        tmp = out_path + ".src.jpg"
        img.save(tmp, "JPEG", quality=95)
        src = tmp
        prescaled = True
    except Exception:
        tmp = None

    parts = []
    if not prescaled:
        parts.append(f"scale={w * sf}:{h * sf}:force_original_aspect_ratio=increase")
        parts.append(f"crop={w * sf}:{h * sf}")
    parts.append(build_vf(motion, dur_s, w, h, fps, sf))
    vf = ",".join(p for p in parts if p)

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", src,
        "-t", f"{dur_s:.3f}", "-vf", vf, "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        out_path,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, **_no_window_kwargs())
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode("utf-8", "ignore")[-700:])
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return out_path
