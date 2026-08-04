#!/usr/bin/env python3
"""
103FM Podcast Feed Generator - v0.12

Changes from v0.11:
  - Self-host the show cover image. Each run downloads the 103FM cover
    JPG to the repo (as cover.jpg) and the feed points to it via
    feed.urik.uk/cover.jpg instead of the original 103fm URL. Pocket
    Casts and other podcast clients cache original hotlinked images
    aggressively; giving them a new URL under our own domain forces
    a fresh fetch, and also protects against 103FM adding hotlink
    protection in the future.

v0.11:
  - Speaker extraction: truncate at first ':' + tightened role patterns.
"""

import os
import re
import sys
import html
import subprocess
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

# Safety cap: hard limit on commits per calendar month to protect against
# runaway loops. Normal usage is ~22/month. Cloudflare Pages allows 500/month.
# 100 = 4.5x normal, 20% of Cloudflare limit → comfortable margin.
MAX_COMMITS_PER_MONTH = 100
FORCE_RUN = os.environ.get('FORCE_RUN', '').lower() == 'true'

FEED_TITLE = "ינון מגל ובן כספית - 103FM"
FEED_DESCRIPTION = "התוכנית של ינון מגל ובן כספית ברדיו 103FM — כולל ראיונות, ריבים ותוכניות מלאות. פיד אישי."
FEED_LINK = PROGRAM_URL
FEED_LANGUAGE = "he"
FEED_AUTHOR = "103FM"
# Cover image: downloaded from 103FM (below) and served from our own domain
# so podcast clients see it under our URL (bypasses their cache of the old
# 103fm-hotlinked image, and shields us from potential hotlink protection).
FEED_IMAGE = "https://feed.urik.uk/cover.jpg"
IMAGE_SOURCE_URL = "https://103fm.maariv.co.il/download/programs/imgNewTop_262.jpg"
IMAGE_LOCAL_FILE = "cover.jpg"

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

