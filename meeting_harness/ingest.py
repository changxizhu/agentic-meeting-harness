"""Local ingestion. No network, macros, or document instructions are executed."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


class HarnessError(ValueError):
    """An actionable input or workflow failure safe to show on the console."""


SUPPORTED = {".docx", ".md", ".txt"}
# Reports live beside their source in a dated meeting folder. They must never
# compete with that source when choosing the latest meeting.
GENERATED_REPORT_NAMES = {"summary.md", "digest.md"}
MAX_SOURCE_BYTES = 20_000_000
MAX_XML_BYTES = 40_000_000
NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_paragraphs(path: Path) -> list[str]:
    if path.suffix.lower() not in SUPPORTED:
        raise HarnessError("Supported source formats: .docx, .md, .txt")
    if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise HarnessError("Source missing or larger than the 20 MB source limit.")
    try:
        if path.suffix.lower() == ".docx":
            with ZipFile(path) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > MAX_XML_BYTES:
                    raise HarnessError("Document XML exceeds the extraction limit.")
                data = archive.read(info)
            if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
                raise HarnessError("Document contains unsupported XML entities.")
            root = ET.fromstring(data)
            paragraphs = []
            for para in root.iter(NS + "p"):
                parts = []
                for node in para.iter():
                    if node.tag == NS + "t":
                        parts.append(node.text or "")
                    elif node.tag == NS + "tab":
                        parts.append("\t")
                    elif node.tag in {NS + "br", NS + "cr"}:
                        parts.append("\n")
                # Soft breaks often separate a Teams speaker heading from speech.
                # Keep the paragraph intact so attribution cannot be detached.
                paragraphs.append("".join(parts))
            return [p.strip() for p in paragraphs if p.strip()]
        return [p.strip() for p in path.read_text(encoding="utf-8-sig").splitlines() if p.strip()]
    except (BadZipFile, KeyError, ET.ParseError, UnicodeError, OSError) as exc:
        raise HarnessError("Cannot read source as the selected document format.") from exc


def _dates(text: str) -> set[str]:
    found: set[str] = set()
    for pattern in (r"(?<!\d)(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})(?:日)?(?!\d)",
                    r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"):
        for match in re.finditer(pattern, text):
            try:
                found.add(date(*map(int, match.groups())).isoformat())
            except ValueError:
                pass
    months = "January February March April May June July August September October November December"
    pattern = rf"\b({'|'.join(months.split())})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b"
    for match in re.finditer(pattern, text, re.I):
        try:
            found.add(datetime.strptime(" ".join(match.groups()), "%B %d %Y").date().isoformat())
        except ValueError:
            pass
    return found


def meeting_date(path: Path, paragraphs: list[str], override: str | None = None) -> tuple[str | None, str]:
    if override:
        try:
            return date.fromisoformat(override).isoformat(), "explicit_override"
        except ValueError as exc:
            raise HarnessError("Meeting date must be YYYY-MM-DD.") from exc
    # Header only: dates mentioned later in discussion must not change selection.
    header = "\n".join(paragraphs[:4])
    candidates = _dates(path.stem) | _dates(header)
    if len(candidates) > 1:
        raise HarnessError("Conflicting header/filename dates; select a source and pass --meeting-date.")
    if candidates:
        return candidates.pop(), "header_or_filename"
    return None, "not_recorded"


def latest_source(folder: Path) -> Path:
    if not folder.is_dir():
        raise HarnessError("Meetings folder does not exist.")
    candidates = sorted(p for p in folder.rglob("*") if p.is_file()
                        and p.suffix.lower() in SUPPORTED
                        and p.name.lower() not in GENERATED_REPORT_NAMES
                        and not any(part.startswith(".") for part in p.relative_to(folder).parts)
                        and not p.name.startswith("~$"))
    if not candidates:
        raise HarnessError("No supported meeting files found.")
    dated = []
    for path in candidates:
        d, _ = meeting_date(path, read_paragraphs(path))
        if d is None:
            raise HarnessError("An undated meeting prevents reliable latest selection; use --source.")
        dated.append((d, path))
    newest = max(d for d, _ in dated)
    winners = [p for d, p in dated if d == newest]
    if len(winners) != 1:
        raise HarnessError("Multiple sources share the latest meeting date; choose one with --source.")
    return winners[0]


def _split_speaker(text: str) -> tuple[str | None, str | None, str]:
    # Teams/Zoom export: speaker, whitespace, timestamp, optional whitespace, speech.
    match = re.match(r"^(.+?)\s+(\d{1,2}:\d{2}(?::\d{2})?)(?![\d:A-Za-z])\s*(.*)$", text, re.S)
    if match and match.group(3):
        return match.group(1).strip(), match.group(2), match.group(3).strip()
    match = re.match(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+([^:：]+)[:：]\s*(.+)$", text, re.S)
    if match:
        return match.group(2).strip(), match.group(1), match.group(3).strip()
    match = re.match(r"^([^:：]{1,80})[:：]\s+(.+)$", text, re.S)
    if match and match.group(1).lower() not in {"title", "date", "meeting", "duration", "source"}:
        return match.group(1).strip(), None, match.group(2).strip()
    return None, None, text


def make_bundle(path: Path, override: str | None = None) -> dict:
    paragraphs = read_paragraphs(path)
    if not paragraphs:
        raise HarnessError("The meeting source contains no readable text.")
    d, basis = meeting_date(path, paragraphs, override)
    segments = []
    for i, para in enumerate(paragraphs, 1):
        speaker, timestamp, text = _split_speaker(para)
        segments.append({"id": f"S{i:04d}", "speaker": speaker, "timestamp": timestamp, "text": text})
    warnings = ["Speaker labels come from the transcript; identity and transcription errors need human review."]
    if d is None:
        warnings.append("Meeting date not recorded; this source was selected explicitly.")
    if not any(s["speaker"] for s in segments):
        warnings.append("No named speaker labels found; individual attribution may be unavailable.")
    return {"schema_version": 1, "meeting": {"title": paragraphs[0], "date": d,
            "date_basis": basis, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
            "segments": segments, "warnings": warnings}
