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


def clean_script(text: str) -> str:
    """Strip markdown/section headers, keep only readable sentences."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        # Drop pure markdown dividers and hash headers
        if re.match(r"^[-=#*_]{3,}$", stripped):
            continue
        # Strip leading markdown markers but keep content
        stripped = re.sub(r"^#+\s*", "", stripped)
        stripped = re.sub(r"^\*+\s*", "", stripped)
        stripped = re.sub(r"^-\s+", "", stripped)
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


def concatenate(chunk_paths: list[Path], out_path: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in chunk_paths:
            # ffmpeg concat format requires forward slashes, quoted
            escaped = str(p).replace("\\", "/").replace("'", r"'\''")
            f.write(f"file '{escaped}'\n")
        list_file = f.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
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
