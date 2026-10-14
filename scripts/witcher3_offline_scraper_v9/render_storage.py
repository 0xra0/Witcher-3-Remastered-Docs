#!/usr/bin/env python3
"""Save Confluence storage bodies and render offline readers with inline images."""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib import request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=Path('witcher3-offline-library'))
args = parser.parse_args()
root = args.output.expanduser().resolve()
inventory = json.loads((root / 'media_inventory.json').read_text(encoding='utf-8'))
manifest = json.loads((root / 'media_manifest.json').read_text(encoding='utf-8'))
db = sqlite3.connect(root / 'library.sqlite3')
storage_dir = root / 'sites/official-redkit-full-site/storage'
reader_dir = root / 'sites/official-redkit-full-site/reader'
storage_dir.mkdir(parents=True, exist_ok=True)
reader_dir.mkdir(parents=True, exist_ok=True)
record = {'pages': {}, 'errors': {}}


def esc(value):
    return html.escape(str(value or ''), quote=True)


def replace_once(path, marker, snippet):
    content = path.read_text(encoding='utf-8')
    content = re.sub(rf'<!-- {marker} START -->.*?<!-- {marker} END -->', '', content, flags=re.S)
    block = f'<!-- {marker} START -->{snippet}<!-- {marker} END -->'
    content = re.sub(r'</body\s*>', lambda m: block + m.group(0), content, count=1, flags=re.I) if re.search(r'</body\s*>', content, re.I) else content + block
    path.write_text(content, encoding='utf-8')


