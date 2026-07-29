#!/usr/bin/env python3
"""
103FM Podcast Feed Generator
Scrapes the 'Yinon Magal & Ben Caspit' show page and generates an RSS podcast feed.
"""

import re
import sys
import html
import urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path

# ---------- Configuration ----------
PROGRAM_ID = "FJF"  # Yinon Magal & Ben Caspit on 103FM
PROGRAM_URL = f"https://103fm.maariv.co.il/programs/complete_episodes.aspx?c41t4nzVQ={PROGRAM_ID}"
MEDIA_BASE = "https://103fm.maariv.co.il"
MP3_BASE = "https://awaod01.streamgates.net/103fm_aw"
OUTPUT_FILE = "pkfsfm9xqbhxbxkc.xml"  # Obfuscated filename
MAX_EPISODES = 7
USER_AGENT = "Mozilla/5.0 (compatible; PodcastFeedBot/1.0)"

FEED_TITLE = "ינון מגל ובן כספית - 103FM"
FEED_DESCRIPTION = "התוכנית היומית המלאה של ינון מגל ובן כספית ברדיו 103FM. פיד אישי."
FEED_LINK = "https://103fm.maariv.co.il/program/ינון-מגל-בן-כספית.aspx"
FEED_LANGUAGE = "he"
FEED_AUTHOR = "103FM"
FEED_IMAGE = "https://103fm.maariv.co.il/images/logo_fm_footer.png"

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
    except Exception as e:
        print(f"  HEAD failed: {e}", file=sys.stderr)
        return None, 0

# ---------- Extraction ----------
def find_episode_page_urls(list_html: str):
    """Extract links to individual episode pages from the show's episode list page."""
    pattern = r'/programs/media\.aspx\?ZrqvnVq=([A-Za-z0-9]+)&(?:amp;)?c41t4nzVQ=' + PROGRAM_ID
    matches = re.findall(pattern, list_html)
    seen = set()
    result = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(f"{MEDIA_BASE}/programs/media.aspx?ZrqvnVq={m}&c41t4nzVQ={PROGRAM_ID}")
    return result

def extract_episode_info(page_html: str, page_url: str):
    """From a single episode page, extract data-file value, title, and date."""
    df_match = re.search(r'data-file="([^"]+)"', page_html)
    if not df_match:
        return None
    data_file = df_match.group(1)

    title = "התוכנית המלאה"
    pub_date = None

    # Try to match date like "התוכנית המלאה 29.07.26"
    date_match = re.search(r'התוכנית המלאה\s+(\d{1,2})\.(\d{1,2})\.(\d{2,4})', page_html)
    if date_match:
        day, month, year = (int(x) for x in date_match.groups())
        if year < 100:
            year += 2000
        title = f"התוכנית המלאה {day:02d}.{month:02d}.{year % 100:02d}"
        try:
            tz_il = timezone(timedelta(hours=3))
            pub_date = datetime(year, month, day, 11, 0, 0, tzinfo=tz_il)
        except ValueError:
            pub_date = None

    return {
        "data_file": data_file,
        "title": title,
        "page_url": page_url,
        "pub_date": pub_date,
    }

# ---------- XML building ----------
def escape_xml(text: str) -> str:
    return html.escape(text, quote=True)

def build_rss(episodes) -> str:
    now = datetime.now(timezone.utc)
    items_xml = []

    for ep in episodes:
        mp3_url = f"{MP3_BASE}/{ep['data_file']}.mp3"
        status, size = head(mp3_url)
        if status is None or size == 0:
            print(f"  Skipping {ep['data_file']} (HEAD failed or size 0)", file=sys.stderr)
            continue

        pub_date = ep["pub_date"] or now
        pub_date_str = format_datetime(pub_date)
        guid = ep["data_file"]

        items_xml.append(f"""    <item>
      <title>{escape_xml(ep['title'])}</title>
      <link>{escape_xml(ep['page_url'])}</link>
      <description>{escape_xml(ep['title'])}</description>
      <enclosure url="{escape_xml(mp3_url)}" length="{size}" type="audio/mpeg"/>
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
    list_html = fetch(PROGRAM_URL)

    episode_urls = find_episode_page_urls(list_html)
    print(f"Found {len(episode_urls)} episode page links", file=sys.stderr)

    if not episode_urls:
        print("ERROR: No episodes found. HTML structure may have changed.", file=sys.stderr)
        sys.exit(1)

    episodes = []
    for url in episode_urls[:MAX_EPISODES]:
        print(f"Fetching episode page: {url}", file=sys.stderr)
        try:
            page_html = fetch(url)
            info = extract_episode_info(page_html, url)
            if info:
                episodes.append(info)
                print(f"  Got: {info['title']} -> {info['data_file']}", file=sys.stderr)
            else:
                print(f"  Could not extract data-file", file=sys.stderr)
        except Exception as e:
            print(f"  Error: {e}", file=sys.stderr)

    if not episodes:
        print("ERROR: No valid episodes found.", file=sys.stderr)
        sys.exit(1)

    print(f"Building RSS with {len(episodes)} episodes...", file=sys.stderr)
    rss_xml = build_rss(episodes)

    Path(OUTPUT_FILE).write_text(rss_xml, encoding="utf-8")
    print(f"Written: {OUTPUT_FILE} ({len(rss_xml)} bytes)", file=sys.stderr)

if __name__ == "__main__":
    main()
