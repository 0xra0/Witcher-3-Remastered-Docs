#!/usr/bin/env python3
"""Build offline galleries and link them from archived documentation pages."""
import html
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

PARSER = argparse.ArgumentParser(description=__doc__)
PARSER.add_argument('--output', type=Path, default=Path('witcher3-offline-library'), help='Archive directory made by witcher_archive.py')
ARGS = PARSER.parse_args()
ROOT = ARGS.output.expanduser().resolve()
INV = json.loads((ROOT / 'media_inventory.json').read_text(encoding='utf-8'))
MAN = json.loads((ROOT / 'media_manifest.json').read_text(encoding='utf-8'))
DB = sqlite3.connect(ROOT / 'library.sqlite3')
MEDIA_DIR = ROOT / 'sites/official-redkit-full-site/media'
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
STYLE = '''<style>body{background:#10151a;color:#e0e8eb;font:16px system-ui;max-width:1100px;margin:2em auto;padding:0 1em}a{color:#8ad0ff}h1,h2{color:#f2e1b8}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:1em}.card{background:#1c2830;padding:.8em;border-radius:9px;overflow-wrap:anywhere}.card img,.card video{width:100%;height:auto;max-height:320px;object-fit:contain}.small{font-size:.85em;color:#aebec6}li{margin:.5em 0}</style>'''


def esc(s):
    return html.escape(str(s or ''), quote=True)


def link(from_path, to_path):
    return os.path.relpath(ROOT / to_path, from_path.parent).replace(os.sep, '/')


def insert_once(path, marker, snippet):
    source = path.read_text(encoding='utf-8')
    source = re.sub(rf'<!-- {marker} START -->.*?<!-- {marker} END -->', '', source, flags=re.S)
    block = f'<!-- {marker} START -->{snippet}<!-- {marker} END -->'
    source = re.sub(r'</body\s*>', lambda m: block + m.group(0), source, count=1, flags=re.I) if re.search(r'</body\s*>', source, re.I) else source + block
    path.write_text(source, encoding='utf-8')


all_pages = []
for pid, record in sorted(INV['pages'].items(), key=lambda x: x[1]['page']['title'].casefold()):
    page = record['page']
    row = DB.execute('SELECT path FROM items WHERE url=?', (page['url'],)).fetchone()
    if not row:
        continue
    page_path = ROOT / row[0]
    gallery_path = MEDIA_DIR / f'{pid}.html'
    cards = []
    image_count = video_count = other_count = missing_count = 0
    for att in record['attachments']:
        aid = att['id']
        saved = MAN['attachments'].get(aid)
        name = att.get('title') or aid
        typ = (att.get('mediaType') or '').strip('"').lower()
        if not saved:
            missing_count += 1
            reason = 'Invalid zero-byte attachment in source' if aid in MAN.get('unavailable', {}) else 'Download failed; see media_manifest.json'
            cards.append(f'<div class="card"><strong>{esc(name)}</strong><p class="small">{reason}</p></div>')
            continue
        rel = esc(link(gallery_path, saved['path']))
        label = f'<p><a href="{rel}">{esc(name)}</a></p><p class="small">{saved["bytes"]:,} bytes · {esc(typ)}</p>'
        if typ.startswith('image/') or Path(name).suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif'}:
            image_count += 1
            body = f'<a href="{rel}"><img loading="lazy" src="{rel}" alt="{esc(name)}"></a>'
        elif typ == 'video/mp4' or name.lower().endswith('.mp4'):
            video_count += 1
            body = f'<video controls preload="metadata" src="{rel}"></video>'
        elif typ.startswith('video/') or Path(name).suffix.lower() in {'.mkv', '.webm', '.mov'}:
            video_count += 1
            body = f'<a href="{rel}">Open or download video file</a>'
        else:
            other_count += 1
            body = ''
        cards.append(f'<div class="card">{body}{label}</div>')
    reader_rel = Path('sites/official-redkit-full-site/reader') / f'{pid}.html'
    back = esc(link(gallery_path, reader_rel if (ROOT / reader_rel).exists() else row[0]))
    gallery = f'<!doctype html><html lang="en"><meta charset="utf-8"><title>{esc(page["title"])} — saved media</title>{STYLE}<h1>{esc(page["title"])}</h1><p><a href="{back}">← Return to saved article</a> · <a href="../../../media.html">All saved media</a> · <a href="{esc(page["url"])}">Original page</a></p><p>{image_count} images, {video_count} videos, {other_count} other attachments, {missing_count} unavailable at source</p><div class="grid">{"".join(cards)}</div></html>'
    gallery_path.write_text(gallery, encoding='utf-8')
    snippet = f'<aside style="margin:2em;padding:1em;background:#14242b;color:white;border:2px solid #6ca8ba;position:relative;z-index:9999"><strong>Offline media:</strong> <a style="color:#a8e5ff" href="{esc(link(page_path, gallery_path.relative_to(ROOT)))}">Browse {len(record["attachments"])} listed attachments for this article</a></aside>'
    insert_once(page_path, 'W3 OFFLINE MEDIA', snippet)
    all_pages.append((page, gallery_path, image_count, video_count, other_count))

