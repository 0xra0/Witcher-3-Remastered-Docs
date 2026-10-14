# The Witcher 3: Wild Hunt — Remastered modding docs

An independently maintained offline archive of **public, official** documentation relevant to The Witcher 3: Wild Hunt — Remastered. The current snapshot was collected on **13 October 2026** and includes CD PROJEKT RED's [REDkit documentation](https://cdprojektred.atlassian.net/wiki/spaces/W3REDkit/overview), four [official mod.io guides](https://mod.io/g/the-witcher-3/r?tags=Official), and the [official patch 5.01 notes](https://www.thewitcher.com/us/en/news/52085/patch-5-01-for-the-witcher-3-wild-hunt-remastered-is-live). It contains no mirrored GitHub repositories or community mod downloads.

Patch 5.01 is documented for PC, PlayStation 5, and Xbox Series X|S. The official note says the Nintendo Switch 2 update was still in progress at the snapshot date. Older REDkit tutorials remain useful reference material, but their presence does not establish that every procedure was tested with 5.01. Check the current REDkit changelog and game patch notes before following version-sensitive instructions.

## Download and read

The [latest GitHub release](https://github.com/0xra0/Witcher-3-Remastered-Docs/releases/latest) contains:

| Asset | Contents |
| --- | --- |
| `witcher3_official_5_01_docs.zip` | Small, text-focused set: the 5.01 patch note, REDkit 5.x changelog, and four official Remastered guides. |
| `witcher3_official_full_docs.zip.part-aa` and `.part-ab` | Two pieces of one full ZIP archive: 90 public pages, original available attachments, screenshots, and 14 official-guide-linked tutorial videos. Download **both** pieces. |
| `witcher3_offline_scraper_v9.zip` | Scripts and source configuration used to build and verify the full archive. |
| `SHA256SUMS` | Checksums for every release asset. |

For the small archive, extract the ZIP and open `witcher3_official_5_01_docs/index.html` in a browser. Use `guides.html` to browse the guides. The small archive does not contain the full media collection.

For the full archive, put both `.part-*` files in one directory, join them, then extract the ZIP:

```bash
cat witcher3_official_full_docs.zip.part-aa witcher3_official_full_docs.zip.part-ab > witcher3_official_full_docs.zip
sha256sum -c SHA256SUMS
unzip witcher3_official_full_docs.zip
```

On Windows PowerShell, join the parts without treating them as text:

```powershell
$out = [IO.File]::Create('witcher3_official_full_docs.zip')
try {
  foreach ($part in @('witcher3_official_full_docs.zip.part-aa', 'witcher3_official_full_docs.zip.part-ab')) {
    $inputFile = [IO.File]::OpenRead($part)
    try { $inputFile.CopyTo($out) } finally { $inputFile.Dispose() }
  }
} finally { $out.Dispose() }
Expand-Archive witcher3_official_full_docs.zip -DestinationPath .
```

Open `witcher3_official_full_docs/index.html` in any modern browser. Search is built into the offline reader. `guides.html` lists the guides; `media.html` and `videos.html` browse locally saved attachments and tutorials. Each archived page has a companion `.txt` file with its original URL and fetch time. `ARCHIVE_NOTES.md` explains coverage and known source gaps. No server or account is needed to read the downloaded archive.

## Rebuild or update the archive

Use the [v9 scripts](scripts/witcher3_offline_scraper_v9/README.md). They require Python 3.10+; a full video download also needs `yt-dlp` and `ffmpeg`. On Linux, mod.io rendering needs Chromium or Playwright's Chromium. Run from the script directory:

```bash
cd scripts/witcher3_offline_scraper_v9
python3 full_dump.py --output ./Witcher3OfficialDocs
python3 witcher_archive.py search 'scope-based' --output ./Witcher3OfficialDocs
python3 verify_archive.py --output ./Witcher3OfficialDocs
```

Re-run `full_dump.py` with the same output path to resume downloads and discover new public REDkit pages. `sources.json` is restricted to the official sources listed above and has an empty `repositories` list. Historical community source configurations are excluded from this repository and release. Scripts for earlier archive versions are retained locally as development history; v9 produced the full official archive.

## Coverage and provenance

The full archive reports 85 public REDkit space pages (84 article pages and the space overview), four mod.io guides marked Official, and one game patch note. Its verification report records 1,128 saved Confluence attachments and 14 downloaded tutorial videos with zero integrity errors. The source listed 38 additional zero-byte `Invalid file id` attachments whose public URLs returned 404; they are recorded in `media_manifest.json` as unavailable. See `VERIFICATION.json` in the full archive for exact counts and checks.

This repository is an independent archive and is **not affiliated with or endorsed by CD PROJEKT RED**. Documentation, trademarks, and media remain the property of their respective owners. The archiving scripts carry their own MIT license. For the newest instructions and changes after the snapshot date, use the linked official sources.