last_request = 0.0
for n, (pid, item) in enumerate(inventory['pages'].items(), 1):
    source_path = storage_dir / f'{pid}.html'
    if source_path.exists():
        body = source_path.read_text(encoding='utf-8')
    else:
        gap = time.monotonic() - last_request
        if gap < 1:
            time.sleep(1 - gap)
        last_request = time.monotonic()
        url = f'https://cdprojektred.atlassian.net/wiki/api/v2/pages/{pid}?body-format=storage'
        req = request.Request(url, headers={'User-Agent': 'Witcher3OfflineDocs/1.0 (personal public documentation archive)', 'Accept': 'application/json'})
        try:
            with request.urlopen(req, timeout=45) as response:
                value = json.load(response)
            body = value.get('body', {}).get('storage', {}).get('value', '')
            if not body:
                raise ValueError('Storage body missing')
            source_path.write_text(body, encoding='utf-8')
        except Exception as exc:
            record['errors'][pid] = str(exc)
            print(f'{n}/{len(inventory["pages"])} ERROR {pid}: {exc}', flush=True)
            continue
    title_map = {}
    for att in item['attachments']:
        saved = manifest['attachments'].get(att['id'])
        if saved:
            title_map[att.get('title') or ''] = saved

    def image_repl(match):
        block = match.group(0)
        name_match = re.search(r'ri:filename="([^"]+)"', block)
        if not name_match:
            return '<span class="missing">Image referenced without a filename</span>'
        name = html.unescape(name_match.group(1))
        saved = title_map.get(name)
        if not saved:
            return f'<span class="missing">Image unavailable: {esc(name)}</span>'
        rel = os.path.relpath(root / saved['path'], reader_dir).replace(os.sep, '/')
        return f'<figure><a href="{esc(rel)}"><img loading="lazy" src="{esc(rel)}" alt="{esc(name)}"></a><figcaption>{esc(name)}</figcaption></figure>'

    rendered = re.sub(r'<ac:image\b[^>]*>.*?</ac:image>', image_repl, body, flags=re.S | re.I)

    def linked_attachment(match):
        name = html.unescape(match.group(1))
        saved = title_map.get(name)
        if not saved:
            return esc(name)
        rel = os.path.relpath(root / saved['path'], reader_dir).replace(os.sep, '/')
        if name.lower().endswith('.mp4'):
            return f'<video controls preload="metadata" style="max-width:100%" src="{esc(rel)}"></video><p><a href="{esc(rel)}">Download {esc(name)}</a></p>'
        return f'<a href="{esc(rel)}">{esc(name)}</a>'

    rendered = re.sub(r'<ri:attachment\b[^>]*ri:filename="([^"]+)"[^>]*/>', linked_attachment, rendered, flags=re.I)
    rendered = re.sub(r'<ac:plain-text-body>\s*<!\[CDATA\[(.*?)\]\]>\s*</ac:plain-text-body>', lambda m: '<pre><code>' + esc(m.group(1)) + '</code></pre>', rendered, flags=re.S | re.I)
    for tag, replacement in [('layout', 'div'), ('layout-section', 'section'), ('layout-cell', 'div'), ('rich-text-body', 'div'), ('link', 'span'), ('link-body', 'span')]:
        rendered = re.sub(rf'<ac:{tag}\b[^>]*>', f'<{replacement}>', rendered, flags=re.I)
        rendered = re.sub(rf'</ac:{tag}>', f'</{replacement}>', rendered, flags=re.I)
    rendered = re.sub(r'<ac:structured-macro\b[^>]*>', '<aside class="macro">', rendered, flags=re.I)
    rendered = re.sub(r'</ac:structured-macro>', '</aside>', rendered, flags=re.I)
    rendered = re.sub(r'<ri:[^>]+/>', '', rendered, flags=re.I)
    rendered = re.sub(r'</?ac:[^>]+>', '', rendered, flags=re.I)
    rendered = re.sub(r'<script\b[^>]*>.*?</script>', '', rendered, flags=re.S | re.I)
    reader = reader_dir / f'{pid}.html'
    page = item['page']
    gallery = f'../media/{pid}.html'
    reader.write_text(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(page['title'])}</title><style>body{{background:#11191e;color:#e1e8eb;font:17px/1.65 system-ui;max-width:950px;margin:2em auto;padding:0 1em}}a{{color:#87d2ff}}h1,h2,h3{{color:#f1dfb4;line-height:1.25}}figure{{margin:1.3em 0;background:#21313a;padding:.8em;border-radius:8px}}img{{max-width:100%;height:auto}}figcaption{{font-size:.8em;color:#a5b9c3}}pre{{background:#1b2930;padding:1em;overflow:auto}}aside.macro{{border-left:4px solid #70a7b8;background:#1b2930;padding:.3em 1em;margin:1em 0}}.missing{{color:#ffbf9d}}</style></head><body><nav><a href="../../../index.html">Archive home</a> · <a href="{gallery}">All page attachments</a> · <a href="{esc(page['url'])}">Original page</a></nav><h1>{esc(page['title'])}</h1><main>{rendered}</main><p><a href="{gallery}">See all attachments for this article</a></p></body></html>''', encoding='utf-8')
    row = db.execute('SELECT path FROM items WHERE url=?', (page['url'],)).fetchone()
    if row:
        old = root / row[0]
        replace_once(old, 'W3 INLINE READER', f'<aside style="margin:2em;padding:1em;background:#14242b;color:white;border:2px solid #6ca8ba;position:relative;z-index:9999"><a style="color:#a8e5ff" href="../reader/{pid}.html">Read this article with screenshots inline</a></aside>')
        for nav_name in ('index.html', 'guides.html'):
            nav = root / nav_name
            text = nav.read_text(encoding='utf-8')
            old_href = row[0]
            new_href = reader.relative_to(root).as_posix()
            if old_href in text:
                nav.write_text(text.replace(old_href, new_href), encoding='utf-8')
    record['pages'][pid] = {'source': source_path.relative_to(root).as_posix(), 'reader': reader.relative_to(root).as_posix(), 'sha256': hashlib.sha256(body.encode()).hexdigest()}
    if n % 10 == 0:
        print(f'{n}/{len(inventory["pages"])} rendered', flush=True)
(root / 'storage_manifest.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
print(f'DONE {len(record["pages"])} reader pages; {len(record["errors"])} errors', flush=True)
if record['errors']:
    raise SystemExit(1)
