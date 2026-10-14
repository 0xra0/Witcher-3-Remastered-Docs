#!/usr/bin/env python3
"""Inventory public REDkit page attachments through the Confluence v2 API."""
import json
import argparse
import re
import sqlite3
import time
from pathlib import Path
from urllib import request, parse

PARSER = argparse.ArgumentParser(description=__doc__)
PARSER.add_argument('--output', type=Path, default=Path('witcher3-offline-library'), help='Archive directory made by witcher_archive.py')
PARSER.add_argument('--inventory', type=Path, help='Inventory JSON path; defaults to <output>/media_inventory.json')
ARGS = PARSER.parse_args()
ROOT = ARGS.output.expanduser().resolve()
OUT = (ARGS.inventory or ROOT / 'media_inventory.json').expanduser().resolve()
BASE = 'https://cdprojektred.atlassian.net'
HEADERS = {'User-Agent': 'Witcher3OfflineDocs/1.0 (personal public documentation archive)', 'Accept': 'application/json'}

db = sqlite3.connect(ROOT / 'library.sqlite3')
pages = []
for url, title in db.execute("SELECT url,title FROM items WHERE source='official-redkit-full-site' AND kind='page' AND status='ok'"):
    m = re.search(r'/pages/(\d+)/', url)
    if m:
        pages.append({'id': m.group(1), 'url': url, 'title': title})
old = json.loads(OUT.read_text()) if OUT.exists() else {'pages': {}, 'errors': {}}
for i, page in enumerate(pages, 1):
    pid = page['id']
    if pid in old['pages']:
        continue
    next_url = f'{BASE}/wiki/api/v2/pages/{pid}/attachments?limit=100'
    results = []
    try:
        while next_url:
            req = request.Request(next_url, headers=HEADERS)
            with request.urlopen(req, timeout=35) as res:
                body = json.load(res)
            results += body.get('results', [])
            nxt = body.get('_links', {}).get('next')
            next_url = parse.urljoin(BASE, nxt) if nxt else None
            time.sleep(1)
        old['pages'][pid] = {'page': page, 'attachments': results}
        old['errors'].pop(pid, None)
        print(f'{i}/{len(pages)} {pid}: {len(results)}', flush=True)
    except Exception as exc:
        old['errors'][pid] = str(exc)
        print(f'{i}/{len(pages)} {pid}: ERROR {exc}', flush=True)
    OUT.write_text(json.dumps(old, indent=2), encoding='utf-8')
    time.sleep(1)
print('DONE', len(old['pages']), 'pages;', sum(len(v['attachments']) for v in old['pages'].values()), 'attachments;', len(old['errors']), 'errors', flush=True)