rows = []
for page, gallery_path, images, videos, other in all_pages:
    rows.append(f'<li><a href="{esc(gallery_path.relative_to(ROOT).as_posix())}">{esc(page["title"])}</a> <span class="small">{images} images · {videos} videos · {other} other</span></li>')
(ROOT / 'media.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Saved REDkit media</title>' + STYLE + '<h1>Saved REDkit media</h1><p><a href="index.html">← Main archive</a> · <a href="videos.html">YouTube tutorial videos</a></p><ul>' + ''.join(rows) + '</ul></html>', encoding='utf-8')

ids = [line.strip().split('v=')[-1] for line in (ROOT / 'video_urls.txt').read_text().splitlines() if line.strip()]
def lesson_order(vid):
    files = list((ROOT / 'videos').glob(f'{vid}--*.mp4'))
    match = re.search(r'Tutorial\s*#(\d+)', files[0].name, re.I) if files else None
    return (int(match.group(1)) if match else 999, vid)
ids.sort(key=lesson_order)
vrows = []
for vid in ids:
    matches = list((ROOT / 'videos').glob(f'{vid}--*.mp4'))
    posters = list((ROOT / 'videos').glob(f'{vid}--*.jpg'))
    source = f'https://www.youtube.com/watch?v={vid}'
    if matches:
        file = matches[0]
        video_href = esc(quote(file.relative_to(ROOT).as_posix(), safe='/'))
        poster = f' poster="{esc(quote(posters[0].relative_to(ROOT).as_posix(), safe="/"))}"' if posters else ''
        vrows.append(f'<div class="card"><h2>{esc(file.stem.split("--",1)[-1])}</h2><video controls preload="metadata" src="{video_href}"{poster}></video><p><a href="{video_href}">Download MP4</a> · <a href="{source}">Original</a></p></div>')
    else:
        vrows.append(f'<div class="card"><h2>{vid}</h2><p>Local copy unavailable. <a href="{source}">Original video</a></p></div>')
(ROOT / 'videos.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>REDkit video tutorials</title>' + STYLE + '<h1>REDkit video tutorials</h1><p><a href="index.html">← Main archive</a> · <a href="media.html">Confluence attachments</a></p><div class="grid">' + ''.join(vrows) + '</div></html>', encoding='utf-8')

for filename in ('index.html', 'guides.html'):
    target = ROOT / filename
    insert_once(target, 'W3 MEDIA NAV', '<p style="padding:.7em;background:#1e3440"><a href="media.html">Browse saved screenshots and attachments</a> · <a href="videos.html">Watch saved tutorial videos</a></p>')
for url in ('https://cdprojektred.atlassian.net/wiki/spaces/W3REDkit/pages/28737537/Video+tutorials', 'https://mod.io/g/the-witcher-3/r/redkit'):
    row = DB.execute('SELECT path FROM items WHERE url=?', (url,)).fetchone()
    if row:
        target = ROOT / row[0]
        insert_once(target, 'W3 VIDEO NAV', f'<aside style="margin:2em;padding:1em;background:#14242b;color:white;border:2px solid #6ca8ba;position:relative;z-index:9999"><a style="color:#a8e5ff" href="{esc(link(target, Path("videos.html")))}">Watch the 14 saved REDkit tutorial videos</a></aside>')
print(f'Built {len(all_pages)} page galleries and linked media/videos from archive pages.')
