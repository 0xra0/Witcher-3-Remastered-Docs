#!/usr/bin/env python3
"""Verify saved documentation, media sizes and hashes, and playable videos."""
import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from urllib.parse import urlsplit, unquote

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=Path('witcher3-offline-library'))
args = parser.parse_args()
root = args.output.expanduser().resolve()
inventory = json.loads((root / 'media_inventory.json').read_text(encoding='utf-8'))
media = json.loads((root / 'media_manifest.json').read_text(encoding='utf-8'))
modio = json.loads((root / 'modio_image_manifest.json').read_text(encoding='utf-8'))
storage = json.loads((root / 'storage_manifest.json').read_text(encoding='utf-8'))
video_ids = [line.split('v=')[-1].strip() for line in (root / 'video_urls.txt').read_text(encoding='utf-8').splitlines() if line.strip()]
errors = []
expected = {a['id']: a for p in inventory['pages'].values() for a in p['attachments']}
saved = media['attachments']
unavailable = media.get('unavailable', {})
if set(expected) != set(saved) | set(unavailable):
    errors.append('Attachment ID coverage differs from API inventory')
if set(saved) & set(unavailable):
    errors.append('Attachment appears in both saved and unavailable lists')
if media['errors'] or inventory['errors'] or storage['errors']:
    errors.append('One or more API/download/storage errors remain')
kind_counts = {'images': 0, 'videos': 0, 'other': 0}
bytes_saved = 0
for aid, item in saved.items():
    if aid not in expected:
        errors.append(f'Saved attachment absent from current inventory: {aid}')
        continue
    path = root / item['path']
    if not path.is_file():
        errors.append(f'Missing attachment {aid}')
        continue
    actual_size = path.stat().st_size
    bytes_saved += actual_size
    if actual_size != item['bytes'] or actual_size != int(expected[aid]['fileSize']):
        errors.append(f'Attachment size mismatch {aid}')
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    if h.hexdigest() != item['sha256']:
        errors.append(f'Attachment hash mismatch {aid}')
    typ = (item.get('media_type') or '').strip('"').lower()
    ext = path.suffix.lower()
    if typ.startswith('image/') or ext in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif'}:
        kind_counts['images'] += 1
    elif typ.startswith('video/') or ext in {'.mp4', '.mkv', '.webm', '.mov'}:
        kind_counts['videos'] += 1
    else:
        kind_counts['other'] += 1
for url, item in modio.items():
    path = root / item['path']
    if not path.is_file() or path.stat().st_size != item['bytes']:
        errors.append(f'Mod.io image missing or wrong size: {url}')
    elif hashlib.sha256(path.read_bytes()).hexdigest() != item.get('sha256'):
        errors.append(f'Mod.io image hash mismatch: {url}')
if set(storage['pages']) != set(inventory['pages']):
    errors.append('Reader pages do not cover every Confluence page ID')
for pid, item in storage['pages'].items():
    if not (root / item['reader']).is_file() or not (root / item['source']).is_file():
        errors.append(f'Storage or reader file missing: {pid}')


class LocalRefs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        self.refs.extend(value for key, value in attrs if key in {'href', 'src', 'poster'} and value)


generated_pages = [root / 'index.html', root / 'media.html', root / 'videos.html']
generated_pages += list((root / 'sites/official-redkit-full-site/media').glob('*.html'))
generated_pages += [root / item['reader'] for item in storage['pages'].values()]
for page in generated_pages:
    if not page.is_file():
        errors.append(f'Generated page missing: {page.relative_to(root)}')
        continue
    parser = LocalRefs()
    parser.feed(page.read_text(encoding='utf-8'))
    for ref in parser.refs:
        url = urlsplit(ref)
        if url.scheme or url.netloc or ref.startswith(('#', 'data:', 'mailto:')):
            continue
        if url.path and not (page.parent / unquote(url.path)).exists():
            errors.append(f'Broken local link in {page.relative_to(root)}: {ref}')
video_bytes = 0
partials = list((root / 'videos').glob('*.part')) + list((root / 'videos').glob('*.f*.mp4'))
if partials:
    errors.append(f'{len(partials)} incomplete or unmerged video parts remain')
for vid in video_ids:
    files = list((root / 'videos').glob(f'{vid}--*.mp4'))
    if len(files) != 1:
        errors.append(f'Expected one MP4 for YouTube video {vid}; found {len(files)}')
        continue
    video_bytes += files[0].stat().st_size
    if not list((root / 'videos').glob(f'{vid}--*.jpg')):
        errors.append(f'Video thumbnail missing: {vid}')
    if shutil.which('ffprobe'):
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'json', str(files[0])], capture_output=True, text=True)
        if result.returncode:
            errors.append(f'ffprobe could not read {vid}')
        else:
            streams = {s.get('codec_type') for s in json.loads(result.stdout).get('streams', [])}
            if not {'video', 'audio'} <= streams:
                errors.append(f'MP4 lacks video or audio stream: {vid}')
db = sqlite3.connect(root / 'library.sqlite3')
page_count = db.execute("SELECT count(*) FROM items WHERE kind='page' AND status='ok'").fetchone()[0]
db.close()
report = {'verified_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'status': 'ok' if not errors else 'incomplete', 'pages': page_count, 'confluence_pages_with_attachments_api': len(inventory['pages']), 'attachments_listed': len(expected), 'attachments_saved': len(saved), 'attachments_unavailable_at_source': len(unavailable), 'attachment_counts': kind_counts, 'attachment_bytes': bytes_saved, 'modio_images': len(modio), 'youtube_videos': len(video_ids), 'youtube_video_bytes': video_bytes, 'reader_pages': len(storage['pages']), 'errors': errors}
(root / 'VERIFICATION.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
if errors:
    raise SystemExit(1)
