"""Synthesize a long podcast from a plain-text script using Azure Neural TTS.

Usage:
    python azure_tts.py <input.txt> <output.mp3>

Env vars:
    AZURE_TTS_KEY      Azure Speech subscription key
    AZURE_TTS_REGION   e.g. eastus
    AZURE_TTS_VOICE    default: en-US-AndrewMultilingualNeural

Handles long scripts by chunking at sentence boundaries (~3000 chars per chunk,
Azure's safe limit per request), synthesizing each chunk, then concatenating
with ffmpeg.
"""
from __future__ import annotations
import os
import re
import sys
import time
import subprocess
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

CHUNK_TARGET = 2800  # chars per TTS request (Azure hard limit is ~10 min audio)
VOICE = os.environ.get("AZURE_TTS_VOICE", "en-US-AndrewMultilingualNeural")
KEY = os.environ["AZURE_TTS_KEY"]
REGION = os.environ.get("AZURE_TTS_REGION", "eastus")
ENDPOINT = f"https://{REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"


TICKER_PRONUNCIATIONS = {
    # Pairs & ratios -> "X versus Y" reads naturally
    "EUR/USD": "EUR versus USD",
    "EUR/SGD": "EUR versus SGD",
    "USD/SGD": "USD versus SGD",
    "USD/THB": "USD versus THB",
    "USD/JPY": "USD versus JPY",
    "GBP/USD": "GBP versus USD",
    "BTC/USD": "Bitcoin versus dollar",
    "ETH/USD": "Ethereum versus dollar",
    "XAU/USD": "gold spot",
    "XAG/USD": "silver spot",
    # Common symbols that read badly
    "S&P 500": "S and P 500",
    "S&P500": "S and P 500",
    "S&P": "S and P",
    "AT&T": "A T and T",
    "P&G": "P and G",
    "J&J": "J and J",
    # Indices / tickers -> natural names
    "IBEX35": "IBEX 35",
    "STOXX50": "STOXX 50",
    "N225": "Nikkei",
    "NFLX": "Netflix",
    "TSLA": "Tesla",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "Nvidia",
    "GOOGL": "Google",
    "AMZN": "Amazon",
    "META": "Meta",
}


def clean_script(text: str) -> str:
    """Normalize the script so a TTS engine reads it as a news bulletin.

    Strips dividers, markdown markers, expands tickers/symbols that read
    poorly, and handles section headers like "=== SECTION ===".
    """
    # First, substitute ticker/symbol pronunciations in the raw text
    for src, dst in TICKER_PRONUNCIATIONS.items():
        text = text.replace(src, dst)

    # Normalize curly/em-dashes to commas for natural pauses
    text = text.replace("\u2014", ",").replace("\u2013", ",")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    # Replace broken encodings sometimes produced by Windows consoles
    text = text.replace("\ufffd?\"", ",").replace("\ufffd?", ",")

    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        # Drop pure dividers made of repeated punctuation
        if re.match(r"^[\-=#*_~]{3,}$", stripped):
            continue
        # "=== SECTION ===" -> "Section." (keeps a natural pause)
        m = re.match(r"^={2,}\s*(.+?)\s*={2,}$", stripped)
        if m:
            title = m.group(1).strip().rstrip(":.")
            lines.append("")
            lines.append(f"{title.title()}.")
            continue
        # Strip leading markdown markers but keep content
        stripped = re.sub(r"^#+\s*", "", stripped)
        stripped = re.sub(r"^\*+\s*", "", stripped)
        stripped = re.sub(r"^-\s+", "", stripped)
        # Strip stray = signs that survived (bulletin item separators etc.)
        stripped = re.sub(r"\s*=+\s*", " ", stripped)
        # Convert remaining slashes between words to "and" (covers edge
        # cases like "and/or", "Israel/Lebanon" -> less robotic reading).
        stripped = re.sub(r"(?<=\w)/(?=\w)", " and ", stripped)
        # Collapse whitespace
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


def chunk_text(text: str, target: int = CHUNK_TARGET) -> list[str]:
    """Split into chunks at sentence/paragraph boundaries under `target` chars."""
    # Split into paragraphs first (double newline)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) + 2 <= target:
            buf = (buf + "\n\n" + para) if buf else para
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        # Paragraph itself is larger than target: split by sentences
        if len(para) > target:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            sbuf = ""
            for s in sentences:
                if len(sbuf) + len(s) + 1 <= target:
                    sbuf = (sbuf + " " + s) if sbuf else s
                else:
                    if sbuf:
                        chunks.append(sbuf)
                    sbuf = s
            if sbuf:
                buf = sbuf
        else:
            buf = para
    if buf:
        chunks.append(buf)
    return chunks


def synthesize_chunk(text: str, out_path: Path, retries: int = 3) -> None:
    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xml:lang="en-US">'
        f'<voice name="{VOICE}">'
        '<prosody rate="+5%">'
        f'{xml_escape(text)}'
        '</prosody>'
        '</voice>'
        '</speak>'
    ).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=ssml,
        method="POST",
        headers={
            "Ocp-Apim-Subscription-Key": KEY,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
            "User-Agent": "sushi-market-briefing",
        },
    )
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            out_path.write_bytes(data)
            return
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            last_err = f"HTTP {e.code}: {body}"
            if e.code == 429:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(last_err)
        except Exception as e:
            last_err = str(e)
            time.sleep(2 * attempt)
    raise RuntimeError(f"Failed after {retries} retries: {last_err}")


def _ffmpeg_bin() -> str:
    """Locate ffmpeg: env FFMPEG, PATH, then known fallback locations."""
    env = os.environ.get("FFMPEG")
    if env and Path(env).exists():
        return env
    for candidate in (
        "ffmpeg",
        r"C:\Program Files\Jellyfin\Server\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ):
        try:
            subprocess.run([candidate, "-version"], check=True, capture_output=True)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise RuntimeError("ffmpeg not found — set FFMPEG env var")


def concatenate(chunk_paths: list[Path], out_path: Path) -> None:
    ffmpeg = _ffmpeg_bin()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in chunk_paths:
            # ffmpeg concat format requires forward slashes, quoted
            escaped = str(p).replace("\\", "/").replace("'", r"'\''")
            f.write(f"file '{escaped}'\n")
        list_file = f.name
    try:
        subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0",
             "-i", list_file, "-c", "copy", str(out_path)],
            check=True, capture_output=True,
        )
    finally:
        os.unlink(list_file)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: azure_tts.py <input.txt> <output.mp3>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    raw = src.read_text(encoding="utf-8")
    cleaned = clean_script(raw)
    chunks = chunk_text(cleaned)
    print(f"Script: {len(cleaned)} chars -> {len(chunks)} chunks")

    work = Path(tempfile.mkdtemp(prefix="azure_tts_"))
    paths: list[Path] = []
    for i, chunk in enumerate(chunks, 1):
        p = work / f"chunk_{i:03d}.mp3"
        print(f"  [{i}/{len(chunks)}] {len(chunk)} chars -> {p.name}")
        synthesize_chunk(chunk, p)
        paths.append(p)

    print(f"Concatenating {len(paths)} chunks -> {dst}")
    concatenate(paths, dst)

    for p in paths:
        try:
            p.unlink()
        except OSError:
            pass
    try:
        work.rmdir()
    except OSError:
        pass

    size = dst.stat().st_size
    print(f"Done: {dst} ({size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
