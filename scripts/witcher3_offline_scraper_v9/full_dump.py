#!/usr/bin/env python3
"""Build a resumable offline copy of the public official Witcher 3 modding docs."""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=Path('witcher3-offline-library'))
parser.add_argument('--skip-crawl', action='store_true', help='Use an existing completed page archive')
args = parser.parse_args()
output = args.output.expanduser().resolve()
here = Path(__file__).resolve().parent


def run(script, *extra, required=True):
    cmd = [sys.executable, str(here / script), *extra, '--output', str(output)]
    print('\nRUN', ' '.join(cmd), flush=True)
    completed = subprocess.run(cmd, check=False)
    if completed.returncode and required:
        raise SystemExit(f'{script} failed with exit code {completed.returncode}; rerun to resume')
    return completed.returncode


if not args.skip_crawl:
    run('witcher_archive.py', 'crawl')
run('inventory_media.py')
run('download_media.py')
run('download_modio_images.py')
video_status = run('download_videos.py', required=False)
run('render_storage.py')
run('build_media_pages.py')
run('verify_archive.py')
media = json.loads((output / 'media_manifest.json').read_text(encoding='utf-8'))
inventory = json.loads((output / 'media_inventory.json').read_text(encoding='utf-8'))
db = sqlite3.connect(output / 'library.sqlite3')
page_ids = {re.search(r'/pages/(\d+)/', url).group(1) for (url,) in db.execute("SELECT url FROM items WHERE source='official-redkit-full-site' AND kind='page' AND status='ok'") if re.search(r'/pages/(\d+)/', url)}
db.close()
expected = sum(len(page['attachments']) for page in inventory['pages'].values())
saved = len(media['attachments'])
unavailable = len(media.get('unavailable', {}))
print(f'Confluence attachments: {saved} saved, {unavailable} broken at source, {expected} listed; video command exit: {video_status}', flush=True)
if saved + unavailable != expected or media['errors'] or inventory['errors'] or set(inventory['pages']) != page_ids or video_status:
    raise SystemExit('Archive is incomplete; inspect media_manifest.json and rerun to resume')
