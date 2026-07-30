#!/usr/bin/env python3
"""
103FM Podcast Feed Generator - v0.2

Scrapes the show's main page and extracts, per day:
  - Segments (interviews, arguments)
  - Full episode ("התוכנית המלאה")

Feed order (newest first, as podcast apps display):
  Day N: segment[0], segment[1], ..., segment[K], full episode
  Day N-1: segment[0], ..., full episode
  ...

pubDate is computed so segments always appear ABOVE the full episode
of the same day in podcast apps.
"""

import re
import sys
import html
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- Configuration ----------
PROGRAM_ID = "FJF"
PROGRAM_URL = "https://103fm.maariv.co.il/program/" + urllib.parse.quote("ינון-מגל-בן-כספית") + ".aspx"
MEDIA_BASE = "https://103fm.maariv.co.il"
MP3_BASE = "https://awaod01.streamgates.net/103fm_aw"
OUTPUT_FILE = "pkfsfm9xqbhxbxkc.xml"
MAX_DAYS = 5
CONCURRENT_REQUESTS = 8
USER_AGENT = "Mozilla/5.0 (compatible; PodcastFeedBot/1.0)"

FEED_TITLE = "ינון מגל ובן כספית - 103FM"
FEED_DESCRIPTION = "התוכנית של ינון מגל ובן כספית ברדיו 103FM — כולל ראיונות, ריבים ותוכניות מלאות. פיד אישי."
FEED_LINK = PROGRAM_URL
FEED_LANGUAGE = "he"
FEED_AUTHOR = "103FM"
FEED_IMAGE = "https://103fm.maariv.co.il/images/logo_fm_footer.png"

TZ_IL = timezone(timedelta(hours=3))

# ---------- HTTP helpers ----------
def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

def head(url: str):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            length = r.headers.get("Content-Length")
            return r.status, int(length) if length else 0
    except Exception:
        return None, 0

# ---------- Helpers ----------
def parse_date(date_str: str):
    """Parse date in DD/MM/YYYY or DD.MM.YY format. Returns (day, month, year) or None."""
    for sep in ("/", "."):
        if sep in date_str:
            parts = date_str.split(sep)
            if len(parts) == 3:
                try:
                    d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                    if y < 100:
                        y += 2000
                    return d, m, y
                except ValueError:
                    pass
    return None

def clean_text(text: str) -> str:
    """Strip tags, decode HTML entities, normalize whitespace."""
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ---------- Extraction ----------
def extract_items_from_page(page_html: str):
    """
    Extract all items from the show's main page.
    Returns a list of item dicts, in the order they should appear in the feed
    (day N segments, day N full, day N-1 segments, day N-1 full, ...).
    """
    items = []

    day_dates = re.findall(
        r'<div class="day_date">(\d{1,2}\.\d{1,2}\.\d{2,4})</div>',
        page_html
    )
    print(f"  Found {len(day_dates)} day dates: {day_dates}", file=sys.stderr)

    day_pattern = re.compile(
        r'<div class="days grid" id="innerList_(\d+)"[^>]*>(.*?)</div>\s*</li>',
        re.DOTALL
    )
    day_blocks = day_pattern.findall(page_html)
    print(f"  Found {len(day_blocks)} day blocks", file=sys.stderr)

    for day_block_idx, (day_idx_str, day_content) in enumerate(day_blocks[:MAX_DAYS]):
        day_idx = int(day_idx_str)
        day_date_str = day_dates[day_idx] if day_idx < len(day_dates) else None
        day_date_parsed = parse_date(day_date_str) if day_date_str else None

        # Segments (interviews/arguments)
        segment_pattern = re.compile(
            r'<a\s+href="(/programs/media\.aspx\?ZrqvnVq=[^"]+)"\s+'
            r'id="[^"]*segmentLink_(\d+)"[^>]*>(.*?)</a>',
            re.DOTALL
        )
        segments = segment_pattern.findall(day_content)

        day_segments = []
        for href, seg_idx_str, seg_content in segments:
            title_m = re.search(r'<div class="segment_title"[^>]*>(.*?)</div>', seg_content, re.DOTALL)
            info_m = re.search(r'<div class="segment_info"[^>]*>(.*?)</div>', seg_content, re.DOTALL)
            date_m = re.search(r'<div class="segment_date_txt">(\d{1,2}/\d{1,2}/\d{4})</div>', seg_content)

            if not title_m:
                continue

            title = clean_text(title_m.group(1))
            description = clean_text(info_m.group(1)) if info_m else title
            item_date = parse_date(date_m.group(1)) if date_m else day_date_parsed

            day_segments.append({
                "type": "segment",
                "page_url": MEDIA_BASE + href,
                "title": title,
                "description": description,
                "date": item_date,
                "seg_idx": int(seg_idx_str),
            })

        # Full episode
        full_pattern = re.compile(
            r'<a\s+href="(/programs/media\.aspx\?ZrqvnVq=[^"]+)"\s+'
            r'id="[^"]*fullShowLink_\d+"[^>]*>(.*?)</a>',
            re.DOTALL
        )
        full_shows = full_pattern.findall(day_content)

        day_full = None
        for href, full_content in full_shows:
            title_m = re.search(r'<div dir="rtl">(התוכנית המלאה\s+[\d.]+)</div>', full_content)
            title = clean_text(title_m.group(1)) if title_m else (
                f"התוכנית המלאה {day_date_str}" if day_date_str else "התוכנית המלאה"
            )
            day_full = {
                "type": "full",
                "page_url": MEDIA_BASE + href,
                "title": title,
                "description": title,
                "date": day_date_parsed,
            }
            break

        items.extend(day_segments)
        if day_full:
            items.append(day_full)

        print(f"  Day {day_block_idx} ({day_date_str}): {len(day_segments)} segments + {1 if day_full else 0} full", file=sys.stderr)

    return items

