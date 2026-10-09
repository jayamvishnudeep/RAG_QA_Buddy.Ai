"""Meeting notes and transcripts.

Transcripts (Teams/Zoom .vtt, .srt, or text with "Name: ..." lines) are cut at
speaker turns: turns are packed into chunks of about 450 tokens, and the last
turn or two repeat at the start of the next chunk so an answer split across a
boundary keeps its question. Notes without speakers are chunked like any other
document, by heading.
"""

from __future__ import annotations

import re

from ..chunking import pack_tagged, split_text
from ..models import Chunk
from ..text import mask_secrets, normalize
from . import documents
from .base import LoadContext, Unsupported, read_text

SUPPORTED = {".vtt", ".srt", ".txt", ".md", ".markdown", ".pdf", ".docx"}

_TIMING = re.compile(r"^((?:\d{1,2}:)?\d{1,2}:\d{2})[.,]\d{1,3}\s*-->\s*")
_VOICE = re.compile(r"<v(?:\.[^ >]+)?\s+([^>]+)>")
_TAG = re.compile(r"</?[^>]+>")
_NAME = r"[A-Z][\w.'\- ]{0,38}[\w.]"
_SPEAKER_LINES = [
    # [00:01:02] Alice Smith: text      or      00:01 - Alice: text
    re.compile(rf"^\[?(?P<ts>\d{{1,2}}:\d{{2}}(?::\d{{2}})?)\]?\s*[-–]?\s*(?P<who>{_NAME}):\s+(?P<text>.+)$"),
    # Alice Smith (00:01:02): text      or      Alice Smith  00:01:02
    re.compile(rf"^(?P<who>{_NAME})\s*[\(\[]?(?P<ts>\d{{1,2}}:\d{{2}}(?::\d{{2}})?)[\)\]]?\s*:?\s*(?P<text>.*)$"),
    # Alice: text
    re.compile(rf"^(?P<who>{_NAME}):\s+(?P<text>.+)$"),
]
_DATE = re.compile(r"(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])")
_NOTES_HEADING = re.compile(r"^[A-Z][\w /&()\-]{1,40}:$")


def load(ctx: LoadContext) -> list[Chunk]:
    suffix = ctx.path.suffix.lower()
    if suffix not in SUPPORTED:
        raise Unsupported(f"{suffix} is not a transcript or notes format")
    title = _title(ctx)
    if suffix in (".pdf", ".docx"):
        _, blocks, kind = documents.parse(ctx.path)
        return documents.chunk_blocks(ctx, blocks, title, ctx.source.chunking, kind=kind, source_type="meeting")
    text = normalize(read_text(ctx.path))
    if suffix == ".vtt":
        turns = _cues(text, vtt=True)
    elif suffix == ".srt":
        turns = _cues(text, vtt=False)
    else:
        turns = _speaker_turns(text)
    if not turns:  # meeting notes, not a transcript
        blocks = documents.markdown_blocks(text) if suffix in (".md", ".markdown") else documents.text_blocks(text)
        return documents.chunk_blocks(ctx, blocks, title, ctx.source.chunking, kind="notes", source_type="meeting")
    return _turn_chunks(ctx, title, turns)


def _title(ctx: LoadContext) -> str:
    stem = ctx.path.stem.replace("_", " ").strip()
    date = _DATE.search(ctx.path.stem)
    label = f"Meeting: {stem}"
    if date:
        iso = "-".join(date.groups())
        if iso not in stem:
            label += f" ({iso})"
    return label


def _cues(text: str, vtt: bool) -> list[tuple[str, str, str]]:
    """(timestamp, speaker, text) per cue, with consecutive cues of one speaker merged."""
    turns: list[tuple[str, str, str]] = []
    timestamp = ""
    for block in re.split(r"\n\s*\n", text):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE")):
            continue
        timing_index = next((i for i, l in enumerate(lines) if _TIMING.match(l)), None)
        if timing_index is None:
            continue
        timestamp = _TIMING.match(lines[timing_index]).group(1)
        content = " ".join(lines[timing_index + 1 :])
        voice = _VOICE.search(content) if vtt else None
        speaker = voice.group(1).strip() if voice else ""
        content = _TAG.sub("", content).strip()
        if not speaker:
            match = re.match(rf"^({_NAME}):\s+(.+)$", content)
            if match:
                speaker, content = match.group(1), match.group(2)
        if not content:
            continue
        if turns and turns[-1][1] == speaker:
            ts, who, previous = turns[-1]
            turns[-1] = (ts, who, f"{previous} {content}")
        else:
            turns.append((timestamp, speaker, content))
    return turns


def _speaker_turns(text: str) -> list[tuple[str, str, str]]:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    turns: list[tuple[str, str, str]] = []
    matched = 0
    for line in lines:
        if _NOTES_HEADING.match(line):  # "Decisions:", "Action items:" belong to nobody
            turns.append(("", "", line))
            continue
        for pattern in _SPEAKER_LINES:
            m = pattern.match(line)
            if m and len(m.group("who").split()) <= 4:
                matched += 1
                turns.append((m.groupdict().get("ts") or "", m.group("who").strip(), m.group("text").strip()))
                break
        else:
            if turns:  # continuation of the previous turn; list items keep their own line
                ts, who, previous = turns[-1]
                separator = "\n" if not who or re.match(r"^([-*•]|\d+[.)])\s", line) else " "
                turns[-1] = (ts, who, f"{previous}{separator}{line}")
    # Real transcripts are mostly speaker lines; notes have only a few "Attendees:"-style lines.
    if matched < 4 or matched < 0.3 * len(lines):
        return []
    return [t for t in turns if t[2]]


def _turn_chunks(ctx: LoadContext, title: str, turns: list[tuple[str, str, str]]) -> list[Chunk]:
    chunking = ctx.source.chunking
    units = []
    for ts, who, said in turns:
        line = f"{f'[{ts}] ' if ts else ''}{who + ': ' if who else ''}{mask_secrets(said)}"
        for part in split_text(line, chunking.max_tokens, chunking.overlap_tokens):
            units.append((part, ts))
    chunks = []
    for text, stamps in pack_tagged(units, chunking.max_tokens, chunking.overlap_tokens, joiner="\n"):
        stamps = [s for s in stamps if s]
        location = f"{stamps[0]}-{stamps[-1]}" if stamps else ""
        chunks.append(
            ctx.chunk(f"{title}\n\n{text}", title=title, location=location, source_type="meeting", kind="transcript")
        )
    return chunks
