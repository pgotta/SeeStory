"""
SeeStory — turn a Parroty audiobook into a watch-along illustrated video.

Pipeline:  ebook + Parroty MP3 + Parroty timestamps
           -> chapters (same parser as Parroty) mapped onto the audio timeline
           -> shots (one image/clip each), prompted + routed by the director
           -> images (Stable Diffusion / Copilot / placeholder)
           -> Ken Burns motion clips
           -> one chaptered MP4 synced to the narration.

Runs locally at http://127.0.0.1:5001 so it sits beside Parroty (port 5000).
"""

import io
import json
import os
import random
import sys

import shutil
import threading
import time
import webbrowser
from dataclasses import asdict

from flask import (Flask, Response, jsonify, render_template, request,
                   send_from_directory, stream_with_context)

from .epub_parser import parse_epub
from .document_parser import parse_document
from . import timeline as TL
from . import director as DIR
from . import kenburns as KB
from . import imagegen
from . import assembler as ASM
from . import pagemap
from . import subtitles as SUB

COVER_SECONDS = 6.0  # how long the book cover holds at the very start

# ── paths ────────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(BASE, "output")
UPLOADS = os.path.join(BASE, "uploads")
os.makedirs(OUTPUT, exist_ok=True)
os.makedirs(UPLOADS, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024 * 1024  # 5 GB (long audiobooks)

PORT = int(os.environ.get("SEESTORY_PORT", "5001"))
VIDEO_W = int(os.environ.get("SEESTORY_W", "1280"))
VIDEO_H = int(os.environ.get("SEESTORY_H", "720"))
VIDEO_FPS = int(os.environ.get("SEESTORY_FPS", "30"))


# ── helpers ──────────────────────────────────────────────────────────────
class _PairedChapter:
    """A chapter whose label is the audio/YouTube title and whose text is the
    aligned ebook content (may be empty if the ebook had no matching section)."""
    __slots__ = ("title", "text")

    def __init__(self, title, text):
        self.title = title
        self.text = text


def _slug(s, n=40):
    keep = "".join(c if c.isalnum() or c in " -_" else " " for c in (s or ""))
    return "-".join(keep.split())[:n].strip("-").lower() or "book"


def _job_dir(job):
    return os.path.join(OUTPUT, job)


def _sse(obj):
    return f"data: {json.dumps(obj)}\n\n"


def save_project(proj):
    jd = _job_dir(proj["job"])
    os.makedirs(jd, exist_ok=True)
    with open(os.path.join(jd, "project.json"), "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=2)


def load_project(job):
    p = os.path.join(_job_dir(job), "project.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _parse_book(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".epub":
        return parse_epub(path)
    return parse_document(path)


def _spans(markers, total_ms):
    """[(start,end)] per chapter from [(title,start)] + total runtime."""
    spans = []
    for i, (_t, start) in enumerate(markers):
        end = markers[i + 1][1] if i + 1 < len(markers) else total_ms
        spans.append((start, max(start, end)))
    return spans


def _shot_from(d):
    s = TL.Shot(**{k: d[k] for k in (
        "id", "chapter_index", "chapter_title", "shot_in_chapter",
        "page_start", "page_end", "text", "start_ms", "end_ms")})
    for k in ("is_chapter_start", "word_count", "prompt", "backend",
              "highlight_score", "highlighted", "image_path", "status",
              "error", "motion"):
        if k in d:
            setattr(s, k, d[k])
    return s


def _shot_json(s):
    return asdict(s) | {"duration_ms": s.duration_ms}


# ── routes ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template(
        "index.html", ffmpeg_ok=ASM.ensure_ffmpeg(), probe=imagegen.probe(),
        presets=list(KB.PRESETS.keys()), styles=list(DIR.StyleBible.PRESETS.keys()),
        default_motion=KB.DEFAULT_MOTION)


@app.route("/api/probe")
def api_probe():
    return jsonify(imagegen.probe() | {"ffmpeg": ASM.ensure_ffmpeg()})


@app.route("/api/page_check", methods=["POST"])
def page_check():
    """Detect embedded page numbers in an uploaded ebook and suggest a
    words-per-page that matches real page density."""
    ebook = request.files.get("ebook")
    if not ebook:
        return jsonify({"has_pages": False})
    tmp = os.path.join(UPLOADS, "_pagecheck" + os.path.splitext(ebook.filename)[1].lower())
    ebook.save(tmp)
    info = {"has_pages": False, "page_count": 0}
    try:
        info = pagemap.detect_pages(tmp)
        if info.get("has_pages"):
            try:
                book = _parse_book(tmp)
                words = sum(len((c.text or "").split()) for c in book.chapters)
                info["total_words"] = words
                info["words_per_page"] = max(120, min(600,
                    round(words / max(1, info["page_count"]))))
            except Exception:
                info["words_per_page"] = 280
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return jsonify(info)


@app.route("/api/sample", methods=["POST"])
def sample():
    """Generate ONE preview image from a random page, using Stable Diffusion
    (falls back to placeholder if SD isn't available)."""
    ebook = request.files.get("ebook")
    if not ebook:
        return jsonify({"error": "Add your ebook above first, then generate a sample."}), 400
    style_key = request.form.get("style_key", "cinematic")
    custom_style = request.form.get("custom_style", "")
    wpp = int(request.form.get("words_per_page", 280) or 280)

    tmp = os.path.join(UPLOADS, "_sample" + os.path.splitext(ebook.filename)[1].lower())
    ebook.save(tmp)
    try:
        book = _parse_book(tmp)
    except Exception as e:
        return jsonify({"error": f"Could not read the ebook: {e}"}), 400
    finally:
        pass

    chapters = [c for c in book.chapters if len((c.text or "").split()) >= 30] or book.chapters
    if not chapters:
        return jsonify({"error": "No readable text found in the ebook."}), 400
    ch = random.choice(chapters)
    words = (ch.text or "").split()
    if len(words) > wpp:
        start = random.randint(0, len(words) - wpp)
        page_text = " ".join(words[start:start + wpp])
        page_no = start // max(1, wpp) + 1
    else:
        page_text = " ".join(words)
        page_no = 1

    shot = TL.Shot(id="sample", chapter_index=0, chapter_title=ch.title,
                   shot_in_chapter=0, page_start=0, page_end=0, text=page_text,
                   start_ms=0, end_ms=1000)
    bible = DIR.StyleBible(style_key, custom_style)
    DIR.direct([shot], bible)
    shot.backend = "stablediffusion" if imagegen.probe()["stablediffusion"] else "placeholder"

    sdir = os.path.join(OUTPUT, "_sample")
    os.makedirs(sdir, exist_ok=True)
    fn = f"sample_{int(time.time())}.jpg"
    # Preview with the SAME model + guidance the real build will use, so the
    # sample actually reflects the chosen style (e.g. the photoreal model).
    sd_opts = {"w": 1024, "h": 576}
    try:
        sd_opts["guidance"] = float(request.form.get("sd_guidance", 1.6))
    except (TypeError, ValueError):
        pass
    if style_key == "photoreal" and not os.environ.get("SEESTORY_SD_MODEL"):
        sd_opts["model"] = imagegen.stablediffusion.PHOTOREAL_MODEL
    try:
        res = imagegen.generate_for(shot, os.path.join(sdir, fn), sd_opts=sd_opts)
    except Exception as e:
        return jsonify({"error": f"Sample generation failed: {e}"}), 500
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    return jsonify({
        "image_url": f"/image/_sample/{fn}",
        "page_text": page_text,
        "prompt": shot.prompt,
        "chapter_title": ch.title,
        "page_no": page_no,
        "backend_used": res["backend"],
        "note": res.get("note", ""),
    })


@app.route("/api/ingest", methods=["POST"])
def ingest():
    """Build the storyboard, streaming progress stages as Server-Sent Events."""
    ebook = request.files.get("ebook")
    audio = request.files.get("audio")
    ts_file = request.files.get("timestamps")
    ts_text = request.form.get("timestamps_text", "")
    opts = request.form.to_dict()

    err = None
    if not ebook:
        err = "Please add your ebook file."
    elif not audio:
        err = "Please add Parroty's audiobook MP3."
    elif not ts_file and not ts_text.strip():
        err = "Please add Parroty's youtube-chapters .txt (or paste the timestamp lines)."

    ts = ts_text
    if ts_file and not err:
        ts = ts_file.read().decode("utf-8", "ignore")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    job = stamp
    jd = _job_dir(job)
    ebook_path = audio_path = cover_path = subtitle_path = None
    book = None
    if not err:
        # Save the ebook to a scratch spot and read its title FIRST, so the
        # output folder can lead with the book name (e.g.
        # origin-robert-langdon-book-5-20260624-181315) instead of a bare stamp.
        ext = os.path.splitext(ebook.filename)[1].lower()
        tmp_dir = _job_dir("_ingest")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_ebook = os.path.join(tmp_dir, f"{stamp}{ext}")
        ebook.save(tmp_ebook)
        try:
            book = _parse_book(tmp_ebook)
        except Exception as e:
            err = f"Couldn't read the ebook: {e}"

        if not err:
            title_slug = _slug(getattr(book, "title", "") or
                               os.path.splitext(ebook.filename)[0])
            job = f"{title_slug}-{stamp}"
            jd = _job_dir(job)
            os.makedirs(os.path.join(jd, "images"), exist_ok=True)
            os.makedirs(os.path.join(jd, "clips"), exist_ok=True)
            ebook_path = os.path.join(jd, "book" + ext)
            os.replace(tmp_ebook, ebook_path)
            audio_path = os.path.join(jd, "audio" + os.path.splitext(audio.filename)[1].lower())
            audio.save(audio_path)
            cover = request.files.get("cover")
            if cover and cover.filename:
                cext = os.path.splitext(cover.filename)[1].lower() or ".jpg"
                cover_path = os.path.join(jd, "cover" + cext)
                cover.save(cover_path)
            sub = request.files.get("subtitle")
            if sub and sub.filename:
                subtitle_path = os.path.join(jd, "subtitles-source.srt")
                sub.save(subtitle_path)
        else:
            try:
                os.remove(tmp_ebook)
            except OSError:
                pass

    @stream_with_context
    def stream():
        if err:
            yield _sse({"type": "error", "message": err})
            return
        try:
            yield _sse({"type": "stage", "pct": 12, "label": "Reading the ebook…"})
            # (already parsed above, so the folder could be named after the book)

            yield _sse({"type": "stage", "pct": 28, "label": "Reading chapter timestamps…"})
            markers = TL.parse_youtube_timestamps(ts)
            if not markers:
                yield _sse({"type": "error", "message": "No timestamps found. Expected "
                            "lines like '00:00 Chapter One'."})
                return

            yield _sse({"type": "stage", "pct": 42, "label": "Measuring the narration…"})
            total_ms = TL.audio_duration_ms(audio_path)
            if total_ms <= 0:
                yield _sse({"type": "error", "message": "Could not read the audio length "
                            "(is ffmpeg installed?)."})
                return

            chapters = book.chapters
            if not chapters or not markers:
                yield _sse({"type": "error", "message":
                    f"Couldn't match the book to the timestamps "
                    f"(found {len(chapters)} chapters in the ebook and "
                    f"{len(markers)} timestamp lines)."})
                return
            # Align to the audio's chapter list: the timestamps are the source of
            # truth, so skip any ebook front/back matter that wasn't narrated.
            align = TL.align_chapters_to_markers(chapters, markers)
            empty_text = 0
            use_chapters = []
            for i, (mtitle, _start) in enumerate(markers):
                src = align["pairs"][i] if i < len(align["pairs"]) else None
                txt = (getattr(src, "text", "") or "") if src is not None else ""
                if not txt:
                    empty_text += 1
                use_chapters.append(_PairedChapter(mtitle, txt))
            spans = _spans(markers, total_ms)

            words_per_page = int(opts.get("words_per_page", 280))
            pages_per_shot = int(opts.get("pages_per_shot", 1))
            mode = opts.get("mode", "both")
            style_key = opts.get("style_key", "cinematic")
            custom_style = opts.get("custom_style", "")
            copilot_every = int(opts.get("copilot_every_pages", 10))
            copilot_cap = int(opts.get("copilot_cap", 30))
            page_basis = opts.get("page_basis", "words")
            page_count = int(opts.get("page_count", 0) or 0)
            try:
                guidance = float(opts.get("sd_guidance", 1.6))
            except (TypeError, ValueError):
                guidance = 1.6

            yield _sse({"type": "stage", "pct": 58, "label": "Splitting into pages…"})
            shots = TL.segment_book(use_chapters, spans, words_per_page=words_per_page,
                                    pages_per_shot=pages_per_shot)

            yield _sse({"type": "stage", "pct": 74, "label": "Writing image prompts…"})
            bible = DIR.StyleBible(style_key, custom_style)
            DIR.direct(shots, bible)

            yield _sse({"type": "stage", "pct": 88, "label": "Choosing image sources…"})
            sd_backend = "stablediffusion" if imagegen.probe()["stablediffusion"] else "placeholder"
            summary = DIR.route_backends(
                shots, mode=mode, sd_backend=sd_backend,
                copilot_every_pages=copilot_every, copilot_cap=copilot_cap)
            default_motion = dict(KB.DEFAULT_MOTION)
            try:
                m = json.loads(opts.get("motion", "") or "{}")
                if isinstance(m, dict):
                    default_motion.update({k: m[k] for k in m if k in KB.DEFAULT_MOTION})
            except Exception:
                pass
            for s in shots:
                s.motion = dict(default_motion)

            proj = {
                "job": job, "title": book.title or "Audiobook",
                "author": getattr(book, "author", "") or "",
                "ebook_file": os.path.basename(ebook_path),
                "audio_file": os.path.basename(audio_path),
                "cover_file": os.path.basename(cover_path) if cover_path else None,
                "total_ms": total_ms,
                "markers": markers,
                "settings": {
                    "mode": mode, "words_per_page": words_per_page,
                    "pages_per_shot": pages_per_shot, "style_key": style_key,
                    "custom_style": custom_style, "copilot_every_pages": copilot_every,
                    "copilot_cap": copilot_cap, "sd_backend": sd_backend,
                    "guidance": guidance,
                    "page_basis": page_basis, "page_count": page_count,
                    "subtitle_mode": opts.get("subtitle_mode", "none"),
                    "subtitle_file": os.path.basename(subtitle_path) if subtitle_path else None,
                    "cover_seconds": COVER_SECONDS,
                    "w": VIDEO_W, "h": VIDEO_H, "fps": VIDEO_FPS,
                },
                "bible": bible.to_json(),
                "shots": [_shot_json(s) for s in shots],
                "routing": summary,
                "alignment": {
                    "skipped": align["skipped"],
                    "empty_text": empty_text,
                    "start_title": align["start_title"],
                    "ebook_chapters": len(chapters),
                    "audio_chapters": len(markers),
                },
            }
            save_project(proj)
            yield _sse({"type": "done", "project": proj})
        except Exception as e:
            yield _sse({"type": "error", "message": f"Could not build the storyboard: {e}"})

    return Response(stream(), mimetype="text/event-stream")


@app.route("/api/project/<job>")
def get_project(job):
    proj = load_project(job)
    return jsonify(proj) if proj else (jsonify({"error": "not found"}), 404)


@app.route("/api/project/<job>/shot/<shot_id>", methods=["POST"])
def update_shot(job, shot_id):
    proj = load_project(job)
    if not proj:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(force=True)
    for s in proj["shots"]:
        if s["id"] == shot_id:
            for k in ("prompt", "backend", "motion"):
                if k in body:
                    s[k] = body[k]
            if "prompt" in body:
                # Remember the user hand-edited this prompt, so a later regenerate
                # won't overwrite it with an auto-rebuilt one.
                s["prompt_edited"] = True
            save_project(proj)
            return jsonify(s)
    return jsonify({"error": "shot not found"}), 404


@app.route("/api/project/<job>/shot/<shot_id>/delete", methods=["POST"])
def delete_shot(job, shot_id):
    """Remove a shot; give its on-screen time to the previous shot so the
    timeline stays continuous. Manual only — never automatic."""
    proj = load_project(job)
    if not proj:
        return jsonify({"error": "not found"}), 404
    shots = proj["shots"]
    idx = next((i for i, s in enumerate(shots) if s["id"] == shot_id), None)
    if idx is None:
        return jsonify({"error": "shot not found"}), 404
    gone = shots.pop(idx)
    # absorb its time into a neighbour
    if idx - 1 >= 0:
        shots[idx - 1]["end_ms"] = gone["end_ms"]
    elif shots:
        shots[idx]["start_ms"] = gone["start_ms"]
    img = gone.get("image_path")
    if img and os.path.exists(os.path.join(_job_dir(job), img)):
        try:
            os.unlink(os.path.join(_job_dir(job), img))
        except OSError:
            pass
    save_project(proj)
    return jsonify({"ok": True, "shots": shots})


@app.route("/api/project/<job>/shot/<shot_id>/regenerate", methods=["POST"])
def regenerate_shot(job, shot_id):
    proj = load_project(job)
    if not proj:
        return jsonify({"error": "not found"}), 404
    sd = proj["shots"]
    rec = next((s for s in sd if s["id"] == shot_id), None)
    if not rec:
        return jsonify({"error": "shot not found"}), 404
    s = _shot_from(rec)
    # Refresh the prompt with the current (improved) prompt logic — strips
    # dialogue, drops genre/cover words, guarantees a real visual style — so an
    # old storyboard benefits without a full rebuild. Skip if the user hand-edited
    # this prompt (we don't clobber their wording).
    if not rec.get("prompt_edited"):
        try:
            st = proj["settings"]
            bible = DIR.StyleBible(st.get("style_key", "cinematic"),
                                   st.get("custom_style", ""),
                                   (proj.get("bible") or {}).get("entities"))
            s.prompt = DIR.build_prompt(s, bible)
            rec["prompt"] = s.prompt
        except Exception:
            pass
    out = os.path.join(_job_dir(job), "images", f"{shot_id}.jpg")
    # A manual regenerate should always give the intended backend a fresh try —
    # clear any per-run Copilot breaker left over from a bulk run.
    if rec.get("backend") == "copilot":
        try:
            from .imagegen import copilot_backend as _cb
            _cb.reset_run_state()
        except Exception:
            pass
    res = imagegen.generate_for(s, out, sd_opts=_sd_opts(proj))
    rec["image_path"] = f"images/{shot_id}.jpg"
    rec["status"] = "done"
    rec["backend_used"] = res["backend"]
    rec["error"] = res.get("error", "")
    save_project(proj)
    return jsonify(rec | {"cache_bust": int(time.time()), "note": res.get("note", "")})


def _sd_opts(proj):
    s = proj["settings"]
    o = {"w": s.get("w", VIDEO_W), "h": s.get("h", VIDEO_H)}
    g = s.get("guidance")
    if g is not None:
        o["guidance"] = g
    # Photorealistic style → load the photoreal checkpoint instead of turbo,
    # unless the user has pinned a specific model via SEESTORY_SD_MODEL.
    if s.get("style_key") == "photoreal" and not os.environ.get("SEESTORY_SD_MODEL"):
        from .imagegen import stablediffusion as SD
        o["model"] = SD.PHOTOREAL_MODEL
    return o


@app.route("/api/project/<job>/generate", methods=["POST"])
def generate_all(job):
    proj = load_project(job)
    if not proj:
        return jsonify({"error": "not found"}), 404

    @stream_with_context
    def stream():
        sd_opts = _sd_opts(proj)
        shots = proj["shots"]
        pending = [s for s in shots if s.get("status") != "done"
                   or not s.get("image_path")]
        yield _sse({"type": "start", "total": len(pending),
                    "already": len(shots) - len(pending)})

        # If any shot is meant to use Copilot, reset its per-run state and report
        # its status once up front so any fallback reason is visible immediately.
        if any(s.get("backend") == "copilot" for s in pending):
            try:
                from .imagegen import copilot_backend as _cb
                _cb.reset_run_state()
                msg = _cb.diagnose()
                sys.stderr.write(f"[seestory] copilot: {msg}\n")
                sys.stderr.flush()
                yield _sse({"type": "info", "message": f"Copilot: {msg}"})
            except Exception:
                pass

        done = 0
        warned_copilot = False
        for rec in shots:
            if rec.get("status") == "done" and rec.get("image_path"):
                continue
            s = _shot_from(rec)
            out = os.path.join(_job_dir(job), "images", f"{rec['id']}.jpg")
            try:
                res = imagegen.generate_for(s, out, sd_opts=sd_opts)
                rec["image_path"] = f"images/{rec['id']}.jpg"
                rec["status"] = "done"
                rec["backend_used"] = res["backend"]
                rec["error"] = res.get("error", "")
                note = res.get("note", "")
            except Exception as e:
                rec["status"] = "error"
                rec["error"] = str(e)
                note = "error"
            # The first time a Copilot shot falls back, surface WHY prominently
            # (once) in the build log + console, so it isn't lost in the per-shot
            # noise.
            if (not warned_copilot and rec.get("backend") == "copilot"
                    and rec.get("backend_used") not in (None, "", "copilot")):
                warned_copilot = True
                detail = rec.get("error") or "Copilot was unavailable."
                sys.stderr.write(f"[seestory] copilot fallback: {detail}\n")
                sys.stderr.flush()
                yield _sse({"type": "info", "message": detail})
            done += 1
            save_project(proj)
            yield _sse({"type": "shot", "id": rec["id"], "done": done,
                        "total": len(pending), "status": rec["status"],
                        "backend_used": rec.get("backend_used", ""),
                        "note": note, "error": rec.get("error", ""),
                        "cache_bust": int(time.time())})
        yield _sse({"type": "complete", "done": done})

    return Response(stream(), mimetype="text/event-stream")


@app.route("/api/project/<job>/assemble", methods=["POST"])
def assemble(job):
    proj = load_project(job)
    if not proj:
        return jsonify({"error": "not found"}), 404

    @stream_with_context
    def stream():
        jd = _job_dir(job)
        s = proj["settings"]
        w, h, fps = s.get("w", VIDEO_W), s.get("h", VIDEO_H), s.get("fps", VIDEO_FPS)
        ready = [r for r in proj["shots"] if r.get("image_path")
                 and os.path.exists(os.path.join(jd, r["image_path"]))]
        if not ready:
            yield _sse({"type": "error", "message": "No images yet — generate first."})
            return
        yield _sse({"type": "start", "total": len(ready)})

        clip_paths = []
        for i, rec in enumerate(ready):
            clip = os.path.join(jd, "clips", f"{rec['id']}.mp4")
            img = os.path.join(jd, rec["image_path"])
            dur = max(0.3, (rec["end_ms"] - rec["start_ms"]) / 1000.0)
            try:
                KB.render_clip(img, clip, dur, rec.get("motion") or {},
                               w=w, h=h, fps=fps)
                clip_paths.append(clip)
            except Exception as e:
                yield _sse({"type": "error",
                            "message": f"Clip failed on shot {rec['id']}: {e}"})
                return
            yield _sse({"type": "clip", "done": i + 1, "total": len(ready)})

        base = _slug(proj["title"])
        out_mp4 = os.path.join(jd, f"seestory-{base}.mp4")
        audio = os.path.join(jd, proj["audio_file"])
        markers = [tuple(m) for m in proj["markers"]]

        # ── optional book cover: a title-card pre-roll, like opening a book ──
        lead_ms = 0
        cover_file = proj.get("cover_file")
        if cover_file and os.path.exists(os.path.join(jd, cover_file)):
            cover_secs = float(s.get("cover_seconds", COVER_SECONDS))
            cover_clip = os.path.join(jd, "clips", "_cover.mp4")
            try:
                ASM.render_cover_clip(os.path.join(jd, cover_file), cover_clip,
                                      cover_secs, w, h, fps)
                clip_paths = [cover_clip] + clip_paths
                lead_ms = int(cover_secs * 1000)
            except Exception as e:
                yield _sse({"type": "mux", "message": f"(cover skipped: {e})"})

        # offset chapters by the cover; keep a 0:00 entry so YouTube accepts them
        if lead_ms:
            markers_out = [(proj["title"], 0)] + [(t, int(ms) + lead_ms) for (t, ms) in markers]
        else:
            markers_out = markers
        total_out = proj["total_ms"] + lead_ms

        # ── subtitles: Parroty's exact .srt if uploaded, else auto-approx ───
        sub_mode = s.get("subtitle_mode", "none")
        sub_path = None
        if sub_mode in ("soft", "burn"):
            src = s.get("subtitle_file")
            if src and os.path.exists(os.path.join(jd, src)):
                txt = open(os.path.join(jd, src), encoding="utf-8", errors="ignore").read()
                txt = SUB.shift_srt(txt, lead_ms)
                sub_path = os.path.join(jd, f"subtitles-{base}.srt")
                with open(sub_path, "w", encoding="utf-8") as f:
                    f.write(txt)
            else:
                srt = SUB.build_srt(proj["shots"], lead_ms=lead_ms)
                if srt.strip():
                    sub_path = os.path.join(jd, f"subtitles-{base}.srt")
                    with open(sub_path, "w", encoding="utf-8") as f:
                        f.write(srt)

        yield _sse({"type": "mux", "message": "Muxing audio + chapters…"})

        def prog(frac):
            pass  # mux is fast; per-clip progress already streamed
        try:
            ASM.assemble_video(clip_paths, audio, out_mp4, markers=markers_out,
                               total_ms=total_out, progress_callback=prog,
                               lead_silence_ms=lead_ms,
                               subtitle_path=sub_path, subtitle_mode=sub_mode)
        except Exception as e:
            yield _sse({"type": "error", "message": f"Assembly failed: {e}"})
            return

        # sidecar chapter files (offset to match the final video)
        yt = ASM.build_youtube_timestamps(markers_out)
        with open(os.path.join(jd, f"youtube-chapters-{base}.txt"), "w",
                  encoding="utf-8") as f:
            f.write(yt)
        drive = f"drive-chapters-{base}.html"
        try:
            with open(os.path.join(jd, drive), "w", encoding="utf-8") as f:
                f.write(ASM.build_drive_chapter_page(markers_out, proj["title"],
                                                     total_out))
        except Exception:
            drive = None
        proj["video_file"] = os.path.basename(out_mp4)
        save_project(proj)
        done = {"type": "done", "video": os.path.basename(out_mp4),
                "timestamps_file": f"youtube-chapters-{base}.txt",
                "drive_file": drive}
        if sub_path:
            done["subtitle_file"] = os.path.basename(sub_path)
            done["subtitle_mode"] = sub_mode
        yield _sse(done)

    return Response(stream(), mimetype="text/event-stream")


def _session_dir_safe(job):
    """Resolve a job to its folder under OUTPUT, or None — guards against path
    traversal and non-session folders (_sample, _ingest, .gitkeep)."""
    name = os.path.basename(os.path.normpath(job or ""))
    if not name or name.startswith("_") or name.startswith("."):
        return None
    d = os.path.join(OUTPUT, name)
    if os.path.dirname(os.path.abspath(d)) != os.path.abspath(OUTPUT):
        return None
    return d if os.path.isdir(d) else None


@app.route("/api/project/<job>", methods=["DELETE"])
def delete_project(job):
    """Remove a single recent session (its folder, images and any built video)."""
    d = _session_dir_safe(job)
    if not d:
        return jsonify({"error": "No such session."}), 404
    try:
        shutil.rmtree(d)
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/copilot_test", methods=["POST"])
def copilot_test():
    """Fire one real Copilot call and report whether it worked + why not."""
    from .imagegen import copilot_backend as cb
    ok, detail = cb.test()
    return jsonify({"ok": ok, "detail": detail})


@app.route("/api/projects/clear", methods=["POST"])
def clear_projects():
    """Remove all recent sessions at once."""
    removed = 0
    try:
        names = os.listdir(OUTPUT)
    except OSError:
        names = []
    for name in names:
        if name.startswith("_") or name.startswith("."):
            continue
        d = os.path.join(OUTPUT, name)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "project.json")):
            try:
                shutil.rmtree(d)
                removed += 1
            except OSError:
                pass
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/project/<job>/motion_all", methods=["POST"])
def motion_all(job):
    """Set the motion of every shot at once (the storyboard 'apply to all')."""
    proj = load_project(job)
    if not proj:
        return jsonify({"error": "Project not found."}), 404
    m = (request.get_json(force=True) or {}).get("motion") or {}
    clean = {k: m[k] for k in m if k in KB.DEFAULT_MOTION}
    for s in proj["shots"]:
        mm = dict(KB.DEFAULT_MOTION)
        mm.update(s.get("motion") or {})
        mm.update(clean)
        s["motion"] = mm
    save_project(proj)
    return jsonify({"ok": True, "count": len(proj["shots"])})