def fetch_data_file(item):
    """Fetch the item's page and extract data-file (audio ID). Mutates item."""
    try:
        page_html = fetch(item["page_url"])
        df_match = re.search(r'data-file="([^"]+)"', page_html)
        if df_match:
            item["data_file"] = df_match.group(1)
    except Exception as e:
        print(f"  fetch_data_file error for {item.get('title', '?')}: {e}", file=sys.stderr)
    return item

def fetch_size(item):
    """HEAD the MP3 URL to get file size. Mutates item."""
    if not item.get("data_file"):
        return item
    mp3_url = f"{MP3_BASE}/{item['data_file']}.mp3"
    _, size = head(mp3_url)
    item["mp3_url"] = mp3_url
    item["size"] = size
    return item

# ---------- pubDate logic ----------
def compute_pub_date(item):
    """
    Within one day, segments should appear ABOVE full episode in podcast apps.
    Strategy: segments get 12:59-N, full episode gets 09:00.
    """
    if not item.get("date"):
        return datetime.now(TZ_IL)
    day, month, year = item["date"]
    try:
        if item["type"] == "segment":
            seg_idx = item.get("seg_idx", 0)
            minute = max(0, 59 - seg_idx)
            return datetime(year, month, day, 12, minute, 0, tzinfo=TZ_IL)
        else:
            return datetime(year, month, day, 9, 0, 0, tzinfo=TZ_IL)
    except ValueError:
        return datetime.now(TZ_IL)

# ---------- XML building ----------
def escape_xml(text: str) -> str:
    return html.escape(text, quote=True)

def build_rss(items) -> str:
    now = datetime.now(timezone.utc)
    items_xml = []

    for item in items:
        if not item.get("data_file") or not item.get("size"):
            print(f"  Skipping (missing data_file or size): {item.get('title', '?')}", file=sys.stderr)
            continue

        pub_date_str = format_datetime(compute_pub_date(item))
        guid = item["data_file"]

        items_xml.append(f"""    <item>
      <title>{escape_xml(item['title'])}</title>
      <link>{escape_xml(item['page_url'])}</link>
      <description>{escape_xml(item['description'])}</description>
      <enclosure url="{escape_xml(item['mp3_url'])}" length="{item['size']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{escape_xml(guid)}</guid>
      <pubDate>{pub_date_str}</pubDate>
      <itunes:explicit>false</itunes:explicit>
    </item>""")

    build_date_str = format_datetime(now)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape_xml(FEED_TITLE)}</title>
    <link>{escape_xml(FEED_LINK)}</link>
    <description>{escape_xml(FEED_DESCRIPTION)}</description>
    <language>{FEED_LANGUAGE}</language>
    <lastBuildDate>{build_date_str}</lastBuildDate>
    <itunes:author>{escape_xml(FEED_AUTHOR)}</itunes:author>
    <itunes:summary>{escape_xml(FEED_DESCRIPTION)}</itunes:summary>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="News"/>
    <itunes:image href="{escape_xml(FEED_IMAGE)}"/>
    <image>
      <url>{escape_xml(FEED_IMAGE)}</url>
      <title>{escape_xml(FEED_TITLE)}</title>
      <link>{escape_xml(FEED_LINK)}</link>
    </image>
{chr(10).join(items_xml)}
  </channel>
</rss>
"""

# ---------- Main ----------
def main():
    print(f"Fetching program page: {PROGRAM_URL}", file=sys.stderr)
    page_html = fetch(PROGRAM_URL)

    print("Extracting items...", file=sys.stderr)
    items = extract_items_from_page(page_html)
    if not items:
        print("ERROR: No items found. HTML structure may have changed.", file=sys.stderr)
        sys.exit(1)
    print(f"Total items extracted: {len(items)}", file=sys.stderr)

    print(f"Fetching data-file for each item ({CONCURRENT_REQUESTS} workers parallel)...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=CONCURRENT_REQUESTS) as ex:
        futures = [ex.submit(fetch_data_file, item) for item in items]
        for _ in as_completed(futures):
            pass

    print("Fetching file sizes...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=CONCURRENT_REQUESTS) as ex:
        futures = [ex.submit(fetch_size, item) for item in items]
        for _ in as_completed(futures):
            pass

    valid = [i for i in items if i.get("data_file") and i.get("size")]
    print(f"Valid items: {len(valid)}/{len(items)}", file=sys.stderr)

    if not valid:
        print("ERROR: No valid items to include in feed.", file=sys.stderr)
        sys.exit(1)

    rss_xml = build_rss(items)
    Path(OUTPUT_FILE).write_text(rss_xml, encoding="utf-8")
    print(f"Written: {OUTPUT_FILE} ({len(rss_xml)} bytes, {len(valid)} items)", file=sys.stderr)

if __name__ == "__main__":
    main()
