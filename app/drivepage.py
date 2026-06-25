"""Chapter helpers reused from Parroty (no pydub dependency):
YouTube timestamps, FFMETADATA chapters, and the Google Drive clickable
chapter page. Lifted verbatim so SeeStory videos carry the same chapter
bookmarks as Parroty audiobooks. Branded line updated for SeeStory."""

import json as _json
import html as _html


def _fmt_timestamp(ms: int) -> str:
    total = int(ms // 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def build_youtube_timestamps(markers: list) -> str:
    """Turn [(title, start_ms)] into newline timestamp text for YouTube."""
    lines = []
    for i, (title, start_ms) in enumerate(markers):
        stamp = "00:00" if i == 0 else _fmt_timestamp(start_ms)
        lines.append(f"{stamp} {title}")
    return "\n".join(lines)


def build_ffmetadata(markers: list, total_ms: int) -> str:
    """FFMETADATA1 chapter block so players get real chapter bookmarks."""
    lines = [";FFMETADATA1"]
    for i, (title, start_ms) in enumerate(markers):
        end_ms = markers[i + 1][1] if i + 1 < len(markers) else total_ms
        safe = title.replace("=", " ").replace("\n", " ")
        lines += [
            "[CHAPTER]", "TIMEBASE=1/1000",
            f"START={int(start_ms)}", f"END={int(end_ms)}", f"title={safe}",
        ]
    return "\n".join(lines) + "\n"



_DRIVE_PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — Google Drive chapters</title>
<style>
  :root{ --paper:#efe7d6; --card:#e7ddc8; --ink:#2b2117; --muted:#7a6f5d;
         --rule:#d6c9af; --accent:#7c2d2d; --ok:#3f7d4f; }
  *{ box-sizing:border-box; }
  body{ margin:0; background:var(--paper); color:var(--ink);
        font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  .wrap{ max-width:760px; margin:0 auto; padding:32px 20px 60px; }
  h1{ font-family:Georgia,"Times New Roman",serif; font-size:1.9rem; margin:0 0 2px; }
  .sub{ color:var(--muted); margin:0 0 22px; font-style:italic; }
  ol.how{ background:var(--card); border:1px solid var(--rule); border-radius:8px;
          padding:14px 14px 14px 34px; margin:0 0 18px; }
  ol.how li{ margin:4px 0; }
  .row{ display:flex; gap:8px; margin:0 0 8px; flex-wrap:wrap; }
  #link{ flex:1; min-width:240px; font-family:ui-monospace,Menlo,Consolas,monospace;
         font-size:.86rem; padding:11px 12px; border:1px solid var(--rule);
         border-radius:6px; background:#fbf7ec; color:var(--ink); }
  button{ font:inherit; font-weight:600; cursor:pointer; border-radius:6px;
          border:1px solid var(--accent); background:var(--accent); color:#fff;
          padding:11px 16px; }
  button.ghost{ background:transparent; color:var(--accent); }
  button:hover{ filter:brightness(1.06); }
  .status{ min-height:20px; font-size:.9rem; margin:6px 0 0; }
  .status.ok{ color:var(--ok); } .status.err{ color:var(--accent); }
  .toolbar{ display:flex; align-items:center; gap:12px; margin:16px 0 6px; }
  .muted{ color:var(--muted); font-size:.85rem; }
  ul.chapters{ list-style:none; margin:8px 0 0; padding:0;
               border:1px solid var(--rule); border-radius:8px; overflow:hidden; }
  ul.chapters li{ display:flex; align-items:baseline; gap:12px; padding:9px 14px;
                  border-top:1px solid var(--rule); background:var(--card); }
  ul.chapters li:first-child{ border-top:none; }
  .ts{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.8rem;
       color:var(--muted); min-width:64px; }
  a.ti{ color:var(--accent); text-decoration:none; font-weight:600; }
  a.ti:hover{ text-decoration:underline; }
  span.ti{ color:var(--muted); }
  .foot{ color:var(--muted); font-size:.82rem; margin-top:22px; }
  code{ background:#fbf7ec; border:1px solid var(--rule); border-radius:4px; padding:0 4px; }
</style></head><body>
<div class="wrap">
  <h1>🔖 __TITLE__</h1>
  <p class="sub">Clickable chapters for playback from Google Drive</p>
  <ol class="how">
    <li>Upload your audiobook <strong>.mp4</strong> to Google Drive and let it finish processing.</li>
    <li>Right-click it → <strong>Share</strong> → <strong>Copy link</strong> (set "Anyone with the link" to view if you'll share it).</li>
    <li>Paste that link below. Each chapter turns into a link that opens the video at its start time.</li>
  </ol>
  <div class="row">
    <input id="link" type="text" autocomplete="off"
           placeholder="https://drive.google.com/file/d/.../view?usp=sharing">
    <button id="go">Build chapter links</button>
  </div>
  <div id="status" class="status"></div>
  <div class="toolbar" id="toolbar" hidden>
    <button id="copyall" class="ghost">Copy all links</button>
    <span class="muted" id="count"></span>
  </div>
  <ul id="list" class="chapters"></ul>
  <p class="foot">Made by SeeStory. Google Drive's player has no chapter menu, so each chapter
    is a link that opens the video at its start time (the <code>?t=</code> in the URL). The same
    chapter marks are also embedded in the file itself, so a desktop player like VLC shows a real
    chapter menu if you download it.</p>
</div>
<script>
const CH = __DATA__;
const $ = id => document.getElementById(id);
function fmt(s){ const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), x=s%60;
  return (h>0 ? h+":"+String(m).padStart(2,"0") : ""+m) + ":" + String(x).padStart(2,"0"); }
function extractId(u){
  u=(u||"").trim();
  let m=u.match(/\\/d\\/([A-Za-z0-9_-]{10,})/) || u.match(/[?&]id=([A-Za-z0-9_-]{10,})/);
  if(m) return m[1];
  if(/^[A-Za-z0-9_-]{20,}$/.test(u)) return u;
  return null;
}
let FILE_ID=null;
function linkFor(t){ return "https://drive.google.com/file/d/"+FILE_ID+"/view?t="+t; }
function render(){
  const ul=$("list"); ul.innerHTML="";
  CH.forEach((c,i)=>{
    const li=document.createElement("li");
    const ts=document.createElement("span"); ts.className="ts"; ts.textContent=fmt(c.t);
    let node;
    if(FILE_ID){ node=document.createElement("a"); node.href=linkFor(c.t);
      node.target="_blank"; node.rel="noopener"; }
    else { node=document.createElement("span"); }
    node.className="ti"; node.textContent=c.title || ("Chapter "+(i+1));
    li.appendChild(ts); li.appendChild(node); ul.appendChild(li);
  });
  $("count").textContent=CH.length+" chapters";
  $("toolbar").hidden=!FILE_ID;
}
function build(){
  const id=extractId($("link").value);
  if(!id){ $("status").className="status err";
    $("status").textContent="That doesn't look like a Google Drive link — paste the full 'Copy link' URL.";
    FILE_ID=null; render(); return; }
  FILE_ID=id; $("status").className="status ok";
  $("status").textContent="\\u2713 Linked. Click any chapter to open the video at that point.";
  render();
}
$("go").addEventListener("click", build);
$("link").addEventListener("keydown", e=>{ if(e.key==="Enter") build(); });
$("copyall").addEventListener("click", ()=>{
  if(!FILE_ID) return;
  const lines=CH.map((c,i)=>fmt(c.t)+"  "+(c.title||("Chapter "+(i+1)))+"  "+linkFor(c.t));
  navigator.clipboard.writeText(lines.join("\\n")).then(()=>{
    $("status").className="status ok"; $("status").textContent="\\u2713 All "+CH.length+" links copied.";
  });
});
render();
</script></body></html>
"""


def build_drive_chapter_page(markers: list, book_title: str = "",
                             total_ms: int = None) -> str:
    """A self-contained HTML page that turns a Google Drive video share link into
    a clickable chapter index.

    Google Drive's web player has no chapter menu, but its video URLs accept a
    ?t=<seconds> start time, so each chapter can be a link that opens the video
    at that point. The chapter titles/times are baked in; the user pastes their
    Drive share link once and every chapter becomes clickable. No external
    dependencies — it works opened straight from disk.
    """
    import json as _json
    import html as _html
    data = []
    for i, (title, start_ms) in enumerate(markers):
        data.append({"t": max(0, int(start_ms // 1000)),
                     "title": title or f"Chapter {i + 1}"})
    # Embed safely inside <script> (escape any "</" so it can't close the tag).
    data_js = _json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title_txt = _html.escape(book_title or "Audiobook")
    return (_DRIVE_PAGE_TEMPLATE
            .replace("__TITLE__", title_txt)
            .replace("__DATA__", data_js))
