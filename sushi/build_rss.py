"""Build the podcast RSS feed by listing the repo's podcasts/ directory
via the GitHub Contents API, then PUT the generated feed back to the repo.

Usage:
    python build_rss.py

Env vars:
    GITHUB_PAT           (required) token with contents:write on the repo
    GH_REPO              owner/repo (default: mparellada/market-briefing)
"""
from __future__ import annotations
import os
import sys
import json
import base64
import datetime
import urllib.request
from email.utils import formatdate
from xml.sax.saxutils import escape

REPO = os.environ.get("GH_REPO", "mparellada/market-briefing")
TOKEN = os.environ["GITHUB_PAT"]
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


def _gh(path: str, method: str = "GET", body: bytes | None = None) -> dict:
    url = f"https://api.github.com/repos/{REPO}{path}"
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"token {TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "sushi-rss-builder",
        },
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def rfc822(ts: float) -> str:
    return formatdate(ts, usegmt=True)


def list_podcasts() -> list[dict]:
    items = []
    try:
        entries = _gh("/contents/podcasts")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    for entry in entries:
        name = entry["name"]
        if not (name.startswith("podcast-") and name.endswith(".mp3")):
            continue
        date_part = name[len("podcast-"):-len(".mp3")]
        try:
            dt = datetime.datetime.strptime(date_part, "%Y-%m-%d")
        except ValueError:
            continue
        items.append({
            "name": name,
            "dt": dt,
            "size": int(entry.get("size", 0)),
        })
    items.sort(key=lambda x: x["dt"], reverse=True)
    return items


def build_feed(items: list[dict]) -> str:
    item_xml = []
    for item in items:
        pub_ts = item["dt"].replace(
            hour=7, minute=0, tzinfo=datetime.timezone.utc
        ).timestamp()
        url = f"{FEED_BASE}/podcasts/{item['name']}"
        title = f"Market Briefing — {item['dt'].strftime('%A, %B %d, %Y')}"
        item_xml.append(f"""
    <item>
      <title>{escape(title)}</title>
      <description>{escape(title)}</description>
      <pubDate>{rfc822(pub_ts)}</pubDate>
      <enclosure url="{url}" length="{item['size']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{item['name']}</guid>
      <itunes:author>{escape(AUTHOR)}</itunes:author>
      <itunes:summary>{escape(title)}</itunes:summary>
      <itunes:explicit>false</itunes:explicit>
      <itunes:duration>20:00</itunes:duration>
    </item>""")

    last_build = rfc822(
        datetime.datetime.now(datetime.timezone.utc).timestamp()
    )
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
{"".join(item_xml)}
  </channel>
</rss>
"""


def upsert_feed(feed: str) -> None:
    path = "/contents/podcast.xml"
    encoded = base64.b64encode(feed.encode("utf-8")).decode("ascii")
    sha = None
    try:
        current = _gh(path)
        sha = current.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    payload: dict = {
        "message": f"RSS rebuild {datetime.date.today().isoformat()}",
        "content": encoded,
    }
    if sha:
        payload["sha"] = sha
    _gh(path, method="PUT", body=json.dumps(payload).encode("utf-8"))


def main() -> int:
    items = list_podcasts()
    print(f"Found {len(items)} podcast episodes")
    feed = build_feed(items)
    upsert_feed(feed)
    print(f"Published {FEED_BASE}/podcast.xml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
