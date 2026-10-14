#!/usr/bin/env python3
"""Respectful, resumable offline documentation and public Git repository archiver.

Python 3.10+; standard library for crawling, optional Playwright for JS sites.
No login, paywall, anti-bot bypass, or mod archive downloader.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
from html import escape, unescape
from html.parser import HTMLParser
import http.server
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib import error, parse, request, robotparser
import xml.etree.ElementTree as ET

LOG = logging.getLogger("w3archive")
UA = "Witcher3OfflineDocs/1.0 (educational personal archive; respects robots.txt)"
TEXT_EXTENSIONS = {".txt", ".md", ".mdx", ".rst", ".ws", ".xml", ".csv", ".json",
                   ".toml", ".yaml", ".yml", ".py", ".rs", ".cs", ".ini",
                   ".cfg", ".sh", ".html", ".htm", ".lua", ".java", ".cpp", ".h",
                   ".hpp", ".js", ".ts", ".css", ".bat", ".ps1"}
ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif",
                    ".ico", ".css", ".pdf", ".webm", ".woff", ".woff2", ".ttf",
                    ".drawio"}
SKIP_EXTENSIONS = {".zip", ".7z", ".rar", ".exe", ".dll", ".pak", ".bundle",
                   ".mp4", ".mov", ".mkv", ".iso", ".torrent", ".dmg"}
TIMEOUT = 35


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(url: str) -> str:
    u = parse.urlsplit(url.strip())
    if u.scheme.lower() not in ("http", "https") or not u.hostname:
        return ""
    # Remove tracking and navigational query parameters without losing page IDs.
    keep = [(k, v) for k, v in parse.parse_qsl(u.query, keep_blank_values=False)
            if k.lower() in {"pageid", "page", "start"}]
    host = u.hostname.lower()
    port = f":{u.port}" if u.port else ""
    path = parse.quote(parse.unquote(u.path or "/"), safe="/%:@!$&'()*+,;=-._~")
    # The published English MkDocs build really does use /docs/en/en/ for most
    # articles.  Its navigation also contains a few broken relative links.  Only
    # repair aliases confirmed against the published nav and live page URLs.
    if host == "redkit-resources.github.io":
        path = redkit_page_alias(path)
        if path.startswith("/docs/en/en/assets/"):
            path = "/docs/en/assets/" + path[len("/docs/en/en/assets/"):]
        elif path.startswith("/docs/en/unofficial_docs/assets/"):
            path = "/docs/en/assets/" + path[len("/docs/en/unofficial_docs/assets/"):]
    return parse.urlunsplit((u.scheme.lower(), host + port, path, parse.urlencode(keep), ""))


def redkit_page_alias(path: str) -> str:
    """Fix only REDkit URLs known to be absent in its published English nav."""
    root = "/docs/en/"
    if not path.startswith(root):
        return path
    tail = path[len(root):]
    if tail == "unofficial_docs/references/editors/asset_browser.md":
        return root + "unofficial_docs/references/editors/asset_browser/"
    if re.match(r"^en/(?:base|bugs|guides|references|useful_features)(?:/|$)", tail):
        return root + "en/unofficial_docs/" + tail[len("en/"):]
    # These are malformed relative links left by MkDocs in both language roots.
    if tail.startswith("en/unofficial_docs/"):
        prefix = "en/unofficial_docs/"
    elif tail.startswith("unofficial_docs/"):
        prefix = "unofficial_docs/"
    else:
        prefix = ""
    if prefix:
        rest = tail[len(prefix):]
        parts = rest.strip("/").split("/")
        if parts == ["references", "world", "world_params", "shadow_params"]:
            return root + "en/unofficial_docs/references/world/shadow_params/"
        if len(parts) == 1:
            category = {
                "create_animation_for_npc": "guides",
                "create_usm_video": "guides",
                "create_new_hud_medallion": "guides",
                "copy_info_from_objects_prop": "useful_features",
                "setup_script_studio": "useful_features",
                "shortcuts": "useful_features",
                "uv_bug": "bugs",
                "file_extensions": "references",
                "data_types": "references",
            }.get(parts[0])
            if category:
                return root + "en/unofficial_docs/" + category + "/" + rest
            if parts[0] == "loading_screens":
                return root + "unofficial_docs/base/loading_screens/"
        if len(parts) == 2 and parts[0] == "behavior" and parts[1] in {"behavior", "behavior_architecture"}:
            return root + "en/unofficial_docs/base/" + rest
        if len(parts) == 2 and parts[0] == "world":
            if parts[1] == "world":
                return root + "en/unofficial_docs/base/" + rest
            if parts[1] in {"world_params", "umbra", "pathlib", "shadow_params", "env_params", "merged_geometry_params"}:
                return root + "en/unofficial_docs/references/" + rest
    if tail == "base/loading_screens/":
        return root + "unofficial_docs/base/loading_screens/"
    return path


def slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")[:100] or "item"


def bytes_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def to_text(data: bytes, content_type: str = "") -> str:
    m = re.search(r"charset\s*=\s*['\"]?([\w-]+)", content_type, re.I)
    for encoding in ([m.group(1)] if m else []) + ["utf-8-sig", "utf-8", "windows-1252"]:
        try:
            return data.decode(encoding)
        except (UnicodeError, LookupError):
            pass
    return data.decode("utf-8", errors="replace")


class PageParser(HTMLParser):
    BLOCKS = {"div", "p", "article", "section", "h1", "h2", "h3", "h4", "h5", "h6", "li",
              "br", "pre", "tr", "blockquote", "dt", "dd"}
    IGNORED = {"script", "style", "noscript", "svg", "canvas"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.assets: list[str] = []
        self.title = ""
        self._title_depth = 0
        self._ignore = 0
        self._main_depth = 0
        self._main = []
        self._body = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.IGNORED:
            self._ignore += 1
        if tag == "title":
            self._title_depth += 1
        if tag in ("main", "article") or a.get("role") == "main":
            self._main_depth += 1
        if tag in self.BLOCKS:
            self._body.append("\n")
            if self._main_depth:
                self._main.append("\n")
        if tag == "a" and a.get("href"):
            if is_asset_url(a["href"]):
                self.assets.append(a["href"])
            else:
                self.links.append(a["href"])
        if tag == "link" and "stylesheet" in (a.get("rel") or "") and a.get("href"):
            self.assets.append(a["href"])
        if tag in ("img", "source", "video"):
            for key in ("src", "poster", "data-src"):
                if a.get(key):
                    self.assets.append(a[key])
            if a.get("srcset"):
                for entry in a["srcset"].split(","):
                    self.assets.append(entry.strip().split()[0])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.IGNORED and self._ignore:
            self._ignore -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in ("main", "article") and self._main_depth:
            self._main_depth -= 1
        if tag in self.BLOCKS:
            self._body.append("\n")
            if self._main_depth:
                self._main.append("\n")

    def handle_data(self, data):
        if self._title_depth:
            self.title += data
        if not self._ignore:
            self._body.append(data)
            if self._main_depth:
                self._main.append(data)

    def result(self) -> str:
        text = "".join(self._main if len("".join(self._main)) > 150 else self._body)
        return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def parse_html(html: str) -> PageParser:
    p = PageParser()
    p.feed(html)
    p.close()
    return p


def validate_document(doc: str, ctype: str, title: str, text: str, site: dict) -> str | None:
    """Detect crawler-visible placeholders, not pages of actual tutorial content.

    Return a reason instead of quietly indexing 200 OK error/SPA shells. Source-specific
    title checks are configured rather than treating legitimate short guides as errors.
    """
    lead = doc.lstrip()[:500]
    if (lead.startswith("{") and (
        '"requestCorrelationId"' in lead or '"serverDuration"' in lead)):
        return "Atlassian returned a JSON error/placeholder rather than documentation"
    if "application/json" in ctype.lower() and not site.get("markdown"):
        return "JSON response rather than documentation HTML"
    if len(text.strip()) < int(site.get("min_text_chars", 0)):
        return f"document body too short ({len(text.strip())} characters)"
    if title.strip().casefold() in {t.casefold() for t in site.get("reject_titles", [])}:
        return f"generic placeholder title: {title!r}"
    if ("<html" in doc[:4096].lower() and
        "javascript must be enabled" in text.lower() and len(text) < 500):
        return "unrendered JavaScript application"
    return None


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "library.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                source TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                title TEXT,
                path TEXT,
                sha256 TEXT,
                fetched_at TEXT,
                status TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS frontier (
                site TEXT NOT NULL,
                url TEXT NOT NULL,
                depth INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'queued',
                PRIMARY KEY (site, url)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS item_search USING fts5(
                title, content, tokenize='unicode61'
            );
            CREATE INDEX IF NOT EXISTS items_source ON items(source);
            CREATE TABLE IF NOT EXISTS repo_state (
                repo TEXT PRIMARY KEY,
                head TEXT NOT NULL,
                indexed_files INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def remember(self, kind, source, url, title, path, data, status="ok", error_message=""):
        digest = bytes_sha(data.encode("utf-8") if isinstance(data, str) else data)
        self.db.execute("""INSERT INTO items(kind,source,url,title,path,sha256,fetched_at,status,error)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
            kind=excluded.kind,source=excluded.source,title=excluded.title,path=excluded.path,
            sha256=excluded.sha256,fetched_at=excluded.fetched_at,status=excluded.status,error=excluded.error""",
            (kind, source, url, title, str(path) if path else None, digest, utcnow(), status, error_message))
        ident = self.db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
        self.db.execute("DELETE FROM item_search WHERE rowid=?", (ident,))
        if status == "ok" and kind in ("page", "source"):
            # A textual row per page / source file. HTML and binary images are NOT indexed directly.
            content = data if isinstance(data, str) else ""
            self.db.execute("INSERT INTO item_search(rowid,title,content) VALUES(?,?,?)",
                            (ident, title or "", content[:1_000_000]))
        self.db.commit()

    def enqueue(self, site: str, url: str, depth: int, refresh_days: int):
        old = self.db.execute("SELECT state FROM frontier WHERE site=? AND url=?", (site, url)).fetchone()
        if old:
            if old["state"] == "done" and refresh_days:
                last = self.db.execute("SELECT fetched_at FROM items WHERE url=?", (url,)).fetchone()
                if last and last["fetched_at"]:
                    try:
                        if datetime.fromisoformat(last["fetched_at"]) + timedelta(days=refresh_days) <= datetime.now(timezone.utc):
                            self.db.execute("UPDATE frontier SET state='queued' WHERE site=? AND url=?", (site, url))
                    except ValueError:
                        pass
        else:
            self.db.execute("INSERT INTO frontier(site,url,depth,state) VALUES(?,?,?,'queued')", (site, url, depth))
        self.db.commit()

    def next_url(self, site: str):
        return self.db.execute("SELECT url,depth FROM frontier WHERE site=? AND state='queued' ORDER BY depth,url LIMIT 1", (site,)).fetchone()

    def mark(self, site, url, state):
        self.db.execute("UPDATE frontier SET state=? WHERE site=? AND url=?", (state, site, url))
        self.db.commit()

    def count(self, site):
        return self.db.execute("SELECT count(*) FROM frontier WHERE site=? AND state='done'", (site,)).fetchone()[0]

    def url_to_path(self, url: str):
        result = self.db.execute("SELECT path FROM items WHERE url=? AND status='ok'", (url,)).fetchone()
        return self.root / result[0] if result and result[0] else None


class Fetcher:
    def __init__(self, delay: float, max_mb: float):
        self.delay = max(0.1, delay)
        self.max_bytes = int(max_mb * 1024 * 1024)
        self.last_access: dict[str, float] = defaultdict(float)
        self.robots: dict[str, robotparser.RobotFileParser] = {}
        self.crawl_delays: dict[str, float] = {}
        self.opener = request.build_opener()

    def _pace(self, host: str):
        remaining = max(self.delay, self.crawl_delays.get(host, 0.0)) - (time.monotonic() - self.last_access[host])
        if remaining > 0:
            time.sleep(remaining)
        self.last_access[host] = time.monotonic()

    def allowed(self, url):
        s = parse.urlsplit(url)
        origin = f"{s.scheme}://{s.netloc}"
        if origin not in self.robots:
            p = robotparser.RobotFileParser()
            p.set_url(origin + "/robots.txt")
            self._pace(s.netloc)
            try:
                # RobotFileParser.read() uses Python's default User-Agent. Some
                # hosts reject that UA while serving robots.txt to this crawler.
                robots_request = request.Request(origin + "/robots.txt", headers={"User-Agent": UA})
                with self.opener.open(robots_request, timeout=TIMEOUT) as response:
                    lines = response.read(512 * 1024).decode("utf-8", "replace").splitlines()
                p.parse(lines)
            except error.HTTPError as e:
                if e.code in (404, 410):
                    # A missing robots.txt means this origin publishes no crawl rules.
                    p.parse([])
                else:
                    LOG.warning("Unable to read robots.txt at %s: %s", origin, e)
                    p.disallow_all = True
            except Exception as e:
                # Fail closed if robots policy could not be determined.
                LOG.warning("Unable to read robots.txt at %s: %s", origin, e)
                p.disallow_all = True
            self.robots[origin] = p
            self.crawl_delays[s.netloc] = float(p.crawl_delay(UA.split("/")[0]) or
                                                  p.crawl_delay("*") or 0)
        return self.robots[origin].can_fetch(UA, url)

    def get(self, url, max_bytes=None, extra_headers=None) -> tuple[bytes, str, str]:
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        s = parse.urlsplit(url)
        self._pace(s.netloc)
        headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/pdf,image/*,*/*;q=0.8"}
        headers.update(extra_headers or {})
        r = request.Request(url, headers=headers)
        with self.opener.open(r, timeout=TIMEOUT) as response:
            final_url = response.geturl()
            # Do not continue through cross-domain redirects without checking robots.
            if parse.urlsplit(final_url).netloc != s.netloc and not self.allowed(final_url):
                raise PermissionError(f"Redirect destination blocked by robots.txt: {final_url}")
            limit = max_bytes or self.max_bytes
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > limit:
                raise ValueError(f"too large ({content_length} bytes > {limit})")
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f"too large (limit {limit} bytes)")
            return data, response.headers.get("Content-Type", ""), final_url


class Browser:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.cli = None

    @staticmethod
    def find_cli():
        supplied = os.environ.get("W3_CHROMIUM")
        if supplied and Path(supplied).is_file():
            return supplied
        for name in ("chromium", "chromium-browser", "google-chrome", "chrome-headless-shell"):
            found = shutil.which(name)
            if found:
                return found
        cache = Path.home() / ".cache" / "puppeteer" / "chrome-headless-shell"
        matches = sorted(cache.glob("linux-*/chrome-headless-shell-linux64/chrome-headless-shell"), reverse=True)
        return str(matches[0]) if matches else None

    def start(self):
        if self.browser or self.cli:
            return
        self.cli = self.find_cli()
        if self.cli:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError("Browser rendering requires Chromium (set W3_CHROMIUM) or: python -m pip install playwright && python -m playwright install chromium") from e
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.launch(headless=True)
            self.context = self.browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900})
        except Exception:
            self.playwright.stop()
            self.playwright = None
            raise

    def render(self, url):
        self.start()
        if self.cli:
            result = subprocess.run([self.cli, "--headless", "--no-sandbox", "--disable-gpu",
                                     "--virtual-time-budget=8000", "--dump-dom", url],
                                    capture_output=True, text=True, timeout=60)
            content = result.stdout
            if result.returncode or "<html" not in content[:500].lower():
                raise RuntimeError(f"Chromium could not render {url}: {result.stderr[-800:]}")
            if len(content.encode("utf-8")) > 8 * 1024 * 1024:
                raise ValueError("rendered HTML too large")
            return content, 200, url
        page = self.context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1800)  # allow ordinary client rendering; not anti-bot bypass
            content = page.content()
            if len(content.encode("utf-8")) > 8 * 1024 * 1024:
                raise ValueError("rendered HTML too large")
            return content, response.status if response else 0, page.url
        finally:
            page.close()

    def close(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()


def host_match(host, patterns):
    host = host.lower()
    for pattern in patterns:
        pattern = pattern.lower()
        if pattern.startswith("*.") and (host == pattern[2:] or host.endswith(pattern[1:])):
            return True
        if host == pattern:
            return True
    return False


def belongs(url, site):
    u = parse.urlsplit(url)
    hosts = {parse.urlsplit(seed).hostname for seed in site["seeds"]}
    return u.scheme in ("https", "http") and u.hostname in hosts and any(u.path.startswith(p) for p in site["paths"])


def is_asset_url(url):
    """Classify linked downloads before they enter the HTML page frontier."""
    return Path(parse.urlsplit(url).path).suffix.lower() in ASSET_EXTENSIONS


def local_path(root, site, url, binary=False, ctype="", markdown=False):
    token = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    if not binary:
        extension = (".txt" if parse.urlsplit(url).path.endswith(".txt") else ".md") if markdown else ".html"
        return Path("sites") / slug(site) / "pages" / f"{token}{extension}"
    ext = Path(parse.urlsplit(url).path).suffix.lower()
    if ext not in ASSET_EXTENSIONS:
        mime = ctype.split(";")[0].strip()
        ext = mimetypes.guess_extension(mime) or ".bin"
    return Path("sites") / slug(site) / "assets" / f"{token}{ext}"


def rewrite_html(doc, url, html_path, known):
    # Browser DOM snapshots can include live tracking/scripts. Offline docs do not need scripts.
    doc = re.sub(r"<script\b[^>]*>[\s\S]*?</script\s*>", "", doc, flags=re.I)
    doc = re.sub(r"<base\b[^>]*>", "", doc, flags=re.I)
    def replacement(m):
        attr, quote, raw = m.groups()
        target = unescape(raw.strip())
        if not target or target.startswith(("#", "javascript:", "data:", "mailto:", "tel:")):
            return m.group(0)
        absolute = canonical(parse.urljoin(url, target))
        if not absolute:
            return m.group(0)
        destination = known.get(absolute)
        new = os.path.relpath(destination, html_path.parent).replace(os.sep, "/") if destination else absolute
        frag = parse.urlsplit(parse.urljoin(url, target)).fragment
        if frag:
            new += "#" + frag
        return attr + quote + escape(new, quote=True) + quote
    doc = re.sub(r'(\b(?:href|src|poster|data-src)\s*=\s*)(["\'])(.*?)\2', replacement, doc, flags=re.I | re.S)
    # Improve lazy image support offline: scripts used to move data-src -> src.
    def lazy_image(m):
        tag = m.group(0)
        if not re.search(r'\bsrc\s*=', tag, re.I):
            found = re.search(r'\bdata-src\s*=\s*(["\'])(.*?)\1', tag, re.I | re.S)
            if found:
                tag = tag[:-1] + f' src="{escape(found.group(2), quote=True)}">'
        return tag
    doc = re.sub(r'<img\b[^>]*>', lazy_image, doc, flags=re.I)
    doc = re.sub(r'\bsrcset\s*=\s*(["\']).*?\1', '', doc, flags=re.I | re.S)
    return doc


class Archiver:
    def __init__(self, store, config, args):
        self.store, self.config, self.args = store, config, args
        self.fetcher = Fetcher(args.delay, args.max_asset_mb)
        self.browser = Browser()
        self.asset_count = 0
        self.summary = defaultdict(int)

    def repair_frontier_aliases(self, site):
        """Replace known bad saved links from previous releases with live paths."""
        for old in self.store.db.execute(
                "SELECT url,depth,state FROM frontier WHERE site=? AND state IN ('queued','failed')",
                (site["name"],)).fetchall():
            fixed = canonical(old["url"])
            if fixed and fixed != old["url"] and belongs(fixed, site):
                self.store.enqueue(site["name"], fixed, old["depth"], self.args.refresh_days)
                self.store.mark(site["name"], old["url"], "skipped")
                self.store.db.execute(
                    "UPDATE items SET status='skipped',error=? WHERE url=? AND status='failed'",
                    (f"Replaced by {fixed}", old["url"]))
        self.store.db.commit()

    def prepare_sitemap(self, site):
        if not site.get("sitemap") or self.args.no_sitemaps:
            return
        # Only public sitemap listings, filtered into the user's configured scope.
        seed = parse.urlsplit(site["seeds"][0])
        frontier = [f"{seed.scheme}://{seed.netloc}/sitemap.xml"]
        visited = set()
        while frontier and len(visited) < 12:
            addr = frontier.pop(0)
            if addr in visited:
                continue
            visited.add(addr)
            try:
                data, _ctype, _final = self.fetcher.get(addr, max_bytes=6 * 1024 * 1024)
                root = ET.fromstring(data)
                for item in root.iter():
                    queued_count = self.store.db.execute("SELECT count(*) FROM frontier WHERE site=?", (site["name"],)).fetchone()[0]
                    if queued_count > int(site.get("max_pages", 500)) * 5:
                        break
                    if (item.tag.endswith("}loc") or item.tag == "loc") and item.text:
                        loc = canonical(item.text or "")
                        if loc.endswith(".xml") and parse.urlsplit(loc).hostname == seed.hostname:
                            frontier.append(loc)
                        elif belongs(loc, site):
                            self.store.enqueue(site["name"], loc, 1, self.args.refresh_days)
            except Exception as e:
                LOG.info("Sitemap not available %s: %s", addr, e)

    def prepare_confluence_space(self, site):
        """Enumerate every public page in a Confluence space via its official API."""
        space_id = site.get("confluence_space_id")
        if not space_id:
            return
        base = parse.urlsplit(site["seeds"][0])
        origin = f"{base.scheme}://{base.netloc}"
        next_url = f"{origin}/wiki/api/v2/spaces/{space_id}/pages?limit=100"
        seen = set()
        count = 0
        while next_url:
            if next_url in seen:
                raise ValueError(f"Confluence pagination loop: {next_url}")
            seen.add(next_url)
            raw, ctype, _ = self.fetcher.get(next_url, max_bytes=4 * 1024 * 1024,
                                             extra_headers={"Accept": "application/json"})
            if "json" not in ctype.lower():
                raise ValueError(f"Confluence page listing returned {ctype}, expected JSON")
            listing = json.loads(raw)
            if not isinstance(listing.get("results"), list):
                raise ValueError("Confluence page listing has no results array")
            for page in listing["results"]:
                webui = page.get("_links", {}).get("webui", "")
                if not webui:
                    continue
                url = canonical(parse.urljoin(origin + "/wiki/", webui.lstrip("/")))
                if belongs(url, site):
                    self.store.enqueue(site["name"], url, 0, self.args.refresh_days)
                    count += 1
            more = listing.get("_links", {}).get("next")
            next_url = parse.urljoin(origin + "/wiki/", more) if more else None
        LOG.info("Confluence space %s: discovered %d public pages", space_id, count)

    def fetch_asset(self, site, base_url, raw):
        addr = canonical(parse.urljoin(base_url, raw))
        if not addr or addr in self._assets_seen:
            return
        self._assets_seen.add(addr)
        parts = parse.urlsplit(addr)
        allowed_hosts = {parse.urlsplit(u).hostname for u in site["seeds"]}
        if parts.hostname not in allowed_hosts and not host_match(parts.hostname or "", site.get("asset_hosts", [])):
            return
        ext = Path(parts.path).suffix.lower()
        if ext in SKIP_EXTENSIONS or (ext and ext not in ASSET_EXTENSIONS):
            return
        if self.asset_count >= self.args.max_assets:
            return
        if self.store.url_to_path(addr):
            return
        try:
            if not self.fetcher.allowed(addr):
                return
            data, ctype, _final = self.fetcher.get(addr)
            if "text/html" in ctype.lower():
                return  # do not save login screens as images
            if not (ctype.startswith(("image/", "text/css", "font/", "application/pdf")) or
                    ext in ASSET_EXTENSIONS or "font" in ctype):
                return
            if ext == ".pdf" and not self.args.pdf:
                return
            path = local_path(self.store.root, site["name"], addr, True, ctype)
            dest = self.store.root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if "text/css" in ctype.lower() or ext == ".css":
                css = to_text(data, ctype)
                refs = re.findall(r'url\(\s*["\']?([^"\')]+)', css, flags=re.I)
                for ref in refs[:150]:
                    if not ref.startswith(("data:", "#")):
                        self.fetch_asset(site, addr, ref)
                def css_rewrite(m):
                    q = m.group(1) or ""
                    original = m.group(2).strip()
                    absolute = canonical(parse.urljoin(addr, original))
                    existing = self.store.url_to_path(absolute) if absolute else None
                    linked = (os.path.relpath(existing, dest.parent).replace(os.sep, "/")
                              if existing else absolute or original)
                    return f"url({q}{linked}{q})"
                css = re.sub(r'url\(\s*(["\']?)([^"\')]+)\1\s*\)', css_rewrite, css, flags=re.I)
                data = css.encode("utf-8")
            dest.write_bytes(data)
            self.store.remember("asset", site["name"], addr, Path(parts.path).name, path, data)
            self.asset_count += 1
            self.summary["assets"] += 1
        except Exception as e:
            LOG.warning("Asset skipped %s: %s", addr, e)

    def crawl(self):
        try:
            for site in self.config["sites"]:
                if self.args.site and site["name"] not in self.args.site:
                    continue
                LOG.info("Site: %s", site["name"])
                self._assets_seen = set()
                self.repair_frontier_aliases(site)
                # Upgrade failed binary links left in the frontier by earlier versions.
                # A missing asset is marked skipped below, so it is tried only once.
                for old in self.store.db.execute(
                        "SELECT url FROM frontier WHERE site=? AND state='failed'", (site["name"],)).fetchall():
                    if is_asset_url(old["url"]):
                        self.store.mark(site["name"], old["url"], "queued")
                for seed in site["seeds"]:
                    url = canonical(seed)
                    if belongs(url, site):
                        self.store.enqueue(site["name"], url, 0, self.args.refresh_days)
                self.prepare_confluence_space(site)
                self.prepare_sitemap(site)
                run_count = 0
                limit = min(int(site.get("max_pages", 500)), self.args.max_pages)
                while run_count < limit:
                    row = self.store.next_url(site["name"])
                    if not row:
                        break
                    url, depth = row["url"], row["depth"]
                    if depth > self.args.max_depth:
                        self.store.mark(site["name"], url, "skipped")
                        continue
                    if not belongs(url, site):
                        self.store.mark(site["name"], url, "skipped")
                        continue
                    # Older runs may already have queued images and diagrams as pages.
                    # Archive them as assets so resuming does not fail on their MIME type.
                    if is_asset_url(url):
                        self.fetch_asset(site, url, url)
                        saved = bool(self.store.url_to_path(url))
                        self.store.mark(site["name"], url, "done" if saved else "skipped")
                        if not saved:
                            self.store.db.execute(
                                "UPDATE items SET kind='asset',status='skipped' WHERE url=? AND status='failed'", (url,))
                            self.store.db.commit()
                        continue
                    if Path(parse.urlsplit(url).path).suffix.lower() in SKIP_EXTENSIONS:
                        self.store.mark(site["name"], url, "skipped")
                        continue
                    try:
                        if not self.fetcher.allowed(url):
                            raise PermissionError("robots.txt disallows page")
                        if site.get("browser") and not self.args.no_browser:
                            self.fetcher._pace(parse.urlsplit(url).netloc)
                            doc, code, final_url = self.browser.render(url)
                            if not belongs(canonical(final_url), site):
                                raise PermissionError(f"Browser redirected outside crawl scope: {final_url}")
                            if code >= 400:
                                raise ValueError(f"Browser HTTP {code}")
                            if "JavaScript must be enabled" in doc and len(doc) < 15_000:
                                LOG.warning("Page may have failed to render: %s", url)
                        else:
                            raw, ctype, final = self.fetcher.get(url, max_bytes=8 * 1024 * 1024)
                            doc = to_text(raw, ctype)
                        markdown = bool(site.get("markdown") and (
                            parse.urlsplit(url).path.endswith((".md", ".txt")) or
                            "text/markdown" in ctype.lower() if not (site.get("browser") and not self.args.no_browser) else False))
                        # GitBook's llms.txt offers a full table of contents and individual .md pages.
                        if markdown:
                            title_match = re.search(r"^#\s+(.+)$", doc, re.M)
                            title = (title_match.group(1).strip() if title_match else
                                     Path(parse.urlsplit(url).path).name)
                            text = doc
                            page_links = markdown_links(doc)
                            assets = markdown_assets(doc)
                        else:
                            if not (site.get("browser") and not self.args.no_browser) and not (
                                    "html" in ctype.lower() or b"<html" in raw[:4096].lower()):
                                raise ValueError(f"not HTML or configured Markdown: {ctype}")
                            parsed = parse_html(doc)
                            title = parsed.title.strip() or Path(parse.urlsplit(url).path).name or url
                            text = parsed.result()
                            page_links = parsed.links
                            assets = parsed.assets
                        if not text or ("JavaScript must be enabled" in text and len(text) < 200):
                            raise ValueError("Content missing; use Playwright browser rendering for this site")
                        issue = validate_document(doc, ctype if not site.get("browser") or self.args.no_browser else "text/html",
                                                  title, text, site)
                        if issue:
                            raise ValueError(issue)
                        path = local_path(self.store.root, site["name"], url, markdown=markdown)
                        dest = self.store.root / path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        # Preserve original HTML for later rewriting when URL targets become available.
                        raw_dest = dest.parent.parent / "raw" / dest.name
                        raw_dest.parent.mkdir(parents=True, exist_ok=True)
                        raw_dest.write_text(doc, encoding="utf-8")
                        dest.write_text(doc, encoding="utf-8")
                        if site.get("reader_snapshot"):
                            dest.with_suffix(".txt").write_text(
                                f"{title}\nSource: {url}\nFetched: {utcnow()}\n\n{text}\n",
                                encoding="utf-8")
                        self.store.remember("page", site["name"], url, title, path, text)
                        for rawlink in ([] if site.get("seed_only") else page_links):
                            absolute = canonical(parse.urljoin(url, rawlink))
                            if absolute:
                                if is_asset_url(absolute):
                                    self.fetch_asset(site, url, absolute)
                                elif belongs(absolute, site):
                                    self.store.enqueue(site["name"], absolute, depth + 1, self.args.refresh_days)
                        for asset in assets:
                            self.fetch_asset(site, url, asset)
                        self.store.mark(site["name"], url, "done")
                        self.summary["pages"] += 1
                        LOG.info("[%s %d/%d] %s", site["name"], run_count + 1, limit, title[:90])
                    except Exception as e:
                        blocked = isinstance(e, PermissionError) and "robots.txt" in str(e)
                        status = "blocked" if blocked else "failed"
                        LOG.warning("Page %s %s: %s", status, url, e)
                        self.store.mark(site["name"], url, status)
                        self.store.remember("page", site["name"], url, url, None, "", status, str(e))
                        self.summary[status] += 1
                        if isinstance(e, error.HTTPError) and e.code in (429, 503):
                            LOG.warning("Stopping site %s after HTTP %d; do not evade throttling.", site["name"], e.code)
                            break
                    run_count += 1
        finally:
            self.browser.close()


    def discover(self):
        """Search the official public GitHub Search API (no GitHub web scraping).

        Writes a reviewable repo list. Does not clone anything in this method.
        """
        queries = [
            'witcher3 modding in:name,description',
            'witcherscript in:name,description',
            'witcher 3 redkit in:name,description',
            'the witcher 3 mods in:name,description',
        ]
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = "Bearer " + token
        gathered = {}
        for query in queries:
            u = "https://api.github.com/search/repositories?" + parse.urlencode(
                {"q": query, "per_page": 50, "sort": "stars", "order": "desc"})
            try:
                raw, _content_type, _end = self.fetcher.get(u, max_bytes=4 * 1024 * 1024,
                                                            extra_headers=headers)
                obj = json.loads(raw)
                for info in obj.get("items", []):
                    full = info.get("full_name", "")
                    if not re.fullmatch(r"[\w.-]+/[\w.-]+", full):
                        continue
                    if info.get("fork") or int(info.get("size") or 0) > self.args.max_repo_mb * 1024:
                        continue
                    evidence = (full + " " + (info.get("description") or "")).lower()
                    if not any(kw in evidence for kw in ("witcher", "redkit", "witcherscript", "w3mod")):
                        continue
                    gathered[full] = {
                        "repository": full,
                        "description": info.get("description") or "",
                        "stars": info.get("stargazers_count", 0),
                        "kilobytes": info.get("size", 0),
                        "updated_at": info.get("updated_at"),
                        "web_url": info.get("html_url")
                    }
                LOG.info("GitHub discovery '%s': %d candidates so far", query, len(gathered))
            except Exception as e:
                # Public search is rate-limited; never bypass it.
                LOG.warning("GitHub API query blocked or failed: %s (%s)", query, e)
        entries = sorted(gathered.values(), key=lambda r: (-r["stars"], r["repository"]))
        entries = entries[:self.args.max_discovered_repos]
        if not entries and (self.store.root / "discovered_repos.json").is_file():
            LOG.warning("Discovery found no repositories. Preserving prior discovery list.")
            return
        (self.store.root / "repo-discovery.json").write_text(
            json.dumps({"created_at": utcnow(), "repositories": entries}, indent=2), encoding="utf-8")
        (self.store.root / "discovered_repos.json").write_text(
            json.dumps([r["repository"] for r in entries], indent=2), encoding="utf-8")
        self.summary["discovered_repos"] = len(entries)
        LOG.info("Discovered %d repositories; review %s", len(entries),
                 self.store.root / "repo-discovery.json")

    def _git(self, target, *args, env=None):
        """Never run Git through a shell; return the completed process for diagnosis."""
        return subprocess.run(["git", "-C", str(target), *args], env=env,
                              capture_output=True, text=True,
                              timeout=self.args.git_timeout)

    def _update_repository(self, target, env):
        """Update owned checkout without trusting stale master/main upstream configs.

        A clean checkout can fast-forward from the *remote default HEAD*, even if
        the configured local tracking branch has been deleted or renamed. Local
        edits and diverged histories are intentionally left untouched.
        """
        original = self._git(target, "rev-parse", "--verify", "HEAD", env=env)
        before = original.stdout.strip() if original.returncode == 0 else None
        dirty = self._git(target, "status", "--porcelain", env=env)
        if dirty.returncode or dirty.stdout.strip():
            LOG.warning("Skipping remote update for locally changed checkout %s", target)
            return before, before, True
        fetched = self._git(target, "fetch", "--depth=30", "origin", "HEAD", env=env)
        if fetched.returncode:
            raise RuntimeError((fetched.stderr or fetched.stdout).strip()[-800:])
        # Fast forward only; a broken tracking ref (origin/master) never enters this flow.
        if before:
            merged = self._git(target, "merge", "--ff-only", "FETCH_HEAD", env=env)
        else:
            merged = self._git(target, "checkout", "-B", "archive", "FETCH_HEAD", env=env)
        if merged.returncode:
            raise RuntimeError("Cannot fast-forward remote HEAD (checkout preserved): " +
                               (merged.stderr or merged.stdout).strip()[-800:])
        after = self._git(target, "rev-parse", "--verify", "HEAD", env=env)
        return before, after.stdout.strip(), False

    def repositories(self):
        if not shutil.which("git"):
            raise RuntimeError("git executable was not found in PATH")
        configured = list(self.config.get("repositories", []))
        discovered = self.store.root / "discovered_repos.json"
        if discovered.is_file():
            configured.extend(json.loads(discovered.read_text(encoding="utf-8")))
        requested = set(self.args.repo or ())
        if requested:
            configured = [repo for repo in dict.fromkeys(configured) if repo in requested]
            missing = requested.difference(configured)
            if missing:
                LOG.warning("Requested repositories not configured/discovered: %s", ", ".join(sorted(missing)))
        for repo in dict.fromkeys(configured):
            if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
                LOG.warning("Invalid GitHub repository identifier: %s", repo)
                continue
            owner, name = repo.split("/", 1)
            target = self.store.root / "github" / owner / name
            target.parent.mkdir(parents=True, exist_ok=True)
            repo_url = f"https://github.com/{repo}.git"
            env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}
            LOG.info("GitHub: %s", repo)
            try:
                if (target / ".git").is_dir():
                    before, after, modified = self._update_repository(target, env)
                elif target.exists():
                    LOG.warning("Skipping non-Git directory: %s", target)
                    self.summary["git_failed"] += 1
                    continue
                else:
                    cmd = ["git", "clone", "--depth", "1", "--single-branch", "--", repo_url, str(target)]
                    ran = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=self.args.git_timeout)
                    if ran.returncode:
                        raise RuntimeError((ran.stderr or ran.stdout).strip()[-800:])
                    before, after, modified = None, self._git(target, "rev-parse", "HEAD", env=env).stdout.strip(), False
                self.summary["repos"] += 1
                known = self.store.db.execute("SELECT head,indexed_files FROM repo_state WHERE repo=?", (repo,)).fetchone()
                existing = self.store.db.execute("SELECT count(*) FROM items WHERE source=? AND status='ok'", ("github:" + repo,)).fetchone()[0]
                # Existing v2 source files can be bootstrapped without re-reading
                # thousands of files if Git reports no change in HEAD.
                unchanged = (known and known["head"] == after and existing) or (
                    not known and before and before == after and existing)
                if unchanged and not modified and not self.args.force_reindex:
                    self.store.db.execute("INSERT INTO repo_state(repo,head,indexed_files,indexed_at) VALUES(?,?,?,?) "
                                          "ON CONFLICT(repo) DO UPDATE SET head=excluded.head,indexed_files=excluded.indexed_files,indexed_at=excluded.indexed_at",
                                          (repo, after, existing, utcnow()))
                    self.store.db.commit()
                    self.summary["repos_unchanged"] += 1
                    LOG.info("Unchanged %s: retained %d indexed files", repo, existing)
                    continue
                # Do not remove old rows before a successful new pass. The content
                # of a changed file is replaced by Store.remember's ON CONFLICT.
                indexed_files = self.index_repo(repo, target)
                self.store.db.execute("INSERT INTO repo_state(repo,head,indexed_files,indexed_at) VALUES(?,?,?,?) "
                                      "ON CONFLICT(repo) DO UPDATE SET head=excluded.head,indexed_files=excluded.indexed_files,indexed_at=excluded.indexed_at",
                                      (repo, after, indexed_files, utcnow()))
                self.store.db.commit()
            except (subprocess.TimeoutExpired, OSError, RuntimeError) as e:
                LOG.warning("Git operation failed %s: %s", repo, e)
                self.summary["git_failed"] += 1

    def index_repo(self, repo, directory):
        indexed = 0
        for base, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "venv", "target", "dist"}
                       and not (Path(base) / d).is_symlink()]
            for name in files:
                path = Path(base) / name
                if path.is_symlink():
                    continue
                if path.suffix.lower() not in TEXT_EXTENSIONS and name.lower() not in {"license", "makefile", "readme", "changelog"}:
                    continue
                try:
                    if path.stat().st_size > 1_000_000:
                        continue
                    data = path.read_bytes()
                    if b"\x00" in data[:4096]:
                        continue
                    content = to_text(data)
                    relative = path.relative_to(self.store.root)
                    rel_repo = path.relative_to(directory).as_posix()
                    self.store.remember("source", f"github:{repo}",
                                        f"https://github.com/{repo}/blob/HEAD/{parse.quote(rel_repo)}",
                                        rel_repo, relative, content)
                    indexed += 1
                except (OSError, UnicodeError):
                    continue
        LOG.info("Indexed %s: %d source/text files", repo, indexed)
        self.summary["indexed_files"] += indexed
        return indexed


def markdown_links(markdown):
    return [u for u in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)', markdown) if u]


def markdown_assets(markdown):
    return [u for u in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', markdown) if u]


def rewrite_markdown(markdown, url, path, known):
    def change(m):
        raw = m.group(2)
        absolute = canonical(parse.urljoin(url, raw))
        destination = known.get(absolute)
        if not destination:
            return m.group(0)
        link = os.path.relpath(destination, path.parent).replace(os.sep, "/")
        fragment = parse.urlsplit(parse.urljoin(url, raw)).fragment
        return m.group(1) + link + ("#" + fragment if fragment else "") + m.group(3)
    return re.sub(r'(\[[^\]]*\]\()([^)]+)(\))', change, markdown)


def relink_library(store):
    rows = store.db.execute("SELECT url,path FROM items WHERE status='ok' AND path IS NOT NULL").fetchall()
    known = {r["url"]: store.root / r["path"] for r in rows}
    for item in store.db.execute("SELECT url,path FROM items WHERE kind='page' AND status='ok'"):
        path = store.root / item["path"]
        if path.is_file():
            raw_path = path.parent.parent / "raw" / path.name
            raw = (raw_path if raw_path.exists() else path).read_text(encoding="utf-8")
            if path.suffix.lower() in (".md", ".txt"):
                result = rewrite_markdown(raw, item["url"], path, known)
            else:
                result = rewrite_html(raw, item["url"], path, known)
            if result != raw:
                path.write_text(result, encoding="utf-8")


def audit_library(store, config: dict, repair: bool = False):
    """Find existing bad crawler snapshots; optionally hide/requeue without deleting files."""
    sites = {site["name"]: site for site in config.get("sites", [])}
    counts = defaultdict(int)
    for row in store.db.execute("""SELECT id,url,source,title,path FROM items
                                    WHERE kind='page' AND status='ok'""").fetchall():
        site = sites.get(row["source"], {})
        path = store.root / row["path"] if row["path"] else None
        if not path or not path.exists():
            issue = "stored page file missing"
        else:
            original = path.parent.parent / "raw" / path.name
            doc = (original if original.is_file() else path).read_text(encoding="utf-8")
            if path.suffix.lower() in (".txt", ".md"):
                title = row["title"] or ""
                text = doc
                ctype = "text/markdown"
            else:
                parsed = parse_html(doc)
                title = parsed.title.strip() or row["title"] or ""
                text = parsed.result()
                ctype = "text/html"
            issue = validate_document(doc, ctype, title, text, site)
        if issue:
            counts[row["source"]] += 1
            print(f"SUSPECT [{row['source']}] {row['url']}: {issue}")
            if repair:
                store.db.execute("UPDATE items SET status='suspect',error=? WHERE id=?", (issue, row["id"]))
                store.db.execute("DELETE FROM item_search WHERE rowid=?", (row["id"],))
                store.db.execute("UPDATE frontier SET state='queued' WHERE site=? AND url=?",
                                 (row["source"], row["url"]))
    if repair:
        store.db.commit()
    print(f"Audit: {sum(counts.values())} suspect pages" + (" quarantined and requeued" if repair else " (read-only)"))
    return sum(counts.values())


def import_local_documents(store, source_dir: Path):
    """Preserve a manually exported webpage tree, then index readable documents.

    Relative images, CSS, and links retain their original directory layout under
    imports/manual/; unlike a network crawler, this only reads user-supplied files.
    """
    source_dir = source_dir.expanduser().resolve(strict=True)
    if not source_dir.is_dir():
        raise ValueError("--input must point to a directory of files")
    readable = {".md", ".mdx", ".txt", ".html", ".htm", ".rst", ".pdf"}
    related = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg",
               ".css", ".woff", ".woff2", ".ico", ".bmp"}
    imported = 0
    assets = 0
    for parent, dirs, files in os.walk(source_dir, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(parent) / d).is_symlink()]
        for filename in files:
            src = Path(parent) / filename
            ext = src.suffix.lower()
            if src.is_symlink() or ext not in readable | related:
                continue
            if src.stat().st_size > 40 * 1024 * 1024:
                LOG.warning("Manual import too large: %s", src)
                continue
            rel = src.relative_to(source_dir)
            data = src.read_bytes()
            target = Path("imports/manual") / rel
            dest = store.root / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            if ext in related:
                dest.write_bytes(data)
                assets += 1
                continue
            if ext == ".pdf":
                if not shutil.which("pdftotext"):
                    LOG.warning("pdftotext unavailable; PDF copied without full-text index: %s", src)
                    content = ""
                else:
                    try:
                        run = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data,
                                             capture_output=True, timeout=45)
                        if run.returncode:
                            LOG.warning("PDF text conversion failed: %s", src)
                        content = to_text(run.stdout) if not run.returncode else ""
                    except subprocess.TimeoutExpired:
                        content = ""
            elif ext in (".html", ".htm"):
                content = parse_html(to_text(data)).result()
            else:
                content = to_text(data)
            sha = bytes_sha(rel.as_posix().encode())[:16]
            url = "local-import://" + sha + "/" + parse.quote(rel.as_posix(), safe="/")
            dest.write_bytes(data)
            store.remember("page" if content.strip() else "asset", "manual-import", url,
                           src.stem.replace("-", " "), target, content if content.strip() else data)
            imported += 1
    print(f"Imported {imported} documents and {assets} supporting assets from {source_dir}")
    return imported


def make_index(store):
    rows = store.db.execute("""SELECT kind,source,title,url,path,fetched_at FROM items
                               WHERE status='ok' AND kind IN ('page','source')
                               ORDER BY source,title""").fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["source"]].append(row)
    sections = []
    for source, items in grouped.items():
        links = []
        for i in items:
            if not i["path"]:
                continue
            label = escape(i["title"] or i["url"])
            destination = parse.quote(i["path"], safe="/._-~")
            links.append(f'<li><a href="{destination}">{label}</a> '
                         f'<small><a href="{escape(i["url"], quote=True)}">source ↗</a></small></li>')
        sections.append(f'<section><h2>{escape(source)} <small>({len(links)})</small></h2><ul>{"".join(links)}</ul></section>')
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Witcher 3 Offline Modding Library</title>
    <style>body{background:#10151a;color:#dfe7e9;font:16px system-ui;margin:2em auto;max-width:1050px;padding:0 1em}
    a{color:#82caff}a:hover{color:#bfe5ff}h1{color:#f2e1b8}section{border-top:1px solid #34414a;padding:.8em 0}
    input{width:100%;box-sizing:border-box;background:#1e2a31;color:white;border:1px solid #57717f;border-radius:7px;
    font:inherit;padding:.9em}ul{line-height:1.75}small{color:#9aabb5}li{overflow-wrap:anywhere}</style></head>
    <body><h1>The Witcher 3 — Offline Modding Library</h1><p>Saved guides and public GitHub source files.
    <a href="guides.html">Browse guides only ↗</a>. Open <code>library.sqlite3</code> with the <code>search</code> command for full-text search.</p>
    <input id="q" placeholder="Filter titles and filenames..." aria-label="Filter titles">
    """ + "".join(sections) + """<script>
    const q=document.querySelector('#q');q.addEventListener('input',()=>{const s=q.value.toLowerCase();
    document.querySelectorAll('li').forEach(e=>e.hidden=!e.textContent.toLowerCase().includes(s));
    document.querySelectorAll('section').forEach(e=>e.hidden=![...e.querySelectorAll('li')].some(x=>!x.hidden));});
    </script></body></html>"""
    (store.root / "index.html").write_text(page, encoding="utf-8")
    # A documentation-focused entry point; the full catalog is dominated by code files.
    guide_rows = [r for r in rows if r["kind"] == "page" or (
        r["kind"] == "source" and r["path"] and
        Path(r["path"]).suffix.lower() in {".md", ".mdx", ".rst", ".txt"})]
    guide_groups = defaultdict(list)
    for row in guide_rows:
        guide_groups[row["source"]].append(row)
    guide_sections = []
    for source, entries in guide_groups.items():
        links = ""
        for i in entries:
            if not i["path"]:
                continue
            path = Path(i["path"])
            reader = path.with_suffix(".txt")
            reader_link = (f' <small><a href="{parse.quote(str(reader), safe="/._-~")}">plain text</a></small>'
                           if (store.root / reader).is_file() else "")
            links += (f'<li><a href="{parse.quote(str(path), safe="/._-~")}">'
                      f'{escape(i["title"] or i["url"])}</a>{reader_link}</li>')
        guide_sections.append(f'<section><h2>{escape(source)} ({len(entries)})</h2><ul>{links}</ul></section>')
    guides_html = ("<!doctype html><html><head><meta charset='utf-8'>"
                   "<title>Witcher 3 Modding Guides</title>"
                   "<style>body{max-width:1100px;margin:2em auto;padding:0 1em;"
                   "background:#10151a;color:#e1e8eb;font:16px system-ui}"
                   "a{color:#91d0ff}li{padding:3px}</style></head><body>"
                   "<h1>Documentation &amp; Markdown Guides</h1>"
                   "<p><a href='index.html'>Full library (including code)</a></p>"
                   + "".join(guide_sections) + "</body></html>")
    (store.root / "guides.html").write_text(guides_html, encoding="utf-8")
    manifest = {"generated_at": utcnow(), "count": len(rows), "sources":
                {key: len(values) for key, values in grouped.items()}}
    (store.root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def search(store, words, limit):
    try:
        rows = store.db.execute("""SELECT items.title,items.source,items.path,items.url,
              snippet(item_search,1,'[',']',' … ',16) AS excerpt
              FROM item_search JOIN items ON items.id=item_search.rowid
              WHERE item_search MATCH ? ORDER BY rank LIMIT ?""", (words, limit)).fetchall()
    except sqlite3.OperationalError as e:
        raise ValueError(f"Bad FTS5 query ({e}); try single words, e.g. 'quest' or 'script'") from e
    for row in rows:
        print(f"\n{row['title']} — {row['source']}\n  file: {store.root / row['path']}\n  url:  {row['url']}\n  {row['excerpt']}")
    print(f"\n{len(rows)} matches (limit {limit})")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("command", choices=["all", "crawl", "repos", "discover", "search", "index", "serve", "stats", "audit", "import"])
    parser.add_argument("terms", nargs="*", help="FTS5 query for search command")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("sources.json"))
    parser.add_argument("--output", type=Path, default=Path("witcher3-offline-library"))
    parser.add_argument("--delay", type=float, default=2.0, help="Minimum seconds between requests per host")
    parser.add_argument("--max-pages", type=int, default=3000, help="Maximum fetched pages per site per run")
    parser.add_argument("--max-depth", type=int, default=16, help="Maximum number of links from a seed")
    parser.add_argument("--max-assets", type=int, default=6000, help="Maximum new assets per run")
    parser.add_argument("--max-asset-mb", type=float, default=25, help="Maximum size of a downloaded asset in MiB")
    parser.add_argument("--refresh-days", type=int, default=0, help="Re-crawl saved pages older than N days; zero never refreshes")
    parser.add_argument("--retry-failed", action="store_true", help="Retry previously failed URLs (not robots.txt-blocked URLs)")
    parser.add_argument("--site", action="append", metavar="NAME", help="Crawl only a named site (repeatable)")
    parser.add_argument("--repo", action="append", metavar="OWNER/NAME", help="Clone/update only a named configured repository (repeatable)")
    parser.add_argument("--force-reindex", action="store_true", help="Index all text files again even if the Git commit is unchanged")
    parser.add_argument("--repair", action="store_true", help="With audit: quarantine bad snapshots, clear their search entries, requeue")
    parser.add_argument("--input", type=Path, help="With import: directory of locally exported HTML/Markdown/PDF/text guides")
    parser.add_argument("--no-browser", action="store_true", help="Disable optional Playwright rendering")
    parser.add_argument("--no-sitemaps", action="store_true", help="Disable optional sitemap discovery")
    parser.add_argument("--no-pdf", dest="pdf", action="store_false", help="Do not archive linked PDF files")
    parser.add_argument("--git-timeout", type=int, default=900)
    parser.add_argument("--discover", action="store_true", help="Discover additional public GitHub repositories before cloning (all command)")
    parser.add_argument("--max-discovered-repos", type=int, default=60, help="Maximum extra GitHub repositories from discovery")
    parser.add_argument("--max-repo-mb", type=int, default=150, help="Ignore repositories larger than this GitHub-reported MiB size")
    parser.add_argument("--limit", type=int, default=30, help="Search result count")
    parser.add_argument("--port", type=int, default=8765, help="Local server port")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = args.output.expanduser().resolve()
    store = Store(root)
    try:
        if args.command == "search":
            if not args.terms:
                parser.error("search requires a query")
            search(store, " ".join(args.terms), args.limit)
        elif args.command == "stats":
            for row in store.db.execute("SELECT kind,status,count(*) n FROM items GROUP BY kind,status ORDER BY kind,status"):
                print(f"{row['kind']} / {row['status']}: {row['n']}")
            for row in store.db.execute("SELECT site,state,count(*) n FROM frontier GROUP BY site,state ORDER BY site,state"):
                print(f"{row['site']} / {row['state']}: {row['n']}")
        elif args.command == "serve":
            handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(root), **kw)
            print(f"Serving {root} at http://127.0.0.1:{args.port}/")
            with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
                server.serve_forever()
        else:
            config = json.loads(args.config.read_text(encoding="utf-8"))
            if args.command == "audit":
                audit_library(store, config, repair=args.repair)
                if args.repair:
                    make_index(store)
                return
            if args.command == "import":
                if not args.input:
                    parser.error("import requires --input DIR")
                import_local_documents(store, args.input)
                make_index(store)
                return
            if args.retry_failed:
                store.db.execute("UPDATE frontier SET state='queued' WHERE state='failed'")
                store.db.commit()
            job = Archiver(store, config, args)
            if args.command == "discover" or (args.command == "all" and args.discover):
                job.discover()
            if args.command in ("all", "crawl"):
                job.crawl()
                relink_library(store)
            if args.command in ("all", "repos"):
                job.repositories()
            if args.command in ("all", "crawl", "repos", "discover", "index"):
                make_index(store)
                print("\nSaved offline library:", root)
                print("Start page:", root / "index.html")
                print("Summary:", dict(job.summary))
    finally:
        store.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Finished pages and queue were saved; run the same command to resume.", file=sys.stderr)
        sys.exit(130)
