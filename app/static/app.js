/* SeeStory front-end ----------------------------------------------------------
   Four steps: ingest -> storyboard -> generate -> assemble.
   The server returns a project JSON on ingest; everything after is edits to that
   project plus two server-sent-event streams (generate, assemble).            */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const CFG = window.SEESTORY;

let PROJECT = null;          // the live project object from the server

/* ── tiny helpers ──────────────────────────────────────────────────────── */
function fmtMs(ms) {
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60), x = s % 60;
  return m + ":" + String(x).padStart(2, "0");
}
function fmtWhen(sec) {
  if (!sec) return "";
  const d = new Date(sec * 1000);
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return "today " + time;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "yesterday " + time;
  return d.toLocaleDateString([], { month: "short", day: "numeric" }) + ", " + time;
}
function show(id) { $("#" + id).classList.remove("hidden"); }
function hide(id) { $("#" + id).classList.add("hidden"); }
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
}
function jobPart() { return encodeURIComponent(PROJECT?.job || ""); }
function imgUrl(path, bust) {
  const safePath = String(path || "").split("/").map(encodeURIComponent).join("/");
  return `/image/${jobPart()}/${safePath}` + (bust ? `?t=${encodeURIComponent(bust)}` : "");
}

/* build/error log shared by the generate + assemble steps */
function logLine(msg, kind = "") {
  const box = $("#log-lines");
  if (!box) return;
  const t = new Date().toLocaleTimeString();
  const div = document.createElement("div");
  div.className = "log-line" + (kind ? " " + kind : "");
  div.textContent = `[${t}] ${msg}`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  const c = $("#log-count");
  if (c) c.textContent = `(${box.children.length})`;
}

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 400); }, 7000);
}

let PAGE_BASIS = "words";
let PAGE_COUNT = 0;
/* read a fetch() SSE stream, calling onEvent(obj) per "data:" line */
async function readSSE(resp, onEvent) {
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = chunk.split("\n").find(l => l.startsWith("data:"));
      if (line) {
        let event;
        try { event = JSON.parse(line.slice(5).trim()); }
        catch { continue; }
        onEvent(event);
      }
    }
  }
}

/* ── STEP 1 · ingest ───────────────────────────────────────────────────── */
const files = { ebook: null, audio: null, ts: null, cover: null, subtitle: null };

