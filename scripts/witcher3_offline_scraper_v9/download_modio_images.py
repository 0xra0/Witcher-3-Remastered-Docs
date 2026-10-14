#!/usr/bin/env python3
"""Save images referenced by the four official mod.io guide snapshots."""
import hashlib
import argparse
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib import request, parse

PARSER = argparse.ArgumentParser(description=__doc__)
PARSER.add_argument('--output', type=Path, default=Path('witcher3-offline-library'), help='Archive directory made by witcher_archive.py')
ARGS = PARSER.parse_args()
ROOT = ARGS.output.expanduser().resolve()
db = sqlite3.connect(ROOT / 'library.sqlite3')
assets = ROOT / 'sites/official-remastered-guides/assets'
manifest_path = ROOT / 'modio_image_manifest.json'
manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
for url, rel in db.execute("SELECT url,path FROM items WHERE source='official-remastered-guides' AND kind='page' AND status='ok'"):
    page = ROOT / rel
    doc = page.read_text(encoding='utf-8')
    urls = sorted(set(re.findall(r'https://image\.modcdn\.io/[^"\'\s<>]+?\.(?:png|jpe?g|webp|gif)', doc, re.I)))
    for image_url in urls:
        token = hashlib.sha256(image_url.encode()).hexdigest()[:20]
        ext = Path(parse.urlsplit(image_url).path).suffix.lower()
        target = assets / (token + ext)
        if not target.exists():
            req = request.Request(image_url, headers={'User-Agent': 'Witcher3OfflineDocs/1.0 (personal public documentation archive)', 'Accept': 'image/*'})
            try:
                with request.urlopen(req, timeout=60) as response:
                    ctype = response.headers.get('Content-Type', '')
                    if not ctype.startswith('image/'):
                        raise ValueError(f'Unexpected MIME type: {ctype}')
                    target.write_bytes(response.read(25 * 1024 * 1024 + 1))
                if target.stat().st_size > 25 * 1024 * 1024:
                    target.unlink()
                    raise ValueError('Image exceeds 25 MiB')
                time.sleep(1)
            except Exception as exc:
                print('FAILED', image_url, exc, flush=True)
                continue
        manifest[image_url] = {'path': target.relative_to(ROOT).as_posix(), 'bytes': target.stat().st_size,
                               'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
        doc = doc.replace(image_url, '../assets/' + target.name)
    page.write_text(doc, encoding='utf-8')
for item in manifest.values():
    target = ROOT / item['path']
    if target.is_file():
        item['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('Saved', len(manifest), 'mod.io guide images')
