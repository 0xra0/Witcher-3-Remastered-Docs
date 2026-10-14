#!/usr/bin/env python3
"""Download the public, original Confluence attachments in media_inventory.json.

Runs without credentials, saves every attachment type, verifies advertised sizes,
and resumes by rechecking existing files. A failed file remains in the manifest.
"""
import hashlib
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib import request, parse
from urllib.error import HTTPError

PARSER = argparse.ArgumentParser(description=__doc__)
PARSER.add_argument('--output', type=Path, default=Path('witcher3-offline-library'), help='Archive directory made by witcher_archive.py')
PARSER.add_argument('--inventory', type=Path, help='Inventory JSON path; defaults to <output>/media_inventory.json')
PARSER.add_argument('--workers', type=int, default=4, help='Concurrent transfers; requests to Confluence still start at least one second apart')
ARGS = PARSER.parse_args()
ROOT = ARGS.output.expanduser().resolve()
INVENTORY = (ARGS.inventory or ROOT / 'media_inventory.json').expanduser().resolve()
BASE = 'https://cdprojektred.atlassian.net'
DEST = ROOT / 'sites/official-redkit-full-site/attachments'
MANIFEST = ROOT / 'media_manifest.json'
UA = 'Witcher3OfflineDocs/1.0 (personal public documentation archive)'


def safe_name(name):
    name = re.sub(r'[\\/\x00-\x1f]+', '_', name).strip(' .')
    return name[:160] or 'attachment'


def file_sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def persist(data):
    temp = MANIFEST.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, MANIFEST)


inventory = json.loads(INVENTORY.read_text(encoding='utf-8'))
manifest = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {'attachments': {}, 'errors': {}, 'unavailable': {}}
manifest.setdefault('unavailable', {})
tasks = [(pid, page['page'], att) for pid, page in inventory['pages'].items() for att in page['attachments']]
DEST.mkdir(parents=True, exist_ok=True)
last_request = 0.0
pace_lock = threading.Lock()
pending = []
for pid, page, att in tasks:
    aid = att['id']
    if not int(att.get('fileSize') or 0) and (att.get('title') or '').startswith('Invalid file id'):
        manifest['unavailable'][aid] = {'page_id': pid, 'title': att.get('title'), 'reason': 'Source API lists a zero-byte invalid file ID; original download returns 404'}
        manifest['errors'].pop(aid, None)
        continue
    name = safe_name(att.get('title') or aid)
    relative = Path('sites/official-redkit-full-site/attachments') / pid / f'{aid}--{name}'
    target = ROOT / relative
    expected = int(att.get('fileSize') or 0)
    if target.exists() and (not expected or target.stat().st_size == expected):
        if aid not in manifest['attachments']:
            manifest['attachments'][aid] = {'page_id': pid, 'page_url': page['url'], 'title': att.get('title'), 'media_type': att.get('mediaType'), 'bytes': target.stat().st_size, 'sha256': file_sha256(target), 'path': relative.as_posix(), 'url': parse.urljoin(BASE + '/wiki/', att['downloadLink'].lstrip('/'))}
        continue
    pending.append((pid, page, att, relative, target, expected))
persist(manifest)
print(f'{len(tasks)} listed; {len(pending)} need transfer; {len(manifest["unavailable"])} broken at source', flush=True)


def paced_request():
    global last_request
    with pace_lock:
        gap = time.monotonic() - last_request
        if gap < 1:
            time.sleep(1 - gap)
        last_request = time.monotonic()


def transfer(job):
    pid, page, att, relative, target, expected = job
    aid = att['id']
    target.parent.mkdir(parents=True, exist_ok=True)
    endpoint = parse.urljoin(BASE + '/wiki/', att['downloadLink'].lstrip('/'))
    for attempt in range(3):
        try:
            paced_request()
            req = request.Request(endpoint, headers={'User-Agent': UA, 'Accept': '*/*'})
            with request.urlopen(req, timeout=90) as response:
                ctype = response.headers.get('Content-Type', '').lower()
                if 'text/html' in ctype or 'application/json' in ctype:
                    raise ValueError(f'Expected attachment, got {ctype}')
                temp = target.with_name(target.name + '.part')
                h = hashlib.sha256()
                size = 0
                with temp.open('wb') as out:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        h.update(chunk)
                        size += len(chunk)
                if expected and size != expected:
                    temp.unlink(missing_ok=True)
                    raise ValueError(f'Size mismatch {size} != {expected}')
                os.replace(temp, target)
            return aid, {'page_id': pid, 'page_url': page['url'], 'title': att.get('title'), 'media_type': att.get('mediaType'), 'bytes': size, 'sha256': h.hexdigest(), 'path': relative.as_posix(), 'url': endpoint}, None
        except Exception as exc:
            if attempt == 2 or isinstance(exc, HTTPError) and exc.code in (401, 403, 404):
                return aid, None, {'page_id': pid, 'title': att.get('title'), 'url': endpoint, 'error': str(exc)}
            time.sleep(2 * (attempt + 1))


with ThreadPoolExecutor(max_workers=max(1, min(ARGS.workers, 8))) as pool:
    futures = [pool.submit(transfer, job) for job in pending]
    for n, future in enumerate(as_completed(futures), 1):
        aid, saved, err = future.result()
        if saved:
            manifest['attachments'][aid] = saved
            manifest['errors'].pop(aid, None)
        else:
            manifest['errors'][aid] = err
            print(f'{n}/{len(pending)} FAILED {aid}: {err["error"]}', flush=True)
        if n % 20 == 0 or n == len(pending):
            persist(manifest)
            print(f'{n}/{len(pending)} newly transferred; total saved={len(manifest["attachments"])} errors={len(manifest["errors"])}', flush=True)
persist(manifest)
print(f'DONE saved={len(manifest["attachments"])} unavailable={len(manifest["unavailable"])} errors={len(manifest["errors"])}', flush=True)
