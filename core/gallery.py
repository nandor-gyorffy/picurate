"""Self-contained HTML gallery generator."""
from __future__ import annotations

import html
import shutil
from pathlib import Path

from core.logger import get_logger

log = get_logger("picurate.gallery")

_CSS = """\
*{box-sizing:border-box;margin:0;padding:0}
body{background:#111;color:#eee;font-family:sans-serif}
h1{text-align:center;padding:20px 0 10px;font-size:1.4em;font-weight:400}
.grid{display:flex;flex-wrap:wrap;gap:6px;padding:12px;justify-content:center}
.thumb{width:200px;height:150px;object-fit:cover;cursor:pointer;border-radius:3px;
       transition:transform .15s;border:2px solid transparent}
.thumb:hover{transform:scale(1.04);border-color:#fff}
#lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:9;
    align-items:center;justify-content:center;flex-direction:column}
#lb.on{display:flex}
#lb img{max-width:92vw;max-height:88vh;object-fit:contain;border-radius:4px}
#lb-cap{color:#ccc;margin-top:8px;font-size:.9em}
#lb-close{position:fixed;top:14px;right:22px;font-size:2em;cursor:pointer;color:#fff;line-height:1}
#lb-prev,#lb-next{position:fixed;top:50%;transform:translateY(-50%);
                  font-size:2.5em;cursor:pointer;color:#fff;user-select:none;padding:0 16px}
#lb-prev{left:0}#lb-next{right:0}
"""

_JS = """\
const imgs=document.querySelectorAll('.thumb');
const lb=document.getElementById('lb');
const lbImg=document.getElementById('lb-img');
const lbCap=document.getElementById('lb-cap');
let cur=0;
function show(i){cur=i;lbImg.src=imgs[i].dataset.full;lbCap.textContent=imgs[i].alt;lb.classList.add('on')}
function hide(){lb.classList.remove('on')}
imgs.forEach((img,i)=>img.addEventListener('click',()=>show(i)));
document.getElementById('lb-close').addEventListener('click',hide);
document.getElementById('lb-prev').addEventListener('click',()=>show((cur-1+imgs.length)%imgs.length));
document.getElementById('lb-next').addEventListener('click',()=>show((cur+1)%imgs.length));
lb.addEventListener('click',e=>{if(e.target===lb)hide()});
document.addEventListener('keydown',e=>{
  if(!lb.classList.contains('on'))return;
  if(e.key==='Escape')hide();
  if(e.key==='ArrowLeft')show((cur-1+imgs.length)%imgs.length);
  if(e.key==='ArrowRight')show((cur+1)%imgs.length);
});
"""


def generate_gallery(
    photo_rows,
    dest_folder: Path,
    title: str = "Photo Gallery",
) -> Path:
    """
    Generate a self-contained HTML gallery in *dest_folder*.

    Copies thumbnails (or originals if no thumbnail) into dest_folder/images/.
    Returns the path to index.html.
    """
    dest_folder = Path(dest_folder)
    images_dir = dest_folder / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    seq = 0
    for row in photo_rows:
        thumb = row["thumbnail_path"] or ""
        full = row["file_path"] or ""
        filename = row["filename"] or f"photo_{seq}"
        date = (row["date_taken"] or "")[:10]
        caption = html.escape(f"{filename}  {date}".strip())

        # Copy thumbnail; fall back to original if no thumbnail
        src_thumb = Path(thumb) if thumb and Path(thumb).exists() else (
            Path(full) if full and Path(full).exists() else None
        )
        if src_thumb is None:
            continue

        seq += 1
        out_name = f"{seq:04d}_{filename}"
        out_path = images_dir / out_name
        try:
            _make_thumb(src_thumb, out_path)
        except Exception as exc:
            log.warning("Gallery thumb failed for %s: %s", src_thumb, exc)
            continue

        # Full image: use relative path to the exported image (same dir)
        # The gallery links to the image in the parent dest_folder
        exported_name = out_path.name  # both thumb and full are the same file here

        entries.append({
            "thumb": f"images/{exported_name}",
            "full": f"images/{exported_name}",
            "caption": caption,
        })

    index_html = _build_html(title, entries)
    out_index = dest_folder / "index.html"
    out_index.write_text(index_html, encoding="utf-8")
    log.info("Gallery written to %s (%d photos)", out_index, len(entries))
    return out_index


def _make_thumb(src: Path, dest: Path) -> None:
    """Write a gallery thumbnail (max 400px) for src to dest."""
    from PIL import Image, ImageOps
    img = Image.open(src)
    img = ImageOps.exif_transpose(img)
    img.thumbnail((400, 400), Image.LANCZOS)
    img = img.convert("RGB")
    dest_suffix = dest.suffix.lower()
    if dest_suffix not in (".jpg", ".jpeg"):
        dest = dest.with_suffix(".jpg")
    img.save(dest, "JPEG", quality=80)


def _build_html(title: str, entries: list[dict]) -> str:
    thumbs_html = "\n".join(
        f'    <img class="thumb" src="{e["thumb"]}" alt="{e["caption"]}" data-full="{e["full"]}">'
        for e in entries
    )
    t = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{t}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>{t}</h1>
<div class="grid">
{thumbs_html}
</div>
<div id="lb">
  <span id="lb-close">&#x2715;</span>
  <span id="lb-prev">&#x276E;</span>
  <img id="lb-img" src="" alt="">
  <div id="lb-cap"></div>
  <span id="lb-next">&#x276F;</span>
</div>
<script>{_JS}</script>
</body>
</html>
"""
