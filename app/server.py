"""
SeeStory — turn a Parroty audiobook into a watch-along illustrated video.

Pipeline:  ebook + Parroty MP3 + Parroty timestamps
           -> chapters (same parser as Parroty) mapped onto the audio timeline
           -> shots (one image/clip each), prompted by the director
           -> local GPU-generated images
           -> Ken Burns motion clips
           -> one chaptered MP4 synced to the narration.

Runs locally at http://127.0.0.1:5001 so it sits beside Parroty (port 5000).
"""

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
from . import desktop_runtime as DESKTOP
from . import validation as VALID

COVER_SECONDS = 6.0  # how long the book cover holds at the very start

# ── paths ────────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(BASE, "output")
UPLOADS = os.path.join(BASE, "uploads")
os.makedirs(OUTPUT, exist_ok=True)
os.makedirs(UPLOADS, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024 * 1024  # 5 GB (long audiobooks)
# This is a local desktop app that is upgraded in-place. A persistent Chrome/Edge
# app profile is useful for clean startup, but browser caching must never leave an
# old HTML/JS interface visible after an upgrade.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.after_request
def _disable_ui_cache(response):
    if request.path == "/" or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

PORT = int(os.environ.get("SEESTORY_PORT", "5001"))
VIDEO_W = int(os.environ.get("SEESTORY_W", "1280"))
VIDEO_H = int(os.environ.get("SEESTORY_H", "720"))
VIDEO_FPS = int(os.environ.get("SEESTORY_FPS", "30"))

DESKTOP.install(app, base_dir=BASE, port=PORT)


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
    """Internal job folder helper for server-created safe job names."""
    return os.path.join(OUTPUT, job)


def _session_dir_safe(job):
    return VALID.safe_session_dir(OUTPUT, job)


def _temp_upload_path(prefix: str, filename: str) -> str:
    return VALID.temp_upload_path(UPLOADS, prefix, filename)


def _clean_motion(raw) -> dict:
    return VALID.clean_motion(raw, KB.DEFAULT_MOTION)


def _sse(obj):
    return f"data: {json.dumps(obj)}\n\n"


def save_project(proj):
    jd = _job_dir(proj["job"])
    os.makedirs(jd, exist_ok=True)
    with open(os.path.join(jd, "project.json"), "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=2)


def load_project(job):
    folder = _session_dir_safe(job)
    if not folder:
        return None
    p = os.path.join(folder, "project.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


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
    for k in ("is_chapter_start", "word_count", "prompt", "image_path",
              "status", "error", "motion"):
        if k in d:
            setattr(s, k, d[k])
    return s


def _shot_json(s):
    return asdict(s) | {"duration_ms": s.duration_ms}


# ── routes ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template(
        "index.html", presets=list(KB.PRESETS.keys()),
        styles=list(DIR.StyleBible.PRESETS.keys()), default_motion=KB.DEFAULT_MOTION)


@app.route("/api/health")
def api_health():
    """Lightweight launcher readiness check; avoids loading GPU libraries."""
    return jsonify({"ok": True})


@app.route("/api/probe")
def api_probe():
    """Detailed local diagnostics, intentionally separate from startup readiness."""
    return jsonify(imagegen.probe() | {"ffmpeg": ASM.ensure_ffmpeg()})


@app.route("/api/page_check", methods=["POST"])
def page_check():
    """Detect embedded page numbers in an uploaded ebook and suggest a
    words-per-page that matches real page density."""
    ebook = request.files.get("ebook")
    if not ebook:
        return jsonify({"has_pages": False})
    tmp = _temp_upload_path("pagecheck", ebook.filename)
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
    """Generate one local preview image from a random page."""
    ebook = request.files.get("ebook")
    if not ebook:
        return jsonify({"error": "Add your ebook above first, then generate a sample."}), 400

    style_key = request.form.get("style_key", "cinematic")
    custom_style = request.form.get("custom_style", "")
    try:
        wpp = max(80, min(2000, int(request.form.get("words_per_page", 280) or 280)))
    except (TypeError, ValueError):
        wpp = 280

    tmp = _temp_upload_path("sample", ebook.filename)
    ebook.save(tmp)
    try:
        try:
            book = _parse_book(tmp)
        except Exception as exc:
            return jsonify({"error": f"Could not read the ebook: {exc}"}), 400

        chapters = [c for c in book.chapters if len((c.text or "").split()) >= 30] or book.chapters
        if not chapters:
            return jsonify({"error": "No readable text found in the ebook."}), 400

        ch = random.choice(chapters)
        words = (ch.text or "").split()
        if len(words) > wpp:
            word_start = random.randint(0, len(words) - wpp)
            page_text = " ".join(words[word_start:word_start + wpp])
            page_no = word_start // max(1, wpp) + 1
        else:
            page_text = " ".join(words)
            page_no = 1

        shot = TL.Shot(
            id="sample", chapter_index=0, chapter_title=ch.title, shot_in_chapter=0,
            page_start=0, page_end=0, text=page_text, start_ms=0, end_ms=1000,
        )
        DIR.direct([shot], DIR.StyleBible(style_key, custom_style))

        sdir = os.path.join(OUTPUT, "_sample")
        os.makedirs(sdir, exist_ok=True)
        fn = f"sample_{int(time.time())}.jpg"
        opts = {"w": 1024, "h": 576}
        if not os.environ.get("SEESTORY_SD_MODEL"):
            SD = imagegen.stablediffusion
            if style_key == "photoreal":
                opts["model"] = SD.PHOTOREAL_MODEL
            elif style_key == "cinematic":
                opts["model"] = SD.DEFAULT_MODEL
            else:
                opts["model"] = SD.ARTISTIC_MODEL
        try:
            imagegen.generate_for(shot, os.path.join(sdir, fn), sd_opts=opts)
        except Exception as exc:
            return jsonify({"error": f"Sample generation failed: {exc}"}), 500

        return jsonify({
            "image_url": f"/image/_sample/{fn}",
            "page_text": page_text,
            "prompt": shot.prompt,
            "chapter_title": ch.title,
            "page_no": page_no,
        })
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


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

            try:
                words_per_page = max(80, min(2000, int(opts.get("words_per_page", 280))))
            except (TypeError, ValueError):
                words_per_page = 280
            try:
                pages_per_shot = max(1, min(20, int(opts.get("pages_per_shot", 1))))
            except (TypeError, ValueError):
                pages_per_shot = 1
            style_key = opts.get("style_key", "cinematic")
            if style_key not in DIR.StyleBible.PRESETS:
                style_key = "cinematic"
            custom_style = (opts.get("custom_style", "") or "")[:500]
            page_basis = "embedded" if opts.get("page_basis") == "embedded" else "words"
            try:
                page_count = max(0, min(1_000_000, int(opts.get("page_count", 0) or 0)))
            except (TypeError, ValueError):
                page_count = 0

            yield _sse({"type": "stage", "pct": 58, "label": "Splitting into pages…"})
            shots = TL.segment_book(use_chapters, spans, words_per_page=words_per_page,
                                    pages_per_shot=pages_per_shot)

            yield _sse({"type": "stage", "pct": 74, "label": "Writing image prompts…"})
            bible = DIR.StyleBible(style_key, custom_style)
            DIR.direct(shots, bible)

            yield _sse({"type": "stage", "pct": 88, "label": "Preparing the storyboard…"})
            try:
                default_motion = _clean_motion(json.loads(opts.get("motion", "") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                default_motion = dict(KB.DEFAULT_MOTION)
            for shot in shots:
                shot.motion = dict(default_motion)

            proj = {
                "job": job, "title": book.title or "Audiobook",
                "author": getattr(book, "author", "") or "",
                "ebook_file": os.path.basename(ebook_path),
                "audio_file": os.path.basename(audio_path),
                "cover_file": os.path.basename(cover_path) if cover_path else None,
                "total_ms": total_ms,
                "markers": markers,
                "settings": {
                    "words_per_page": words_per_page,
                    "pages_per_shot": pages_per_shot, "style_key": style_key,
                    "custom_style": custom_style,
                    "page_basis": page_basis, "page_count": page_count,
                    "subtitle_mode": opts.get("subtitle_mode", "none"),
                    "subtitle_file": os.path.basename(subtitle_path) if subtitle_path else None,
                    "cover_seconds": COVER_SECONDS,
                    "w": VIDEO_W, "h": VIDEO_H, "fps": VIDEO_FPS,
                },
                "bible": bible.to_json(),
                "shots": [_shot_json(s) for s in shots],
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
            if "prompt" in body:
                s["prompt"] = str(body.get("prompt") or "")[:2000]
                # Remember the user hand-edited this prompt, so a later regenerate
                # won't overwrite it with an auto-rebuilt one.
                s["prompt_edited"] = True
            if "motion" in body:
                s["motion"] = _clean_motion(body.get("motion"))
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
    folder = _session_dir_safe(job)
    img = gone.get("image_path")
    if folder and img and os.path.exists(os.path.join(folder, img)):
        try:
            os.unlink(os.path.join(folder, img))
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
    folder = _session_dir_safe(job)
    if not folder:
        return jsonify({"error": "Project folder is missing."}), 404
    out = os.path.join(folder, "images", f"{shot_id}.jpg")
    try:
        imagegen.generate_for(s, out, sd_opts=_sd_opts(proj))
    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = str(exc)
        save_project(proj)
        return jsonify({"error": str(exc)}), 500
    rec["image_path"] = f"images/{shot_id}.jpg"
    rec["status"] = "done"
    rec["error"] = ""
    save_project(proj)
    return jsonify(rec | {"cache_bust": int(time.time())})


def _sd_opts(proj):
    settings = proj["settings"]
    opts = {"w": settings.get("w", VIDEO_W), "h": settings.get("h", VIDEO_H)}
    # An explicit SEESTORY_SD_MODEL pins every style to one user-selected model.
    if os.environ.get("SEESTORY_SD_MODEL"):
        return opts
    from .imagegen import stablediffusion as SD
    style = settings.get("style_key", "cinematic")
    if style == "photoreal":
        opts["model"] = SD.PHOTOREAL_MODEL
    elif style == "cinematic":
        opts["model"] = SD.DEFAULT_MODEL
    else:
        opts["model"] = SD.ARTISTIC_MODEL
    return opts


@app.route("/api/project/<job>/generate", methods=["POST"])
def generate_all(job):
    proj = load_project(job)
    if not proj:
        return jsonify({"error": "not found"}), 404

    @stream_with_context
    def stream():
        folder = _session_dir_safe(job)
        if not folder:
            yield _sse({"type": "error", "message": "Project folder is missing."})
            return
        sd_opts = _sd_opts(proj)
        shots = proj["shots"]
        pending = [shot for shot in shots
                   if shot.get("status") != "done" or not shot.get("image_path")]
        yield _sse({"type": "start", "total": len(pending),
                    "already": len(shots) - len(pending)})

        done = 0
        failed = 0
        for rec in shots:
            if rec.get("status") == "done" and rec.get("image_path"):
                continue
            shot = _shot_from(rec)
            out = os.path.join(folder, "images", f"{rec['id']}.jpg")
            try:
                imagegen.generate_for(shot, out, sd_opts=sd_opts)
                rec["image_path"] = f"images/{rec['id']}.jpg"
                rec["status"] = "done"
                rec["error"] = ""
            except Exception as exc:
                failed += 1
                rec["status"] = "error"
                rec["error"] = str(exc)
                rec["image_path"] = None
            done += 1
            save_project(proj)
            yield _sse({
                "type": "shot", "id": rec["id"], "done": done,
                "total": len(pending), "status": rec["status"],
                "error": rec.get("error", ""), "cache_bust": int(time.time()),
            })
        yield _sse({"type": "complete", "done": done, "failed": failed})

    return Response(stream(), mimetype="text/event-stream")


@app.route("/api/project/<job>/assemble", methods=["POST"])
def assemble(job):
    proj = load_project(job)
    if not proj:
        return jsonify({"error": "not found"}), 404

    @stream_with_context
    def stream():
        jd = _session_dir_safe(job)
        if not jd:
            yield _sse({"type": "error", "message": "Project folder is missing."})
            return
        s = proj["settings"]
        w, h, fps = s.get("w", VIDEO_W), s.get("h", VIDEO_H), s.get("fps", VIDEO_FPS)
        ready = [rec for rec in proj["shots"] if rec.get("image_path")
                 and os.path.exists(os.path.join(jd, rec["image_path"]))]
        missing = len(proj["shots"]) - len(ready)
        if missing:
            yield _sse({
                "type": "error",
                "message": f"{missing} storyboard image(s) are missing. Generate or regenerate them before stitching."
            })
            return
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
    motion = _clean_motion((request.get_json(force=True) or {}).get("motion"))
    for shot in proj["shots"]:
        shot["motion"] = dict(motion)
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
            "has_video": bool(p.get("video_file") and
                              os.path.exists(os.path.join(OUTPUT, name, p["video_file"]))),
            "video_file": p.get("video_file"),
            "total_ms": p.get("total_ms", 0),
            "modified": modified,
        })
    items.sort(key=lambda x: x["job"], reverse=True)
    return jsonify({"projects": items[:25]})


@app.route("/api/motion_preview", methods=["POST"])
def motion_preview():
    """Render a short Ken Burns clip so the user can see motion before committing.
    The latest generated sample is used when available; otherwise a neutral local
    preview frame is created solely for this animation preview."""
    body = request.get_json(force=True) or {}
    motion = _clean_motion(body.get("motion"))
    sdir = os.path.join(OUTPUT, "_sample")
    os.makedirs(sdir, exist_ok=True)
    imgs = sorted(f for f in os.listdir(sdir)
                  if f.startswith("sample_") and f.endswith(".jpg"))
    if imgs:
        src = os.path.join(sdir, imgs[-1])
    else:
        from . import preview_frame
        src = os.path.join(sdir, "preview_src.jpg")
        preview_frame.create(src, w=1024, h=576)
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
    if job == "_sample":
        folder = os.path.join(OUTPUT, "_sample")
    else:
        folder = _session_dir_safe(job)
    if not folder:
        return jsonify({"error": "not found"}), 404
    return send_from_directory(folder, fn)


@app.route("/download/<job>/<path:fn>")
def download(job, fn):
    folder = _session_dir_safe(job)
    if not folder:
        return jsonify({"error": "not found"}), 404
    return send_from_directory(folder, fn, as_attachment=True)


# ── startup ──────────────────────────────────────────────────────────────
def _open_browser():
    time.sleep(1.2)
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    except Exception:
        pass


def main():
    DESKTOP.start()
    if "--no-browser" not in sys.argv:
        threading.Thread(target=_open_browser, daemon=True).start()
    print(f"SeeStory running at http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