function wireDrop(kind) {
  const drop = $("#drop-" + kind);
  const input = $("#file-" + kind);
  const sub = $("#ds-" + kind);
  input.addEventListener("change", () => {
    if (input.files[0]) {
      files[kind] = input.files[0];
      sub.textContent = input.files[0].name;
      drop.classList.add("filled");
      refreshIngestBtn();
    }
  });
  ["dragenter", "dragover"].forEach(e => drop.addEventListener(e, ev => {
    ev.preventDefault(); drop.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach(e => drop.addEventListener(e, ev => {
    ev.preventDefault(); drop.classList.remove("drag");
  }));
  drop.addEventListener("drop", ev => {
    const f = ev.dataTransfer.files[0];
    if (f) { input.files = ev.dataTransfer.files; input.dispatchEvent(new Event("change")); }
  });
}
["ebook", "audio", "ts", "cover"].forEach(wireDrop);
$("#ts-paste").addEventListener("input", refreshIngestBtn);

/* subtitles: show the upload only when subtitles are on */
$("#subtitle_mode").addEventListener("change", () => {
  $("#sub-upload").classList.toggle("hidden", $("#subtitle_mode").value === "none");
});
$("#file-subtitle").addEventListener("change", () => {
  const f = $("#file-subtitle").files[0];
  if (f) { files.subtitle = f; $("#ds-subtitle").textContent = "✓ " + f.name + " (exact timing)"; }
});

/* when the ebook is chosen, check it for embedded page numbers */
$("#file-ebook").addEventListener("change", () => { if (files.ebook) checkPages(); });

async function checkPages() {
  const el = $("#page-status");
  el.className = "checkline"; el.classList.remove("hidden");
  el.textContent = "Checking for embedded page numbers…";
  try {
    const fd = new FormData(); fd.append("ebook", files.ebook);
    const d = await (await fetch("/api/page_check", { method: "POST", body: fd })).json();
    if (d.has_pages) {
      PAGE_BASIS = "embedded"; PAGE_COUNT = d.page_count;
      $("#words_per_page").value = d.words_per_page;
      el.className = "checkline ok";
      el.innerHTML = `✓ Found <b>${d.page_count}</b> embedded page numbers — sized to ~${d.words_per_page} words per page to match.`;
    } else {
      PAGE_BASIS = "words"; PAGE_COUNT = 0;
      el.className = "checkline none";
      el.textContent = "No embedded page numbers in this ebook — using the words-per-page value below.";
    }
  } catch {
    el.classList.add("hidden");
  }
}

/* sample image preview */
async function generateSample() {
  if (!files.ebook) { $("#sample-hint").textContent = "Add the ebook above first."; return; }
  show("sample-panel");
  const wrap = $("#sample-imgwrap");
  wrap.innerHTML = `<div class="spin"></div>`;
  $("#sample-meta").textContent = "Generating sample…";
  $("#sample-page").textContent = ""; $("#sample-prompt").textContent = "";
  try {
    const fd = new FormData();
    fd.append("ebook", files.ebook);
    fd.append("style_key", $("#style_key").value);
    fd.append("custom_style", $("#custom_style").value);
    fd.append("words_per_page", $("#words_per_page").value);
    const r = await fetch("/api/sample", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "Sample failed.");
    wrap.innerHTML = `<img src="${escapeHtml(d.image_url)}?t=${Date.now()}" alt="">`;
    $("#sample-meta").innerHTML = `<b>${escapeHtml(d.chapter_title)}</b> · ~page ${Number(d.page_no) || 1}`;
    $("#sample-page").textContent = d.page_text;
    $("#sample-prompt").textContent = "Prompt: " + d.prompt;
  } catch (e) {
    wrap.innerHTML = `<span class="empty">${escapeHtml(e.message)}</span>`;
    $("#sample-meta").textContent = "";
  }
}
$("#btn-sample").addEventListener("click", generateSample);
$("#btn-sample-again").addEventListener("click", generateSample);

function refreshIngestBtn() {
  const ok = files.ebook && files.audio && (files.ts || $("#ts-paste").value.trim());
  $("#btn-ingest").disabled = !ok;
}
refreshIngestBtn();


$("#btn-ingest").addEventListener("click", async () => {
  const btn = $("#btn-ingest");
  btn.disabled = true; btn.textContent = "Building…";
  $("#ingest-err").textContent = "";
  const bar = $("#ingest-pbar"), fill = $("#ingest-fill"), lab = $("#ingest-label");
  bar.classList.remove("hidden"); fill.style.width = "0%"; lab.textContent = "Starting…";
  const fd = new FormData();
  fd.append("ebook", files.ebook);
  fd.append("audio", files.audio);
  if (files.ts) fd.append("timestamps", files.ts);
  fd.append("timestamps_text", $("#ts-paste").value);
  fd.append("page_basis", PAGE_BASIS);
  fd.append("page_count", PAGE_COUNT);
  fd.append("motion", JSON.stringify(getGlobalMotion()));
  if (files.cover) fd.append("cover", files.cover);
  if (files.subtitle) fd.append("subtitle", files.subtitle);
  fd.append("subtitle_mode", $("#subtitle_mode").value);
  ["words_per_page", "pages_per_shot", "style_key", "custom_style"]
    .forEach(k => fd.append(k, $("#" + k).value));

  let project = null, ingestErr = null;
  try {
    const r = await fetch("/api/ingest", { method: "POST", body: fd });
    const ct = r.headers.get("content-type") || "";
    if (!ct.includes("text/event-stream")) {
      // server returned a plain error (not the stream) — surface it
      let msg = `Server error (${r.status}).`;
      try { const j = await r.json(); msg = j.error || msg; }
      catch { try { const t = await r.text(); if (t) msg = t.slice(0, 300); } catch {} }
      throw new Error(msg);
    }
    await readSSE(r, ev => {
      if (ev.type === "stage") { fill.style.width = ev.pct + "%"; lab.textContent = ev.label; }
      else if (ev.type === "done") { project = ev.project; fill.style.width = "100%"; lab.textContent = "Done"; }
      else if (ev.type === "error") { ingestErr = ev.message; }
    });
    if (ingestErr) throw new Error(ingestErr);
    if (!project) throw new Error("Build finished without a storyboard. Check that the timestamps match the book's chapters.");
    PROJECT = project;
    renderBoard();                       // outside the stream, so its errors are visible
    hide("step-ingest"); hide("resume-bar"); show("step-board");
    $("#step-board").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    $("#ingest-err").textContent = e.message || "Build failed.";
    bar.classList.add("hidden");
  } finally {
    btn.disabled = false; btn.textContent = "Build the storyboard →";
  }
});

/* ── global motion controls + preview ──────────────────────────────────── */
let MOTION_ENABLED = true;
function getGlobalMotion() {
  const m = {
    zoom: $("#gm-zoom").value, pan: $("#gm-pan").value,
    intensity: Number($("#gm-intensity").value), speed: Number($("#gm-speed").value),
    fade_in: Number($("#gm-fade_in").value), fade_out: Number($("#gm-fade_out").value),
    opacity: Number($("#gm-opacity").value),
  };
  if (!MOTION_ENABLED) { m.zoom = "none"; m.pan = "none"; m.intensity = 0; }
  return m;
}
(function wireGlobalMotion() {
  const en = $("#gm-enabled");
  if (en) en.addEventListener("change", () => {
    MOTION_ENABLED = en.checked;
    $("#gmotion-body").classList.toggle("hidden", !en.checked);
  });
  // live output labels
  ["intensity", "speed", "fade_in", "fade_out", "opacity"].forEach(k => {
    const inp = $("#gm-" + k);
    if (inp) inp.addEventListener("input", () => {
      const out = inp.parentElement.querySelector("output");
      if (out) out.textContent = inp.value;
    });
  });
  // preset dropdown
  const sel = $("#gm-preset");
  if (sel) {
    sel.innerHTML = `<option value="">choose a preset…</option>` +
      CFG.presets.map(p => `<option value="${p}">${p}</option>`).join("");
    sel.addEventListener("change", () => {
      const p = PRESETS_LOCAL[sel.value]; if (!p) return;
      Object.entries(p).forEach(([k, v]) => {
        const inp = $("#gm-" + k);
        if (inp) { inp.value = v; const o = inp.parentElement.querySelector("output"); if (o) o.textContent = v; }
      });
    });
  }
})();
$("#btn-motion-preview").addEventListener("click", async () => {
  const btn = $("#btn-motion-preview");
  const vid = $("#motion-preview-vid"), hint = $("#gm-preview-hint");
  btn.disabled = true; btn.textContent = "rendering…"; hint.textContent = "Rendering a moving clip…";
  try {
    const r = await fetch("/api/motion_preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ motion: getGlobalMotion() })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "Preview failed.");
    vid.src = d.clip_url + "?t=" + Date.now();
    vid.classList.add("show"); vid.play().catch(() => {});
    hint.textContent = "This is how each still will drift and fade. Tweak the sliders and preview again.";
  } catch (e) {
    hint.textContent = "Couldn't render preview: " + e.message;
  } finally {
    btn.disabled = false; btn.textContent = "▶ Preview motion";
  }
});

/* ── STEP 2 · storyboard ───────────────────────────────────────────────── */
function motionControls(shot) {
  const m = Object.assign({}, CFG.defaultMotion, shot.motion || {});
  const TIPS = {
    __preset: "Quick starting points — pick one, then tweak.",
    zoom: "Slowly push in, pull out, or stay put.",
    pan: "Slide across the image as it plays.",
    intensity: "How far the image travels — higher = more obvious movement.",
    speed: "How fast the drift completes, then holds the final frame until the page ends.",
    fade_in: "Seconds the picture fades up from black.",
    fade_out: "Seconds it fades to black before the next picture (0 = hard cut).",
    opacity: "Dims the picture toward black — lower = moodier.",
  };
  const sel = (name, val, opts) =>
    `<select class="m-sel" data-m="${name}" title="${TIPS[name] || ""}">` +
    opts.map(o => `<option value="${o}" ${o === val ? "selected" : ""}>${o}</option>`).join("") +
    `</select>`;
  const rng = (name, val, min, max, step) =>
    `<div class="m-row" title="${TIPS[name] || ""}"><label>${name.replace("_", " ")}</label>` +
    `<input type="range" data-m="${name}" min="${min}" max="${max}" step="${step}" value="${val}">` +
    `<output>${val}</output></div>`;
  return `
    <button class="m-toggle">▸ motion</button>
    <div class="motion">
      <div class="m-row"><label>preset</label>
        ${sel("__preset", "", [""].concat(CFG.presets))}</div>
      <div class="m-row"><label>zoom</label>${sel("zoom", m.zoom, ["none", "in", "out"])}
        <label style="min-width:34px">pan</label>${sel("pan", m.pan, ["none", "left", "right", "up", "down"])}</div>
      ${rng("intensity", m.intensity, 0, 100, 1)}
      ${rng("speed", m.speed, 0, 100, 1)}
      ${rng("fade_in", m.fade_in, 0, 2.5, 0.1)}
      ${rng("fade_out", m.fade_out, 0, 2.5, 0.1)}
      ${rng("opacity", m.opacity == null ? 100 : m.opacity, 20, 100, 1)}
    </div>`;
}

function shotCard(shot) {
  const el = document.createElement("div");
  el.className = "shot" + (shot.is_chapter_start ? " chapter-start" : "");
  el.dataset.id = shot.id;
  const thumb = shot.image_path
    ? `<img src="${imgUrl(shot.image_path, shot.cache_bust || 1)}" alt="">`
    : `<span class="empty">no image yet</span>`;
  el.innerHTML = `
    <div class="thumb">
      ${thumb}
      <span class="ttime">${fmtMs(shot.start_ms)}–${fmtMs(shot.end_ms)}</span>
    </div>
    <div class="shot-body">
      <div class="shot-chap">${escapeHtml(shot.chapter_title)} · shot ${shot.shot_in_chapter + 1}</div>
      <textarea class="shot-prompt" spellcheck="false">${escapeHtml(shot.prompt || "")}</textarea>
      ${motionControls(shot)}
      <div class="shot-actions">
        <button class="ghost btn-regen">Regenerate</button>
        <button class="danger btn-del">Delete</button>
      </div>
    </div>`;
  wireShot(el, shot);
  return el;
}

function wireShot(el, shot) {
  const id = shot.id;
  const ta = $(".shot-prompt", el);
  ta.addEventListener("blur", () => saveShot(id, { prompt: ta.value }));
  $(".m-toggle", el).addEventListener("click", () => {
    const m = $(".motion", el); m.classList.toggle("open");
    $(".m-toggle", el).textContent = (m.classList.contains("open") ? "▾" : "▸") + " motion";
  });
  $$(".motion [data-m]", el).forEach(inp => {
    const out = inp.parentElement.querySelector("output");
    inp.addEventListener("input", () => { if (out) out.textContent = inp.value; });
    inp.addEventListener("change", () => {
      if (inp.dataset.m === "__preset") {
        if (inp.value) applyPresetToCard(el, id, inp.value);
        return;
      }
      saveMotionFromCard(el, id);
    });
  });
  $(".btn-regen", el).addEventListener("click", () => regen(el, id));
  $(".btn-del", el).addEventListener("click", () => del(el, id));
}

function collectMotion(el) {
  const m = {};
  $$(".motion [data-m]", el).forEach(inp => {
    const k = inp.dataset.m;
    if (k === "__preset") return;
    m[k] = (inp.type === "range") ? Number(inp.value) : inp.value;
  });
  return m;
}
function saveMotionFromCard(el, id) {
  const m = collectMotion(el);
  findShot(id).motion = m;
  saveShot(id, { motion: m });
}
function applyPresetToCard(el, id, presetKey) {
  // ask the server-side defaults via CFG? presets live server-side; fetch by mapping
  // we keep a local copy of preset values mirrored from kenburns.PRESETS:
  const p = PRESETS_LOCAL[presetKey];
  if (!p) return;
  Object.entries(p).forEach(([k, v]) => {
    const inp = $(`.motion [data-m="${k}"]`, el);
    if (inp) { inp.value = v; const o = inp.parentElement.querySelector("output"); if (o) o.textContent = v; }
  });
  saveMotionFromCard(el, id);
}

function findShot(id) { return PROJECT.shots.find(s => s.id === id); }

async function saveShot(id, patch) {
  try {
    await fetch(`/api/project/${jobPart()}/shot/${encodeURIComponent(id)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch)
    });
  } catch {}
}

async function regen(el, id) {
  const thumb = $(".thumb", el);
  thumb.innerHTML = `<div class="spin"></div>` +
    `<span class="ttime">${fmtMs(findShot(id).start_ms)}–${fmtMs(findShot(id).end_ms)}</span>`;
  try {
    const r = await fetch(`/api/project/${jobPart()}/shot/${encodeURIComponent(id)}/regenerate`, { method: "POST" });
    const rec = await r.json();
    if (!r.ok) throw new Error(rec.error || "Regenerate failed.");
    Object.assign(findShot(id), rec);
    thumb.innerHTML =
      `<img src="${imgUrl(rec.image_path, rec.cache_bust)}" alt="">` +
      `<span class="ttime">${fmtMs(rec.start_ms)}–${fmtMs(rec.end_ms)}</span>`;
  } catch (e) {
    thumb.innerHTML = `<span class="empty">${escapeHtml(e.message || "regenerate failed")}</span>`;
    toast(e.message || "Regenerate failed.", "warn");
  }
}

async function del(el, id) {
  if (!confirm("Delete this shot? Its on-screen time is handed to the shot before it.")) return;
  try {
    const r = await fetch(`/api/project/${jobPart()}/shot/${encodeURIComponent(id)}/delete`, { method: "POST" });
    const data = await r.json();
    if (data.shots) PROJECT.shots = data.shots;
    el.remove();
    updateBoardSummary();
  } catch {}
}

/* mirror of kenburns.PRESETS so "apply to all" works without a round-trip */
const PRESETS_LOCAL = {
  gentle_drift:  { zoom: "in",  pan: "none",  intensity: 22, speed: 45, fade_in: 0.8, fade_out: 0.8 },
  slow_reveal:   { zoom: "out", pan: "none",  intensity: 38, speed: 35, fade_in: 1.0, fade_out: 0.8 },
  pan_right:     { zoom: "in",  pan: "right", intensity: 40, speed: 50, fade_in: 0.6, fade_out: 0.6 },
  pan_left:      { zoom: "in",  pan: "left",  intensity: 40, speed: 50, fade_in: 0.6, fade_out: 0.6 },
  dramatic_push: { zoom: "in",  pan: "up",    intensity: 60, speed: 70, fade_in: 0.4, fade_out: 0.5 },
  still:         { zoom: "none", pan: "none", intensity: 0,  speed: 50, fade_in: 0.5, fade_out: 0.5 },
};

function renderBoard() {
  const board = $("#board"); board.innerHTML = "";
  PROJECT.shots.forEach(s => board.appendChild(shotCard(s)));
  updateBoardSummary();
}
function updateBoardSummary() {
  const n = PROJECT.shots.length;
  const dur = PROJECT.total_ms;
  let s = `${n} shots · ${fmtMs(dur)} runtime`;
  const a = PROJECT.alignment;
  if (a) {
    s += ` · aligned to your chapter list`;
    if (a.skipped) s += ` (skipped ${a.skipped} ebook intro section${a.skipped > 1 ? "s" : ""}, starts at “${a.start_title}”)`;
    if (a.empty_text) s += ` · ⚠ ${a.empty_text} chapter${a.empty_text > 1 ? "s" : ""} had no matching ebook text`;
  }
  $("#board-summary").textContent = s;
}

$("#btn-apply-motion-all").addEventListener("click", () => {
  const m = getGlobalMotion();
  PROJECT.shots.forEach(s => s.motion = { ...m });
  // reflect into any per-shot controls already on screen
  $$("#board .shot").forEach(el => {
    Object.entries(m).forEach(([k, v]) => {
      const inp = el.querySelector(`[data-m="${k}"]`);
      if (inp) {
        inp.value = v;
        const o = inp.parentElement.querySelector("output");
        if (o) o.textContent = v;
      }
    });
  });
  fetch(`/api/project/${jobPart()}/motion_all`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ motion: m })
  }).catch(() => {});
  const btn = $("#btn-apply-motion-all"), orig = btn.textContent;
  btn.textContent = "✓ applied to all shots";
  setTimeout(() => { btn.textContent = orig; }, 1500);
});

$("#btn-back").addEventListener("click", () => {
  hide("step-board"); show("step-ingest");
  $("#step-ingest").scrollIntoView({ behavior: "smooth" });
});
$("#btn-expand-motion").addEventListener("click", () => {
  const open = $$("#board .motion.open").length === 0;
  $$("#board .shot").forEach(el => {
    $(".motion", el).classList.toggle("open", open);
    $(".m-toggle", el).textContent = (open ? "▾" : "▸") + " motion";
  });
  $("#btn-expand-motion").textContent = open ? "hide all motion controls" : "show all motion controls";
});

/* ── STEP 3 · generate ─────────────────────────────────────────────────── */
async function runGenerate() {
  show("step-generate");
  $("#step-generate").scrollIntoView({ behavior: "smooth" });
  $("#gen-fill").style.width = "0%"; $("#gen-label").textContent = "0%";
  $("#gen-err").textContent = ""; $("#btn-to-assemble").disabled = true;
  $("#log-lines").innerHTML = "";
  logLine(`Generating ${PROJECT.shots.length} shots…`);
  let completed = false;
  try {
    const r = await fetch(`/api/project/${jobPart()}/generate`, { method: "POST" });
    let total = PROJECT.shots.length;
    await readSSE(r, ev => {
      if (ev.type === "start") { total = ev.total || total; logLine(`${ev.total} to draw, ${ev.already || 0} already done.`); }
      else if (ev.type === "shot") {
        const pct = Math.round((ev.done / Math.max(1, ev.total)) * 100);
        $("#gen-fill").style.width = pct + "%";
        $("#gen-label").textContent = `${pct}% · ${ev.done}/${ev.total}`;
        const failed = ev.status === "error";
        logLine(`Shot ${ev.id}: ${failed ? "FAILED — " + (ev.error || "") : "done"}`,
                failed ? "err" : "");
        const el = $(`#board .shot[data-id="${ev.id}"]`);
        const rec = findShot(ev.id);
        if (el && rec) {
          rec.cache_bust = ev.cache_bust;
          rec.status = ev.status;
          rec.image_path = failed ? null : `images/${ev.id}.jpg`;
          if (!failed) {
            $(".thumb", el).innerHTML =
              `<img src="${imgUrl(rec.image_path, ev.cache_bust)}" alt="">` +
              `<span class="ttime">${fmtMs(rec.start_ms)}–${fmtMs(rec.end_ms)}</span>`;
          } else {
            $(".thumb", el).innerHTML =
              `<span class="empty">generation failed</span>` +
              `<span class="ttime">${fmtMs(rec.start_ms)}–${fmtMs(rec.end_ms)}</span>`;
          }
        }
      } else if (ev.type === "error") {
        throw new Error(ev.message || "Generation stopped.");
      } else if (ev.type === "complete") {
        $("#gen-fill").style.width = "100%";
        const failed = Number(ev.failed || 0);
        if (failed) {
          $("#gen-label").textContent = `${failed} shot${failed === 1 ? "" : "s"} need attention`;
          $("#gen-err").textContent = `${failed} image${failed === 1 ? "" : "s"} failed. Regenerate the failed shot(s), then run Generate again.`;
          $("#btn-to-assemble").disabled = true;
          logLine(`${failed} shot(s) failed; video assembly is blocked until every image exists.`, "err");
          completed = false;
        } else {
          $("#gen-label").textContent = "all shots drawn";
          $("#btn-to-assemble").disabled = false;
          logLine("All shots drawn. Ready to stitch.", "ok");
          completed = true;
        }
      }
    });
  } catch (e) {
    $("#gen-err").textContent = "Generation stopped: " + e.message;
    logLine("Generation stopped: " + e.message, "err");
  }
  return completed;
}

$("#btn-generate").addEventListener("click", runGenerate);

/* one-click: generate everything, then stitch the video, then show results */
$("#btn-all").addEventListener("click", async () => {
  const ok = await runGenerate();
  if (ok) {
    show("step-assemble");
    $("#step-assemble").scrollIntoView({ behavior: "smooth" });
    await runAssemble();
  }
});

$("#btn-to-assemble").addEventListener("click", () => {
  show("step-assemble");
  $("#step-assemble").scrollIntoView({ behavior: "smooth" });
  runAssemble();
});

/* ── STEP 4 · assemble ─────────────────────────────────────────────────── */
async function runAssemble() {
  $("#asm-fill").style.width = "0%"; $("#asm-label").textContent = "rendering clips…";
  $("#asm-err").textContent = ""; hide("results");
  logLine("Stitching: rendering Ken Burns clips…");
  try {
    const r = await fetch(`/api/project/${jobPart()}/assemble`, { method: "POST" });
    await readSSE(r, ev => {
      if (ev.type === "start") { $("#asm-label").textContent = `rendering ${ev.total} clips…`; logLine(`${ev.total} clips to render.`); }
      else if (ev.type === "clip") {
        const pct = Math.round((ev.done / Math.max(1, ev.total)) * 90);
        $("#asm-fill").style.width = pct + "%";
        $("#asm-label").textContent = `Ken Burns clip ${ev.done}/${ev.total}`;
      } else if (ev.type === "mux") {
        $("#asm-fill").style.width = "95%"; $("#asm-label").textContent = ev.message || "muxing…";
        logLine("Muxing audio + chapters…");
      } else if (ev.type === "done") {
        $("#asm-fill").style.width = "100%"; $("#asm-label").textContent = "done";
        logLine("Done — video written: " + ev.video, "ok");
        showResults(ev);
      } else if (ev.type === "error") {
        $("#asm-err").textContent = ev.message;
        logLine("Assembly error: " + ev.message, "err");
      }
    });
  } catch (e) {
    $("#asm-err").textContent = "Assembly stopped: " + e.message;
    logLine("Assembly stopped: " + e.message, "err");
  }
}

function showResults(ev) {
  const job = jobPart();
  const encFile = name => String(name || "").split("/").map(encodeURIComponent).join("/");
  const links = [];
  links.push(`<a class="dl" href="/download/${job}/${encFile(ev.video)}" download>⬇ Download MP4</a>`);
  if (ev.timestamps_file)
    links.push(`<a class="dl alt" href="/download/${job}/${encFile(ev.timestamps_file)}" download>YouTube chapters .txt</a>`);
  if (ev.drive_file)
    links.push(`<a class="dl alt" href="/download/${job}/${encFile(ev.drive_file)}" download>Google Drive chapter page</a>`);
  if (ev.subtitle_file)
    links.push(`<a class="dl alt" href="/download/${job}/${encFile(ev.subtitle_file)}" download>Subtitles (.srt)${ev.subtitle_mode === "burn" ? " · also burned in" : ""}</a>`);
  $("#dl-links").innerHTML = links.join("");
  $("#video-preview").src = `/image/${job}/${encFile(ev.video)}`;

  const rows = PROJECT.shots.map((shot, i) => {
    const ok = shot.status !== "error";
    const title = escapeHtml(shot.chapter_title);
    const status = ok ? "✓ done" : `✕ ${escapeHtml(shot.error || "error")}`;
    return `<tr>
      <td>${i + 1}</td>
      <td>${title}${shot.is_chapter_start ? ' <span class="chip">ch start</span>' : ""}</td>
      <td>${fmtMs(shot.start_ms)}–${fmtMs(shot.end_ms)}</td>
      <td class="${ok ? "ok" : "err"}">${status}</td>
    </tr>`;
  }).join("");
  $("#restable-body").innerHTML = rows;

  show("results");
}

/* ── resume / restore session ──────────────────────────────────────────── */
async function loadResume() {
  try {
    const d = await (await fetch("/api/projects")).json();
    const list = (d.projects || []).filter(p => p.shots > 0);
    if (!list.length) return;
    const box = $("#resume-list");
    box.innerHTML = list.slice(0, 6).map(p => {
      const status = p.has_video ? "✓ video built"
        : (p.done >= p.shots && p.shots) ? "all images ready"
        : `${p.done}/${p.shots} images drawn`;
      const when = fmtWhen(p.modified);
      return `<div class="resume-item" data-job="${escapeHtml(p.job)}">
        <div class="ri-main"><b>${escapeHtml(p.title || "Audiobook")}</b>
          <span class="ri-meta">${when ? when + " · " : ""}${fmtMs(p.total_ms)} · ${status}</span></div>
        <div class="ri-actions">
          <button class="primary small ri-go">Resume →</button>
          <button class="ri-del" title="Remove this session">✕</button>
        </div>
      </div>`;
    }).join("");
    $$(".resume-item .ri-go", box).forEach(btn => btn.addEventListener("click", () =>
      resumeProject(btn.closest(".resume-item").dataset.job)));
    $$(".resume-item .ri-del", box).forEach(btn => btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const item = btn.closest(".resume-item");
      const job = item.dataset.job;
      if (!confirm("Remove this session? This deletes its images and any built video from disk.")) return;
      try {
        await fetch(`/api/project/${encodeURIComponent(job)}`, { method: "DELETE" });
        item.remove();
        if (!$$(".resume-item", box).length) hide("resume-bar");
      } catch { toast("Couldn't remove that session.", "warn"); }
    }));
    show("resume-bar");
  } catch {}
}