@app.route("/api/projects")
def list_projects():
    """Recent sessions, for resume/restore."""
    items = []
    try:
        names = os.listdir(OUTPUT)
    except OSError:
        names = []
    for name in names:
        if name.startswith("_") or name.startswith("."):
            continue
        pj = os.path.join(OUTPUT, name, "project.json")
        if not os.path.exists(pj):
            continue
        try:
            with open(pj, encoding="utf-8") as f:
                p = json.load(f)
        except Exception:
            continue
        shots = p.get("shots", [])
        done = sum(1 for s in shots if s.get("status") == "done" and s.get("image_path"))
        try:
            modified = os.path.getmtime(pj)
        except OSError:
            modified = 0
        items.append({
            "job": p.get("job", name),
            "title": p.get("title", "Audiobook"),
            "shots": len(shots), "done": done,
            "has_video": bool(p.get("video_file")),
            "video_file": p.get("video_file"),
            "total_ms": p.get("total_ms", 0),
            "modified": modified,
        })
    items.sort(key=lambda x: x["job"], reverse=True)
    return jsonify({"projects": items[:25]})


@app.route("/api/motion_preview", methods=["POST"])
def motion_preview():
    """Render a short Ken Burns clip so the user can see the motion before
    committing. Uses the latest sample image if there is one, else a placeholder."""
    body = request.get_json(force=True) or {}
    motion = body.get("motion") or {}
    sdir = os.path.join(OUTPUT, "_sample")
    os.makedirs(sdir, exist_ok=True)
    imgs = sorted(f for f in os.listdir(sdir)
                  if f.startswith("sample_") and f.endswith(".jpg"))
    if imgs:
        src = os.path.join(sdir, imgs[-1])
    else:
        from .imagegen import placeholder
        src = os.path.join(sdir, "preview_src.jpg")
        placeholder.generate("a sweeping landscape, motion preview", src,
                             w=1024, h=576, label="preview")
    out = os.path.join(sdir, f"preview_{int(time.time())}.mp4")
    # Length follows the drift pace (set by speed) plus a short hold, so the
    # preview shows the motion completing and settling — exactly the real look.
    speed = float((motion or {}).get("speed", 50))
    mdur = KB.motion_seconds(speed, 999)
    preview_dur = max(3.0, min(14.0, mdur + 2.0))
    try:
        KB.render_clip(src, out, preview_dur, motion, w=VIDEO_W, h=VIDEO_H, fps=VIDEO_FPS)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"clip_url": f"/image/_sample/{os.path.basename(out)}"})


@app.route("/image/<job>/<path:fn>")
def image(job, fn):
    return send_from_directory(os.path.join(_job_dir(job)), fn)


@app.route("/download/<job>/<path:fn>")
def download(job, fn):
    return send_from_directory(_job_dir(job), fn, as_attachment=True)


# ── startup ──────────────────────────────────────────────────────────────
def _raise_priority():
    """Best-effort: keep GPU work from being throttled when the window is in
    the background (mirrors the issue Parroty hit on laptops)."""
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(-1, 0x00000080)  # HIGH
    except Exception:
        pass


def _open_browser():
    time.sleep(1.2)
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    except Exception:
        pass


def main():
    _raise_priority()
    if "--no-browser" not in sys.argv:
        threading.Thread(target=_open_browser, daemon=True).start()
    print(f"SeeStory running at http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
