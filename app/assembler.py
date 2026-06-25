"""
Final assembly: stitch the per-shot motion clips into one video and lay
Parroty's narration MP3 underneath it, with chapter bookmarks baked in.

Each shot clip is rendered with identical codec/size/fps, so they concatenate
without re-encoding (fast). The audio is the master track; chapter metadata and
the Google Drive chapter page come straight from Parroty's helpers.
"""

import os
import subprocess

from .drivepage import (build_ffmetadata, build_drive_chapter_page,
                        build_youtube_timestamps, _fmt_timestamp)  # noqa: F401


def _no_window_kwargs():
    if os.name == "nt":
        return {"creationflags": (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) |
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))}
    return {}


def ensure_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def render_cover_clip(image_path: str, out_path: str, dur_s: float,
                      w: int, h: int, fps: int) -> str:
    """A still book cover, letterboxed to the video frame, fading in and out —
    shown at the very start like opening a book. Same codec/size/fps as the shot
    clips so it concatenates without re-encoding."""
    dur_s = max(1.0, float(dur_s))
    fo = min(0.8, dur_s / 4.0)
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
          f"fade=t=in:st=0:d=0.8,"
          f"fade=t=out:st={max(0.0, dur_s - fo):.2f}:d={fo:.2f},"
          f"setsar=1,format=yuv420p")
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", image_path, "-t", f"{dur_s:.3f}",
           "-vf", vf, "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", out_path]
    p = subprocess.run(cmd, capture_output=True, **_no_window_kwargs())
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "ignore")[-700:])
    return out_path


def assemble_video(clip_paths: list, audio_path: str, out_path: str,
                   markers: list = None, total_ms: int = None,
                   progress_callback=None, lead_silence_ms: int = 0,
                   subtitle_path: str = None, subtitle_mode: str = "none") -> str:
    """Concatenate clips + mux audio -> chaptered mp4.

    clip_paths       : per-shot clip files in timeline order (a cover clip, if any,
                       should already be prepended by the caller).
    lead_silence_ms  : if a cover was prepended, delay the narration by this much
                       so audio still lines up with the shots.
    subtitle_path    : path to an .srt (timings already include any lead offset).
    subtitle_mode    : 'none' | 'soft' (toggleable track) | 'burn' (re-encoded in).
    """
    if not clip_paths:
        raise RuntimeError("No clips to assemble.")
    work = os.path.dirname(out_path)
    list_file = os.path.join(work, "_concat.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    meta_file = None
    if markers and total_ms:
        meta_file = out_path + ".ffmeta.txt"
        with open(meta_file, "w", encoding="utf-8") as f:
            f.write(build_ffmetadata(markers, total_ms))

    burn = (subtitle_mode == "burn" and subtitle_path and os.path.exists(subtitle_path))
    soft = (subtitle_mode == "soft" and subtitle_path and os.path.exists(subtitle_path))

    cmd = ["ffmpeg", "-y", "-fflags", "+genpts",
           "-f", "concat", "-safe", "0", "-i", list_file,
           "-i", audio_path]
    idx = 2
    meta_idx = None
    if meta_file:
        cmd += ["-i", meta_file]; meta_idx = idx; idx += 1
    subs_idx = None
    if soft:
        cmd += ["-i", subtitle_path]; subs_idx = idx; idx += 1

    # filter graph for audio delay (cover) and/or burned-in subtitles
    fc = []
    vlabel, alabel = "0:v", "1:a"
    if lead_silence_ms and lead_silence_ms > 0:
        fc.append(f"[1:a]adelay={int(lead_silence_ms)}:all=1[aud]")
        alabel = "[aud]"
    if burn:
        subname = os.path.basename(subtitle_path)  # resolved via cwd=work
        fc.append(f"[0:v]subtitles=filename='{subname}'[vid]")
        vlabel = "[vid]"
    if fc:
        cmd += ["-filter_complex", ";".join(fc)]

    cmd += ["-map", vlabel, "-map", alabel]
    if subs_idx is not None:
        cmd += ["-map", f"{subs_idx}:s"]
    if meta_idx is not None:
        cmd += ["-map_metadata", str(meta_idx), "-map_chapters", str(meta_idx)]

    if burn:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k"]
    if subs_idx is not None:
        cmd += ["-c:s", "mov_text"]
    if not (lead_silence_ms and lead_silence_ms > 0):
        cmd += ["-shortest"]
    if progress_callback and total_ms:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += [out_path]

    run_kw = dict(_no_window_kwargs())
    run_kw["cwd"] = work  # so the subtitles filter can use a bare filename
    try:
        if progress_callback and total_ms:
            import threading
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, **run_kw)
            tail = []

            def _drain():
                try:
                    for ln in proc.stderr:
                        tail.append(ln)
                        if len(tail) > 40:
                            del tail[0]
                except Exception:
                    pass
            t = threading.Thread(target=_drain, daemon=True)
            t.start()
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        frac = max(0.0, min(1.0, (int(line.split("=", 1)[1]) / 1000.0) / total_ms))
                        progress_callback(frac)
                    except (ValueError, ZeroDivisionError):
                        pass
                elif line == "progress=end":
                    progress_callback(1.0)
            proc.wait()
            t.join(timeout=2)
            if proc.returncode != 0:
                raise RuntimeError("".join(tail)[-800:])
        else:
            proc = subprocess.run(cmd, capture_output=True, **run_kw)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.decode("utf-8", "ignore")[-800:])
    finally:
        for f in (list_file, meta_file):
            if f and os.path.exists(f):
                try:
                    os.unlink(f)
                except OSError:
                    pass
    return out_path