async function resumeProject(job) {
  try {
    const p = await (await fetch(`/api/project/${encodeURIComponent(job)}`)).json();
    if (!p || p.error) throw new Error("Couldn't load that session.");
    PROJECT = p;
    renderBoard();
    hide("step-ingest"); hide("resume-bar"); show("step-board");
    const done = PROJECT.shots.filter(s => s.status === "done" && s.image_path).length;
    $("#step-board").scrollIntoView({ behavior: "smooth" });
    if (done) {
      // surface a hint that resuming will skip finished images
      $("#board-summary").textContent =
        `${PROJECT.shots.length} shots · ${done} already drawn — “Generate all” resumes where it left off`;
    }
  } catch (e) {
    alert(e.message);
  }
}

$("#resume-dismiss").addEventListener("click", () => hide("resume-bar"));
$("#resume-clear")?.addEventListener("click", async () => {
  if (!confirm("Clear ALL recent sessions? This permanently deletes their images and any built videos from disk.")) return;
  try {
    await fetch("/api/projects/clear", { method: "POST" });
    $("#resume-list").innerHTML = "";
    hide("resume-bar");
  } catch { toast("Couldn't clear sessions.", "warn"); }
});
loadResume();


/* ── SeeStory Windows desktop lifecycle + system monitor ------------------ */
(() => {
  const heartbeat = () => fetch("/api/desktop/heartbeat", {
    method: "POST", cache: "no-store", keepalive: true,
  }).catch(() => {});
  heartbeat();
  window.setInterval(heartbeat, 2500);

  const pct = value => value == null ? "—" : `${Math.round(value)}%`;
  const num = (value, digits = 1) => value == null ? "—" : Number(value).toFixed(digits);

  async function refreshSystemMonitor() {
    const panel = document.getElementById("system-monitor");
    if (!panel) return;
    try {
      const data = await (await fetch("/api/system", { cache: "no-store" })).json();
      const gpu = data.gpu || {};
      document.getElementById("sm-cpu").textContent = pct(data.cpu_percent);
      document.getElementById("sm-ram").textContent =
        data.ram_used_gb == null ? "—" : `${num(data.ram_used_gb)}/${num(data.ram_total_gb)} GB`;
      document.getElementById("sm-gpu").textContent = gpu.available ? pct(gpu.util) : "not detected";
      document.getElementById("sm-vram").textContent = gpu.available
        ? `${Math.round(gpu.memory_used_mb)}/${Math.round(gpu.memory_total_mb)} MB`
        : "—";
      document.getElementById("sm-temp").textContent = gpu.available
        ? `${Math.round(gpu.temperature_c)}°C`
        : "—";
      panel.classList.toggle("gpu-active", Boolean(gpu.available && gpu.util >= 5));
      panel.classList.remove("offline");
    } catch {
      panel.classList.add("offline");
    }
  }

  refreshSystemMonitor();
  window.setInterval(refreshSystemMonitor, 2500);
})();
