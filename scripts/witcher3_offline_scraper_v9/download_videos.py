#!/usr/bin/env python3
"""Save the public YouTube tutorials embedded by official REDkit documentation."""
import argparse
import html
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
from urllib import request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=Path('witcher3-offline-library'), help='Archive directory made by witcher_archive.py')
args = parser.parse_args()
root = args.output.expanduser().resolve()
db = sqlite3.connect(root / 'library.sqlite3')
sources = []
for (rel,) in db.execute("SELECT path FROM items WHERE source='official-remastered-guides' AND kind='page' AND status='ok'"):
    sources.append(html.unescape((root / rel).read_text(encoding='utf-8')).replace('\\/', '/'))
url = 'https://cdprojektred.atlassian.net/wiki/api/v2/pages/28737537?body-format=storage'
req = request.Request(url, headers={'User-Agent': 'Witcher3OfflineDocs/1.0 (personal public documentation archive)', 'Accept': 'application/json'})
try:
    import json
    with request.urlopen(req, timeout=35) as response:
        body = json.load(response).get('body', {}).get('storage', {}).get('value', '')
    if not body:
        raise ValueError('Video tutorial page storage body is empty')
    sources.append(html.unescape(body))
    (root / 'video_page_storage.html').write_text(body, encoding='utf-8')
except Exception as exc:
    saved_storage = root / 'video_page_storage.html'
    if saved_storage.exists():
        sources.append(html.unescape(saved_storage.read_text(encoding='utf-8')))
        print('Using saved video page source after live fetch failed:', exc, flush=True)
    else:
        raise SystemExit(f'Video page source unavailable: {exc}')
ids = sorted(set(re.findall(r'(?:youtube\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})', '\n'.join(sources))))
video_list = root / 'video_urls.txt'
video_list.write_text(''.join(f'https://www.youtube.com/watch?v={vid}\n' for vid in ids), encoding='utf-8')
print(f'Found {len(ids)} distinct embedded YouTube videos', flush=True)
if not ids:
    raise SystemExit('No video IDs found; refusing to claim a complete archive')
if not shutil.which('yt-dlp') or not shutil.which('ffmpeg'):
    raise SystemExit('Downloading YouTube videos requires yt-dlp and ffmpeg on PATH; video_urls.txt was saved')
videos = root / 'videos'
videos.mkdir(exist_ok=True)
cmd = ['yt-dlp', '--ignore-errors', '--no-playlist', '--no-progress', '--sleep-requests', '1',
       '--sleep-interval', '1', '--max-sleep-interval', '2',
       '-f', 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]',
       '--merge-output-format', 'mp4', '--write-thumbnail', '--convert-thumbnails', 'jpg',
       '--download-archive', str(videos / 'downloaded.txt'),
       '-o', str(videos / '%(id)s--%(title).120B.%(ext)s'), '-a', str(video_list)]
subprocess.run(cmd, check=False)
saved = sum(bool(list(videos.glob(f'{vid}--*.mp4'))) for vid in ids)
print(f'Saved {saved}/{len(ids)} YouTube videos', flush=True)
if saved != len(ids):
    raise SystemExit(1)
