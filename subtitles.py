from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SubtitleSegment:
    start: float
    end: float
    text: str
    emphasis: str = ""


def build_segments_from_script(script: list[dict]) -> list[SubtitleSegment]:
    segments = []
    for item in script:
        text = clean_text(item["text"])
        if not text:
            continue
        segments.append(
            SubtitleSegment(
                start=float(item["start"]),
                end=float(item["end"]),
                text=text,
                emphasis=pick_emphasis(text),
            )
        )
    return segments


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def pick_emphasis(text: str) -> str:
    words = [word.strip(".,!?;:").upper() for word in text.split()]
    candidates = [word for word in words if len(word) >= 5]
    return candidates[0] if candidates else ""


def write_srt(segments: list[SubtitleSegment], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for index, segment in enumerate(segments, start=1):
        lines.extend(
            [
                str(index),
                f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}",
                segment.text,
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def format_srt_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"
