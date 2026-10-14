# Witcher 3 official offline documentation archive (v9)

The full dump downloads the public **The Witcher 3 REDkit** documentation space from CD PROJEKT RED's Confluence, four Remastered modding guides marked `Official` on mod.io, and the official game patch 5.01 notes. It also enumerates every public Confluence page attachment through the v2 API and saves original images and hosted videos. The REDkit pages have offline reader copies with screenshots next to their instructions and links to local media galleries. The YouTube tutorials embedded by the official guides are saved as playable MP4 files at up to 720p, with thumbnails and source links. Pages are discovered from the public space API at run time, so new public pages are included on future runs.

The REDkit space contains historical tutorials as well as its 2026 5.x changelog. An older page's presence in this archive does **not** mean its instructions have been updated for game patch 5.01. Use the changelog and current Remastered guides to check version-specific features and behavior. 

## Run full dump

Python 3.10+, `yt-dlp`, and `ffmpeg` are required for a full dump with YouTube videos. On Linux, mod.io pages need Chromium for JavaScript rendering. The script checks `W3_CHROMIUM`, common Chromium executable names, and a Puppeteer headless shell under `~/.cache/puppeteer`. Alternatively install Playwright and its Chromium browser.

```bash
cd witcher3_offline_scraper_v9
python3 full_dump.py --output ./Witcher3OfficialDocs
python3 witcher_archive.py search 'scope-based' --output ./Witcher3OfficialDocs
```

If Chromium is at another path:

```bash
W3_CHROMIUM=/absolute/path/to/chrome-headless-shell python3 full_dump.py --output ./Witcher3OfficialDocs
```

Open `index.html`, `media.html`, or `videos.html` in the output directory. Each archived page has a `.txt` file beside its `.html` file with its source URL and fetch time. Original HTML is saved under `raw`; the Confluence storage bodies are under `storage`; reader pages with inline images are under `reader`. `library.sqlite3` powers search. `media_inventory.json` lists all public attachments the API returned, and `media_manifest.json` records saved files, checksums, and errors. Re-run the same command into the same output directory to resume incomplete media downloads and discover new public pages. If pages are already archived, use `python3 full_dump.py --skip-crawl --output ./Witcher3OfficialDocs`.

Run `python3 verify_archive.py --output ./Witcher3OfficialDocs` at any time to recheck file sizes, SHA-256 hashes, reader coverage, and downloaded MP4 audio/video streams. The result is saved as `VERIFICATION.json`.


The page crawler follows public `robots.txt` rules and request pacing. The attachment downloader uses Confluence's documented public attachment API and original download links, with a one second interval between requests. It uses no account, API token, or encryption bypass. It saves original page attachments, including any example ZIP files that the authors attached. Only anonymously accessible public pages and media can be archived. A zero-byte attachment named `Invalid file id` in the source API cannot be downloaded; the manifest records it as unavailable. YouTube videos are external to Confluence, so a future source change or unavailable video may require re-running `download_videos.py`.