# ---------- Cover image download ----------
def download_cover_image():
    """
    Download the show cover image from 103FM to the local repo.
    Written every run; git will only commit if content changed.
    Failures are non-fatal — the feed still works without a fresh image.
    """
    try:
        req = urllib.request.Request(IMAGE_SOURCE_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if len(data) < 100:
            print(f"  Cover image download suspiciously small ({len(data)} bytes) — skipping save", file=sys.stderr)
            return
        Path(IMAGE_LOCAL_FILE).write_bytes(data)
        print(f"  Cover image saved: {IMAGE_LOCAL_FILE} ({len(data)} bytes)", file=sys.stderr)
    except Exception as e:
        print(f"  Cover image download failed (non-fatal): {e}", file=sys.stderr)

# ---------- Safety cap ----------
def count_commits_this_month() -> int:
    """Count commits to OUTPUT_FILE in the current calendar month (UTC)."""
    try:
        now_utc = datetime.now(timezone.utc)
        first_of_month = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = subprocess.run(
            ['git', 'log', '--since', first_of_month.isoformat(), '--oneline', '--', OUTPUT_FILE],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            print(f"  git log failed (rc={result.returncode}): {result.stderr[:200]}", file=sys.stderr)
            return 0
        lines = [l for l in result.stdout.strip().split('\n') if l.strip()]
        return len(lines)
    except Exception as e:
        print(f"  Failed to count commits: {e}", file=sys.stderr)
        return 0

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

# ---------- Speaker extraction (for enriched segment titles) ----------
HOST_NAMES = ['בן כספית', 'ינון מגל']

# Short-form aliases that map to the canonical host name.
# Longer aliases must come first (matched with startswith).
HOST_ALIASES = [
    ('בן כספית', 'בן כספית'),
    ('ינון מגל', 'ינון מגל'),
    ('כספית', 'בן כספית'),
    ('מגל', 'ינון מגל'),
    ('ינון', 'ינון מגל'),
    # Note: 'בן' alone is intentionally omitted — too common a Hebrew word.
]

# Order matters: more specific patterns first.
# Each pattern captures a name (typically 2 Hebrew words).
# Note: these patterns are applied to the "meta" portion of the description
# (before the first ':'), so anchoring to ^ means start of meta, not start
# of the whole description.
SPEAKER_PATTERNS = [
    # יו"ר [org 1-2 words] [name] - e.g. "יו"ר כחול לבן בני גנץ"
    r'יו"ר\s+\S+(?:\s+\S+)?\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)',
    # ראש עיריית [city] [name]
    r'ראש עיריית\s+\S+\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)',
    # רס"ן/סא"ל/etc. (מיל') [name] - anchored to start (word "אלוף" also means
    # "champion" and appears in prose; only trust it as a rank at the very start).
    r'^(?:רס"ן|סא"ל|אל"מ|תא"ל|אלוף|סג"ם|סרן|רס"ל)\s+\(מיל\'?\)\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)',
    # military ranks (active) - anchored to start of meta
    r'^(?:רס"ן|סא"ל|אל"מ|תא"ל|אלוף|סג"ם|סרן|רס"ל|רב"ט|סמל)\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)',
    # ח"כ [name (exactly 2 words)]
    r'ח"כ\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)(?=\s*[,(]|\s*$)',
    # השר/שר/השרה [name] - requires exactly 2 Hebrew words followed by
    # comma/paren/end. Avoids matching "שר הביטחון" and grabbing verbs.
    r'ה?שר(?:ה)?\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)(?=\s*[,(]|\s*$)',
    # ראש הממשלה / רה"מ / ראש האופוזיציה
    r'(?:ראש הממשלה|רה"מ|ראש האופוזיציה)\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)(?=\s*[,(]|\s*$)',
    # ד"ר / פרופ' / עו"ד
    r'ד"ר\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)(?=\s*[,(]|\s*$)',
    r'פרופ\'\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)(?=\s*[,(]|\s*$)',
    r'עו"ד\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)(?=\s*[,(]|\s*$)',
    # רב / הרב [name] - anchored to start (רב also means "many/great")
    r'^ה?רב\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)(?=\s*[,(]|\s*$)',
    # מנכ"ל / המנכ"ל
    r'ה?מנכ"ל\s+(?:\S+\s+)?([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)',
    # שגריר [country] [name]
    r'שגריר\s+\S+\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)',
    # ראש המועצה [name]
    r'ראש המועצה\s+(?:\S+\s+)?([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)',
    # הכתב(ת) הצבאי(ת)/הפוליטי(ת) [name] - anchored (כתב also means "wrote")
    r'^ה?כתב(?:ת)?\s+ה?\S+\s+([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)',
    # הפרשן [name]
    r'ה?פרשן(?:ית)?\s+(?:ה?\S+\s+)?([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)',
    # Journalist / commentator pattern: "Name Surname (outlet)" at start of description
    # e.g. "יוסי יהושוע (ידיעות אחרונות) מתח ביקורת..."
    r'^([\u05D0-\u05EA]+\s+[\u05D0-\u05EA]+)\s*\(',
]

def extract_speaker(description: str):
    """
    Return the primary speaker/interviewee name from the segment description,
    or None if not confidently detectable. Short-form host names are
    normalized to their canonical full names.
    """
    if not description:
        return None
    # Host commentary: description starts with a host name (full or short form).
    # Checked FIRST because it uses startswith (already implicitly anchored).
    for alias, canonical in HOST_ALIASES:
        if description.startswith(alias):
            # Guard: make sure the match ends at a word boundary
            # (so "מגל" doesn't match a longer word starting with those letters)
            rest = description[len(alias):]
            if not rest or rest[0] in ' :,-.':
                return canonical
    # For role patterns, restrict search to the "meta" portion of the
    # description (before the first ':'). Anything after the colon is
    # the quoted content — role words like 'אלוף' appearing there are
    # not real military rank references.
    colon_idx = description.find(':')
    meta_part = description[:colon_idx] if colon_idx != -1 else description
    # Role + name patterns
    for pattern in SPEAKER_PATTERNS:
        m = re.search(pattern, meta_part)
        if m:
            name = m.group(1).strip()
            # Reject if the "name" itself contains a host or is very short
            if any(h in name for h in HOST_NAMES):
                continue
            if len(name) < 3:
                continue
            return name
    return None

def get_snippet(description: str, max_len: int = 70) -> str:
    """Short snippet of the description, cut at word boundary."""
    if not description:
        return ""
    # Take up to first bullet/pipe separator
    for sep in ('•', '|', '●', ' - '):
        if sep in description:
            description = description.split(sep)[0].strip()
            break
    if len(description) <= max_len:
        return description
    truncated = description[:max_len]
    last_space = truncated.rfind(' ')
    if last_space > max_len // 2:
        truncated = truncated[:last_space]
    return truncated + "..."

def build_segment_title(original_title: str, description: str) -> str:
    """
    Build enriched segment title with emoji type marker.
      Host commentary:  '💬 [host name] [original title]'
      Interview:        '🎙️ [interviewee name] [original title]'
      Unknown/fallback: '🎙️ [title] — [snippet]' (default to interview marker)

    If the original title already starts with the speaker's last name (or
    full name), don't prepend it again — avoids duplicates like
    "בן כספית כספית נגד כ״ץ".
    """
    speaker = extract_speaker(description)
    if speaker:
        marker = "💬" if speaker in HOST_NAMES else "🎙️"
        # Check whether title already begins with the speaker's name.
        # Strip leading quote/whitespace characters to normalize the check.
        stripped_title = original_title.lstrip(' "\'\u201c\u201d')
        speaker_words = speaker.split()
        already_named = (
            stripped_title.startswith(speaker)
            or (speaker_words and stripped_title.startswith(speaker_words[-1]))
        )
        if already_named:
            return f"{marker} {original_title}"
        return f"{marker} {speaker} {original_title}"
    snippet = get_snippet(description)
    if snippet and snippet != original_title:
        return f"🎙️ {original_title} — {snippet}"
    return f"🎙️ {original_title}"

# ---------- Extraction ----------
def extract_items_from_page(page_html: str):
    """
    Extract all items from the show's main page.
    Returns a list of item dicts in the order they should appear in the feed
    (day N segments, day N full, day N-1 segments, day N-1 full, ...).
    """
    items = []

    # Compute today's date once (used as fallback for the "open" day)
    today_il = datetime.now(TZ_IL)
    today_date_str = f"{today_il.day:02d}.{today_il.month:02d}.{today_il.year % 100:02d}"
    today_date_parsed = (today_il.day, today_il.month, today_il.year)

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

        # Primary source: pull the day's date from a segment_date_txt inside
        # this day's content block. This is always accurate because each
        # segment card carries its own date. Avoids the misalignment bug
        # where day_dates[day_idx] is off-by-one when the open day at the
        # top of the page doesn't have a <div class="day_date"> element.
        seg_date_m = re.search(
            r'<div class="segment_date_txt">(\d{1,2}/\d{1,2}/\d{4})</div>',
            day_content
        )
        day_date_parsed = parse_date(seg_date_m.group(1)) if seg_date_m else None

        # Fallback: the open day may not yet have any segments with dates
        # (e.g. early morning). Use today's date only for day_block_idx == 0.
        if day_date_parsed is None and day_block_idx == 0:
            day_date_parsed = today_date_parsed
            print(f"  Day 0: no segment dates found, falling back to today", file=sys.stderr)

        # Build the DD.MM.YY string used in fallback titles
        if day_date_parsed:
            d, m, y = day_date_parsed
            day_date_str = f"{d:02d}.{m:02d}.{y % 100:02d}"
        else:
            day_date_str = None

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

            enriched_title = build_segment_title(title, description)

            day_segments.append({
                "type": "segment",
                "page_url": MEDIA_BASE + href,
                "title": enriched_title,
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
            # Match only the DD.MM.YY format; when 103FM uses Hebrew month
            # names (e.g. "3 באוגוסט 2026" for today's open day) we fall
            # back to day_date_str to keep titles consistent.
            title_m = re.search(r'<div dir="rtl">(התוכנית המלאה\s+\d{1,2}\.\d{1,2}\.\d{2,4})</div>', full_content)
            if title_m:
                base_title = clean_text(title_m.group(1))
            elif day_date_str:
                base_title = f"התוכנית המלאה {day_date_str}"
            else:
                base_title = "התוכנית המלאה"
            title = f"📻 {base_title}"
            day_full = {
                "type": "full",
                "page_url": MEDIA_BASE + href,
                "title": title,
                "description": base_title,
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
    Order within one day (newest first, as shown in podcast apps):
        full episode (top) → segment[0] → segment[1] → ... → segment[K] (bottom)
    Strategy: full gets 13:00, segments get 12:59-seg_idx.
    """
    if not item.get("date"):
        return datetime.now(TZ_IL)
    day, month, year = item["date"]
    try:
        if item["type"] == "segment":
            seg_idx = item.get("seg_idx", 0)
            minute = max(0, 59 - seg_idx)
            return datetime(year, month, day, 12, minute, 0, tzinfo=TZ_IL)
        else:  # full episode - newest of its day
            return datetime(year, month, day, 13, 0, 0, tzinfo=TZ_IL)
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
    # Safety cap - halt if we've exceeded the monthly commit budget
    if FORCE_RUN:
        print("FORCE_RUN=true: skipping safety cap check", file=sys.stderr)
    else:
        commits = count_commits_this_month()
        print(f"Safety check: {commits}/{MAX_COMMITS_PER_MONTH} commits to feed this month", file=sys.stderr)
        if commits >= MAX_COMMITS_PER_MONTH:
            print(f"HALTING: monthly commit cap reached ({commits} >= {MAX_COMMITS_PER_MONTH}).", file=sys.stderr)
            print(f"To override for one run, trigger workflow_dispatch with force=true.", file=sys.stderr)
            sys.exit(0)

    print(f"Fetching program page: {PROGRAM_URL}", file=sys.stderr)
    page_html = fetch(PROGRAM_URL)

    print("Downloading cover image...", file=sys.stderr)
    download_cover_image()

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
