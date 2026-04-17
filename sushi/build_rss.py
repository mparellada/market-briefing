"""Build a podcast RSS feed from the podcasts/ directory in the repo.

Scans mparellada.github.io/market-briefing/podcasts/*.mp3, generates
an iTunes-compatible RSS 2.0 feed at the repo root as podcast.xml.

Usage: python build_rss.py <repo_root>
"""
from __future__ import annotations
import sys
import os
import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from email.utils import formatdate

FEED_BASE = "https://mparellada.github.io/market-briefing"
TITLE = "Marc's Daily Market Briefing"
DESCRIPTION = (
    "Every weekday morning: global markets, major news, Spain and Catalonia, "
    "and AI world updates. Compiled autonomously from BBC, FT, Bloomberg, "
    "NYT, The Economist, La Vanguardia, El Economista, and 3cat.cat."
)
AUTHOR = "Marc Parellada"
LANGUAGE = "en-us"
CATEGORY = "Business"
IMAGE = f"{FEED_BASE}/cover.png"


def rfc822(ts: float) -> str:
    return formatdate(ts, usegmt=True)


def build_feed(repo_root: Path) -> str:
    podcast_dir = repo_root / "podcasts"
    items = []
    for mp3 in sorted(podcast_dir.glob("podcast-*.mp3"), reverse=True):
        name = mp3.stem  # podcast-2026-04-17
        date_part = name.replace("podcast-", "")
        try:
            dt = datetime.datetime.strptime(date_part, "%Y-%m-%d")
        except ValueError:
            continue
        size = mp3.stat().st_size
        pub_ts = dt.replace(hour=7, minute=0, tzinfo=datetime.timezone.utc).timestamp()
        url = f"{FEED_BASE}/podcasts/{mp3.name}"
        title = f"Market Briefing — {dt.strftime('%A, %B %d, %Y')}"
        items.append(f"""
    <item>
      <title>{escape(title)}</title>
      <description>{escape(title)}</description>
      <pubDate>{rfc822(pub_ts)}</pubDate>
      <enclosure url="{url}" length="{size}" type="audio/mpeg"/>
      <guid isPermaLink="false">{mp3.name}</guid>
      <itunes:author>{escape(AUTHOR)}</itunes:author>
      <itunes:summary>{escape(title)}</itunes:summary>
      <itunes:explicit>false</itunes:explicit>
    </item>""")

    last_build = rfc822(datetime.datetime.now(datetime.timezone.utc).timestamp())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(TITLE)}</title>
    <link>{FEED_BASE}/</link>
    <atom:link href="{FEED_BASE}/podcast.xml" rel="self" type="application/rss+xml"/>
    <description>{escape(DESCRIPTION)}</description>
    <language>{LANGUAGE}</language>
    <lastBuildDate>{last_build}</lastBuildDate>
    <itunes:author>{escape(AUTHOR)}</itunes:author>
    <itunes:summary>{escape(DESCRIPTION)}</itunes:summary>
    <itunes:owner>
      <itunes:name>{escape(AUTHOR)}</itunes:name>
      <itunes:email>mparellada92@gmail.com</itunes:email>
    </itunes:owner>
    <itunes:image href="{IMAGE}"/>
    <itunes:category text="{CATEGORY}"/>
    <itunes:explicit>false</itunes:explicit>
{"".join(items)}
  </channel>
</rss>
"""


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: build_rss.py <repo_root>", file=sys.stderr)
        return 2
    repo_root = Path(sys.argv[1])
    feed = build_feed(repo_root)
    out = repo_root / "podcast.xml"
    out.write_text(feed, encoding="utf-8")
    print(f"Wrote {out} ({len(feed)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
