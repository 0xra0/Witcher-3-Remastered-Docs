#!/usr/bin/env python3
"""Offline tests; run: python -m unittest -v test_scraper.py"""
import contextlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

import witcher_archive as w


class SilentHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class ScraperTest(unittest.TestCase):
    def test_redkit_published_links_resolve_to_existing_community_pages(self):
        root = "https://redkit-resources.github.io/docs/en/"
        # Verified against the live site and its mkdocs.en.yml navigation.
        self.assertEqual(w.canonical(root + "en/unofficial_docs/base/"),
                         root + "en/unofficial_docs/base/")
        self.assertEqual(w.canonical(root + "en/unofficial_docs/base/behavior/behavior/"),
                         root + "en/unofficial_docs/base/behavior/behavior/")
        self.assertEqual(w.canonical(root + "en/unofficial_docs/behavior/behavior/"),
                         root + "en/unofficial_docs/base/behavior/behavior/")
        self.assertEqual(w.canonical(root + "en/base/behavior/behavior/"),
                         root + "en/unofficial_docs/base/behavior/behavior/")
        self.assertEqual(w.canonical(root + "unofficial_docs/behavior/behavior/"),
                         root + "en/unofficial_docs/base/behavior/behavior/")
        self.assertEqual(w.canonical(root + "unofficial_docs/create_animation_for_npc/"),
                         root + "en/unofficial_docs/guides/create_animation_for_npc/")
        self.assertEqual(w.canonical(root + "base/loading_screens/"),
                         root + "unofficial_docs/base/loading_screens/")
        self.assertEqual(w.canonical(root + "unofficial_docs/guides/create_animation_for_npc/"),
                         root + "unofficial_docs/guides/create_animation_for_npc/")
        self.assertEqual(w.canonical(root + "unofficial_docs/references/editors/asset_browser.md"),
                         root + "unofficial_docs/references/editors/asset_browser/")
        self.assertEqual(w.canonical(root + "en/assets/images/example.webp"),
                         root + "assets/images/example.webp")
        self.assertEqual(w.canonical(root + "en/contribute/"), root + "en/contribute/")
        page = Path("/tmp/offline/pages/index.html")
        known = {root + "en/unofficial_docs/base/": Path("/tmp/offline/pages/guide.html")}
        html = w.rewrite_html('<a href="en/unofficial_docs/base/">Basics</a>', root, page, known)
        self.assertIn('href="guide.html"', html)

    def test_previous_failed_redkit_link_is_migrated(self):
        from types import SimpleNamespace
        root = "https://redkit-resources.github.io/docs/en/"
        bad = root + "unofficial_docs/behavior/behavior/"
        fixed = root + "en/unofficial_docs/base/behavior/behavior/"
        store = w.Store(self.output)
        try:
            store.enqueue("redkit-open-docs", bad, 2, 0)
            store.mark("redkit-open-docs", bad, "failed")
            store.remember("page", "redkit-open-docs", bad, bad, None, "", "failed", "HTTP 404")
            job = w.Archiver(store, {}, SimpleNamespace(delay=0.1, max_asset_mb=2, refresh_days=0))
            job.repair_frontier_aliases({"name": "redkit-open-docs", "seeds": [root], "paths": ["/docs/en/"]})
            self.assertEqual(store.db.execute("SELECT state FROM frontier WHERE url=?", (bad,)).fetchone()[0], "skipped")
            self.assertEqual(store.db.execute("SELECT state FROM frontier WHERE url=?", (fixed,)).fetchone()[0], "queued")
            self.assertEqual(store.db.execute("SELECT status FROM items WHERE url=?", (bad,)).fetchone()[0], "skipped")
        finally:
            store.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.web = self.root / "web"
        (self.web / "docs").mkdir(parents=True)
        (self.web / "robots.txt").write_text("User-agent: *\nDisallow: /docs/private.html\n")
        (self.web / "docs" / "index.html").write_text('''<!doctype html><title>Index page</title>
          <main><h1>Welcome modding</h1><a href="guide.html">Script guide</a>
          <a href="private.html">Restricted</a><a href="/outside">Outside</a>
          <a href="diagram.drawio">Diagram source</a><a href="photo.webp">Photo</a>
          <link rel="stylesheet" href="style.css"><img src="icon.png" alt="Icon"></main>''')
        (self.web / "docs" / "guide.html").write_text('''<!doctype html><title>Witcher script guide</title>
          <main><h1>Redkit Quest Tutorial</h1><p>Write a WitcherScript quest function and test it.</p>
          <a href="index.html">Back</a></main>''')
        (self.web / "docs" / "private.html").write_text("<html>private</html>")
        (self.web / "docs" / "style.css").write_text("body { color: red; }")
        (self.web / "docs" / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\nFAKE-IMAGE")
        (self.web / "docs" / "photo.webp").write_bytes(b"RIFFfakeWEBP")
        (self.web / "docs" / "diagram.drawio").write_text('<mxfile><diagram>Guide</diagram></mxfile>')
        handler = partial(SilentHandler, directory=str(self.web))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.config = self.root / "sources.json"
        self.config.write_text(json.dumps({"sites": [{
            "name": "test-docs", "seeds": [self.base + "/docs/index.html"],
            "paths": ["/docs/"], "asset_hosts": [], "max_pages": 20,
            "sitemap": False, "browser": False}], "repositories": []}))
        self.output = self.root / "library"

    def command(self, *arguments):
        return w.main([*arguments, "--output", str(self.output), "--config", str(self.config),
                       "--delay", "0.1", "--no-browser"])

    def test_missing_robots_file_allows_public_assets(self):
        (self.web / 'robots.txt').unlink()
        fetcher = w.Fetcher(0.1, 2)
        self.assertTrue(fetcher.allowed(self.base + '/docs/icon.png'))

    def test_crawl_resume_offline_rewrite_and_search(self):
        self.command("crawl")
        store = w.Store(self.output)
        try:
            pages = store.db.execute("SELECT url,path FROM items WHERE kind='page' AND status='ok'").fetchall()
            self.assertEqual(len(pages), 2)
            self.assertTrue((self.output / "guides.html").is_file())
            self.assertEqual(store.db.execute("SELECT count(*) FROM items WHERE status='blocked'").fetchone()[0], 1)
            for row in pages:
                self.assertTrue((self.output / row["path"]).is_file())
            self.assertEqual(store.db.execute("SELECT count(*) FROM items WHERE kind='asset'").fetchone()[0], 4)
            self.assertFalse(store.db.execute("SELECT 1 FROM frontier WHERE url LIKE '%.webp' OR url LIKE '%.drawio'").fetchone())
            index = next(row for row in pages if row["url"].endswith("index.html"))
            html = (self.output / index["path"]).read_text()
            self.assertRegex(html, r'href="[a-f0-9]{20}\.html"')
            self.assertRegex(html, r'src="\.\./assets/[a-f0-9]{20}\.png"')
            self.assertRegex(html, r'href="\.\./assets/[a-f0-9]{20}\.drawio"')
            self.assertIn(self.base + "/docs/private.html", html)  # not downloaded because robots.txt
            self.assertFalse(store.db.execute("SELECT 1 FROM items WHERE url LIKE '%private.html' AND status='ok'").fetchone())
            rows = store.db.execute("SELECT count(*) FROM item_search").fetchone()[0]
            self.assertEqual(rows, 2)
        finally:
            store.close()
        # Re-indexing should use original HTML snapshot, not change already-local relative links.
        self.command("index")
        store = w.Store(self.output)
        try:
            w.relink_library(store)
            h2 = (self.output / index["path"]).read_text()
            self.assertIn('src="../assets/', h2)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                w.search(store, "Quest", 10)
            self.assertIn("Witcher script guide", output.getvalue())
        finally:
            store.close()
        self.command("crawl")
        store = w.Store(self.output)
        try:
            self.assertEqual(store.db.execute("SELECT count(*) FROM items WHERE kind='page' AND status='ok'").fetchone()[0], 2)
        finally:
            store.close()

    def test_resumes_previously_failed_asset_as_asset(self):
        asset = self.base + "/docs/diagram.drawio"
        store = w.Store(self.output)
        try:
            store.enqueue("test-docs", asset, 1, 0)
            store.mark("test-docs", asset, "failed")
            store.remember("page", "test-docs", asset, asset, None, "", "failed", "not HTML")
        finally:
            store.close()
        self.command("crawl")
        store = w.Store(self.output)
        try:
            self.assertEqual(store.db.execute("SELECT state FROM frontier WHERE url=?", (asset,)).fetchone()[0], "done")
            row = store.db.execute("SELECT kind,status,path FROM items WHERE url=?", (asset,)).fetchone()
            self.assertEqual((row["kind"], row["status"]), ("asset", "ok"))
            self.assertTrue((self.output / row["path"]).is_file())
        finally:
            store.close()

    def test_markdown_llms_index_and_links(self):
        (self.web / "docs" / "llms.txt").write_text(
            "# Modding Manual\n- [Quests](guide.md): Quest creation\n")
        (self.web / "docs" / "guide.md").write_text(
            "# Quest Creation\nLearn REDkit nodes.\n![Node graph](icon.png)\n")
        data = json.loads(self.config.read_text())
        data["sites"][0]["seeds"] = [self.base + "/docs/llms.txt"]
        data["sites"][0]["markdown"] = True
        self.config.write_text(json.dumps(data))
        self.command("crawl")
        store = w.Store(self.output)
        try:
            pages = store.db.execute("SELECT url,path FROM items WHERE kind='page' AND status='ok'").fetchall()
            self.assertEqual(len(pages), 2)
            toc = next(i for i in pages if i["url"].endswith("llms.txt"))
            text = (self.output / toc["path"]).read_text()
            self.assertRegex(text, r"\[Quests\]\([a-f0-9]{20}\.md\)")
            guide = next(i for i in pages if i["url"].endswith("guide.md"))
            self.assertIn("../assets/", (self.output / guide["path"]).read_text())
        finally:
            store.close()

    def test_atlassian_placeholder_and_library_audit(self):
        (self.web / "docs" / "bad.html").write_text(
            '<!doctype html><title>CD PROJEKT RED</title><main>Loading navigation ' +
            'generic navigation ' * 40 + '</main>')
        data = json.loads(self.config.read_text())
        data["sites"][0]["seeds"] = [self.base + "/docs/bad.html"]
        data["sites"][0]["reject_titles"] = ["CD PROJEKT RED"]
        self.config.write_text(json.dumps(data))
        self.command("crawl")
        store = w.Store(self.output)
        try:
            result = store.db.execute("SELECT status FROM items WHERE url LIKE '%bad.html'").fetchone()
            self.assertEqual(result["status"], "failed")
        finally:
            store.close()
        # Previously saved placeholder pages can also be identified and quarantined.
        (self.web / "docs" / "bad.html").write_text(
            '<!doctype html><title>Actual REDkit Guide</title><main>' +
            'WitcherScript quest documentation ' * 25 + '</main>')
        self.command("crawl", "--retry-failed")
        store = w.Store(self.output)
        try:
            self.assertEqual(store.db.execute("SELECT status FROM items WHERE url LIKE '%bad.html'").fetchone()[0], "ok")
            path = store.root / store.db.execute("SELECT path FROM items WHERE url LIKE '%bad.html'").fetchone()[0]
            (path.parent.parent / "raw" / path.name).write_text(
                '<!doctype html><title>CD PROJEKT RED</title><main>' + 'nav ' * 50 + '</main>')
            self.assertEqual(w.audit_library(store, data, repair=True), 1)
            self.assertEqual(store.db.execute("SELECT status FROM items WHERE url LIKE '%bad.html'").fetchone()[0], "suspect")
            self.assertFalse(store.db.execute("SELECT 1 FROM item_search").fetchone())
            self.assertEqual(store.db.execute("SELECT state FROM frontier WHERE url LIKE '%bad.html'").fetchone()[0], "queued")
        finally:
            store.close()

    def test_manual_docs_import(self):
        docs = self.root / "saved-pages"
        (docs / "guides").mkdir(parents=True)
        (docs / "guides" / "quest.md").write_text('# REDkit Quest Graph\nQuest PhaseNode input')
        (docs / "img").mkdir()
        (docs / "img" / "logo.png").write_bytes(b"fakeimage")
        (docs / "scripting.html").write_text('<html><title>Witcher Script</title><main>Script wrapper annotation<img src="img/logo.png"></main></html>')
        store = w.Store(self.output)
        try:
            self.assertEqual(w.import_local_documents(store, docs), 2)
            w.make_index(store)
            self.assertTrue((store.root / "guides.html").exists())
            rows = store.db.execute("SELECT count(*) FROM items WHERE source='manual-import'").fetchone()[0]
            self.assertEqual(rows, 2)
            self.assertTrue((self.output / "imports/manual/img/logo.png").is_file())
            html = (self.output / "imports/manual/scripting.html").read_text()
            self.assertIn('src="img/logo.png"', html)
            result = io.StringIO()
            with contextlib.redirect_stdout(result):
                w.search(store, "PhaseNode", 10)
            self.assertIn("quest", result.getvalue())
        finally:
            store.close()

    def test_index_local_source(self):
        target = self.output / "github" / "example" / "mod"
        target.mkdir(parents=True)
        (target / "test.ws").write_text("function HelloQuest() { WorldChange(); }")
        store = w.Store(self.output)
        from types import SimpleNamespace
        archiver = w.Archiver(store, {"sites": [], "repositories": []}, SimpleNamespace(delay=0.1, max_asset_mb=2))
        try:
            archiver.index_repo("example/mod", target)
            self.assertTrue(store.db.execute("SELECT 1 FROM items WHERE kind='source'").fetchone())
            result = io.StringIO()
            with contextlib.redirect_stdout(result):
                w.search(store, "HelloQuest", 10)
            self.assertIn("test.ws", result.getvalue())
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()

class GitUpdateTest(unittest.TestCase):
    """Regression for GitHub default-branch renames (old origin/master disappears)."""

    @unittest.skipUnless(w.shutil.which("git"), "git unavailable")
    def test_renamed_remote_default_branch_and_clean_checkout(self):
        from types import SimpleNamespace
        import os
        import subprocess

        def git(*args, cwd=None):
            run = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                                 env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})
            if run.returncode:
                self.fail(f"git failed {args}: {run.stderr}")
            return run.stdout.strip()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            upstream = root / 'work'
            upstream.mkdir()
            git('init', '-b', 'master', cwd=upstream)
            git('config', 'user.name', 'Offline Test', cwd=upstream)
            git('config', 'user.email', 'test@example.org', cwd=upstream)
            (upstream/'guide.md').write_text('initial')
            git('add', '.', cwd=upstream)
            git('commit', '-m', 'initial', cwd=upstream)
            remote = root / 'upstream.git'
            git('clone', '--bare', str(upstream), str(remote))
            checkout = root / 'snapshot'
            git('clone', '--depth', '1', remote.as_uri(), str(checkout))
            initial = git('rev-parse', 'HEAD', cwd=checkout)
            # Remote HEAD changes to main; local checkout still tracks master.
            git('checkout', '-b', 'main', cwd=upstream)
            (upstream/'guide.md').write_text('updated')
            git('commit', '-am', 'new page', cwd=upstream)
            git('push', str(remote), 'main', cwd=upstream)
            git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=remote)
            git('push', str(remote), '--delete', 'master', cwd=upstream)
            args = SimpleNamespace(delay=.1, max_asset_mb=2, git_timeout=30)
            library = w.Store(root/'library')
            try:
                arch = w.Archiver(library, {'sites': [], 'repositories': []}, args)
                before, after, dirty = arch._update_repository(checkout, {**os.environ,
                                     "GIT_TERMINAL_PROMPT": "0"})
                self.assertFalse(dirty)
                self.assertEqual(before, initial)
                self.assertNotEqual(before, after)
                self.assertEqual((checkout/'guide.md').read_text(), 'updated')
                (checkout/'guide.md').write_text('my own changes')
                _, again, dirty = arch._update_repository(checkout, os.environ)
                self.assertTrue(dirty)
                self.assertEqual(after, again)
                self.assertEqual((checkout/'guide.md').read_text(), 'my own changes')
            finally:
                library.close()
