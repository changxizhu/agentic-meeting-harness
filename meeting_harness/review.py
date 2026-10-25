"""Check source traceability and render a concise, cited meeting brief.

These checks cannot determine whether a quotation logically supports a claim.
That remains a separate model or human review step.
"""

from __future__ import annotations

from datetime import date
import re


def validate_summary(summary: object, bundle: object) -> list[str]:
    """Return all detected contract errors, including safe handling of bad types."""
    errors: list[str] = []

    def obj(value: object, path: str, keys: set[str]) -> dict:
        if not isinstance(value, dict):
            errors.append(f"{path}: expected an object")
            return {}
        for key in sorted(keys - value.keys()):
            errors.append(f"{path}.{key}: required field is missing")
        for key in sorted(value.keys() - keys, key=str):
            errors.append(f"{path}: unexpected field {key!r}")
        return value

    def string(value: object, path: str) -> bool:
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{path}: expected a nonempty string")
            return False
        return True

    def seq(value: object, path: str, minimum: int = 0, maximum: int | None = None) -> list:
        if not isinstance(value, list):
            errors.append(f"{path}: expected an array")
            return []
        if len(value) < minimum or (maximum is not None and len(value) > maximum):
            limit = f"{minimum}–{maximum}" if maximum is not None else f"at least {minimum}"
            errors.append(f"{path}: expected {limit} items")
        return value

    def iso_date(value: object, path: str) -> None:
        if value is not None:
            try:
                if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except (ValueError, TypeError):
                errors.append(f"{path}: expected an ISO date (YYYY-MM-DD) or null")

    def sha256(value: object, path: str) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            errors.append(f"{path}: expected a lowercase SHA-256 digest")

    source = obj(bundle, "bundle", {"schema_version", "meeting", "segments", "warnings"})
    if type(source.get("schema_version")) is not int or source.get("schema_version") != 1:
        errors.append("bundle.schema_version: expected integer 1")
    source_meta = obj(source.get("meeting"), "bundle.meeting",
                      {"title", "date", "date_basis", "source_sha256"})
    string(source_meta.get("title"), "bundle.meeting.title")
    string(source_meta.get("date_basis"), "bundle.meeting.date_basis")
    iso_date(source_meta.get("date"), "bundle.meeting.date")
    sha256(source_meta.get("source_sha256"), "bundle.meeting.source_sha256")
    for i, warning in enumerate(seq(source.get("warnings"), "bundle.warnings")):
        string(warning, f"bundle.warnings[{i}]")
    segments: dict[str, dict] = {}
    for i, raw in enumerate(seq(source.get("segments"), "bundle.segments", 1)):
        path = f"bundle.segments[{i}]"
        segment = obj(raw, path, {"id", "speaker", "timestamp", "text"})
        sid = segment.get("id")
        valid_sid = isinstance(sid, str) and re.fullmatch(r"S[0-9]{4,}", sid)
        if not valid_sid:
            errors.append(f"{path}.id: expected a source ID such as S0001")
        elif sid in segments:
            errors.append(f"{path}.id: duplicate source segment {sid!r}")
        else:
            segments[sid] = segment
        string(segment.get("text"), path + ".text")
        for key in ("speaker", "timestamp"):
            if segment.get(key) is not None:
                string(segment.get(key), path + "." + key)
    speakers = {s["speaker"] for s in segments.values()
                if isinstance(s.get("speaker"), str) and s["speaker"].strip()}

    def evidence(value: object, path: str, minimum: int = 1,
                 speaker: str | None = None) -> set[str]:
        owners: set[str] = set()
        for i, raw in enumerate(seq(value, path, minimum)):
            p = f"{path}[{i}]"
            ev = obj(raw, p, {"segment_id", "quote"})
            sid, quote = ev.get("segment_id"), ev.get("quote")
            valid_sid = string(sid, p + ".segment_id")
            valid_quote = string(quote, p + ".quote")
            segment = segments.get(sid) if valid_sid else None
            if valid_sid and segment is None:
                errors.append(f"{p}.segment_id: unknown source segment {sid!r}")
            if segment is not None:
                source_text = segment.get("text")
                if valid_quote and (not isinstance(source_text, str) or quote not in source_text):
                    errors.append(f"{p}.quote: must be an exact substring of {sid}")
                owner = segment.get("speaker")
                if isinstance(owner, str) and owner in speakers:
                    owners.add(owner)
                if speaker is not None and owner != speaker:
                    errors.append(f"{p}: evidence belongs to {owner!r}, not {speaker!r}")
        return owners

    def view(raw: object, path: str) -> str | None:
        item = obj(raw, path, {"speaker", "view", "evidence"})
        name = item.get("speaker")
        valid_name = string(name, path + ".speaker")
        if valid_name and name not in speakers:
            errors.append(f"{path}.speaker: unknown named speaker {name!r}")
        string(item.get("view"), path + ".view")
        evidence(item.get("evidence"), path + ".evidence", speaker=name if valid_name else None)
        return name if valid_name else None

    root = obj(summary, "summary", {"schema_version", "meeting", "core_points",
        "participant_views", "consensus", "unresolved", "next_discussion", "limitations"})
    if type(root.get("schema_version")) is not int or root.get("schema_version") != 1:
        errors.append("schema_version: expected integer 1")
    meta = obj(root.get("meeting"), "meeting", {"title", "date", "source_sha256"})
    string(meta.get("title"), "meeting.title")
    sha256(meta.get("source_sha256"), "meeting.source_sha256")
    iso_date(meta.get("date"), "meeting.date")
    for key in ("title", "date", "source_sha256"):
        if key not in meta or meta.get(key) != source_meta.get(key):
            errors.append(f"meeting.{key}: must match the supplied source bundle")

    core_ids: set[str] = set()
    for i, raw in enumerate(seq(root.get("core_points"), "core_points", 1, 6)):
        path = f"core_points[{i}]"
        item = obj(raw, path, {"id", "point", "evidence"})
        expected_id = f"C{i + 1}"
        if item.get("id") != expected_id:
            errors.append(f"{path}.id: expected ordered ID {expected_id}")
        else:
            core_ids.add(expected_id)
        string(item.get("point"), path + ".point")
        evidence(item.get("evidence"), path + ".evidence")

    covered: set[str] = set()
    for i, raw in enumerate(seq(root.get("participant_views"), "participant_views")):
        name = view(raw, f"participant_views[{i}]")
        if name:
            if name in covered:
                errors.append(f"participant_views[{i}]: duplicate speaker {name!r}")
            covered.add(name)
    for missing in sorted(speakers - covered):
        errors.append(f"participant_views: missing named speaker {missing!r}")

    agreement = obj(root.get("consensus"), "consensus", {"status", "summary", "evidence", "participants"})
    status = agreement.get("status")
    if status not in ("explicit", "partial", "none_recorded"):
        errors.append("consensus.status: expected explicit, partial, or none_recorded")
    string(agreement.get("summary"), "consensus.summary")
    participants: set[str] = set()
    for i, name in enumerate(seq(agreement.get("participants"), "consensus.participants")):
        if string(name, f"consensus.participants[{i}]"):
            if name not in speakers:
                errors.append(f"consensus.participants[{i}]: unknown named speaker {name!r}")
            if name in participants:
                errors.append(f"consensus.participants[{i}]: duplicate speaker {name!r}")
            participants.add(name)
    owners = evidence(agreement.get("evidence"), "consensus.evidence",
                      minimum=1 if status in ("explicit", "partial") else 0)
    if status in ("explicit", "partial"):
        if len(participants) < 2:
            errors.append("consensus: agreement requires at least two distinct named participants")
        for name in sorted(participants - owners):
            errors.append(f"consensus: no direct evidence from participant {name!r}")
        for name in sorted(owners - participants):
            errors.append(f"consensus: evidence from unlisted participant {name!r}")
    elif status == "none_recorded" and participants:
        errors.append("consensus.participants: must be empty for none_recorded")

    unresolved = seq(root.get("unresolved"), "unresolved")
    for i, raw in enumerate(unresolved):
        path = f"unresolved[{i}]"
        item = obj(raw, path, {"issue", "status", "positions", "evidence"})
        string(item.get("issue"), path + ".issue")
        issue_status = item.get("status")
        if issue_status not in ("disagreement", "open_question"):
            errors.append(f"{path}.status: expected disagreement or open_question")
        evidence(item.get("evidence"), path + ".evidence")
        position_names: set[str] = set()
        for j, raw_position in enumerate(seq(item.get("positions"), path + ".positions")):
            name = view(raw_position, f"{path}.positions[{j}]")
            if name:
                if name in position_names:
                    errors.append(f"{path}.positions[{j}]: duplicate speaker {name!r}")
                position_names.add(name)
        if issue_status == "disagreement" and len(position_names & speakers) < 2:
            errors.append(f"{path}: disagreement requires at least two distinct named positions")

    references = core_ids | {f"U{i + 1}" for i in range(len(unresolved))}
    for i, raw in enumerate(seq(root.get("next_discussion"), "next_discussion", 1, 3)):
        path = f"next_discussion[{i}]"
        item = obj(raw, path, {"question", "why", "based_on", "kind"})
        string(item.get("question"), path + ".question")
        string(item.get("why"), path + ".why")
        if item.get("kind") != "proposal":
            errors.append(f"{path}.kind: expected proposal")
        for j, ref in enumerate(seq(item.get("based_on"), path + ".based_on", 1)):
            if string(ref, f"{path}.based_on[{j}]") and ref not in references:
                errors.append(f"{path}.based_on[{j}]: unknown point or unresolved item {ref!r}")
    for i, limitation in enumerate(seq(root.get("limitations"), "limitations")):
        string(limitation, f"limitations[{i}]")
    return errors


def _md(value: str) -> str:
    """Display source/model prose as text rather than executable Markdown/HTML."""
    return re.sub(r"([\\`*_{}\[\]<>#!|()])", r"\\\1", value).replace("\r\n", "\n").replace("\r", "\n")


def render_markdown(summary: dict, bundle: dict) -> str:
    """Render only a validated brief, with original snippets in an appendix."""
    errors = validate_summary(summary, bundle)
    if errors:
        raise ValueError("Summary failed validation:\n" + "\n".join(errors))
    segments = {segment["id"]: segment for segment in bundle["segments"]}
    citations: list[tuple[str, str]] = []
    numbers: dict[tuple[str, str], int] = {}

    def cite(items: list[dict]) -> str:
        labels = []
        for item in items:
            key = (item["segment_id"], item["quote"])
            if key not in numbers:
                citations.append(key)
                numbers[key] = len(citations)
            label = f"E{numbers[key]}"
            if label not in labels:
                labels.append(label)
        return " ".join(f"[{label}]" for label in labels)

    def line(value: str) -> str:
        return " ".join(_md(value).splitlines())

    meta = summary["meeting"]
    rows = [f"# {line(meta['title'])}", "", f"Meeting date: {meta['date'] or 'not recorded'}", "",
            "## Core discussion", ""]
    for item in summary["core_points"]:
        rows.append(f"- **{item['id']}** {line(item['point'])} {cite(item['evidence'])}")
    rows.extend(["", "## Participant views (supporting evidence)", ""])
    for item in summary["participant_views"]:
        rows.append(f"- **{line(item['speaker'])}:** {line(item['view'])} {cite(item['evidence'])}")
    if not summary["participant_views"]:
        rows.append("No named speakers were identified in the supplied material.")
    agreement = summary["consensus"]
    labels = {"explicit": "Explicit agreement", "partial": "Partial agreement", "none_recorded": "No agreement recorded"}
    rows.extend(["", "## Agreement", "", f"**{labels[agreement['status']]}:** {line(agreement['summary'])} {cite(agreement['evidence'])}".rstrip()])
    if agreement["participants"]:
        rows.append("Participants evidenced as agreeing: " + ", ".join(line(p) for p in agreement["participants"]) + ".")
    rows.extend(["", "## Unresolved differences and questions", ""])
    for i, item in enumerate(summary["unresolved"], 1):
        kind = "Disagreement" if item["status"] == "disagreement" else "Open question"
        rows.append(f"- **U{i} — {kind}:** {line(item['issue'])} {cite(item['evidence'])}")
        for position in item["positions"]:
            rows.append(f"  - **{line(position['speaker'])}:** {line(position['view'])} {cite(position['evidence'])}")
    if not summary["unresolved"]:
        rows.append("No unresolved disagreement or open question was recorded in the supplied material.")
    rows.extend(["", "## For next discussion", "", "Suggested questions; these are proposals, not recorded decisions.", ""])
    for item in summary["next_discussion"]:
        rows.append(f"- **{line(item['question'])}** {line(item['why'])} (Based on {', '.join(item['based_on'])}.)")
    rows.extend(["", "## Evidence appendix", ""])
    for i, (sid, quote) in enumerate(citations, 1):
        segment = segments[sid]
        attribution = line(segment.get("speaker") or "Unattributed")
        timestamp = f" · {line(segment['timestamp'])}" if segment.get("timestamp") else ""
        rows.extend([f"**E{i} · {sid} · {attribution}{timestamp}**", ""])
        rows.extend("> " + _md(part) for part in quote.splitlines())
        rows.append("")
    rows.extend(["## Limitations and review", ""])
    rows.extend("- " + line(item) for item in summary["limitations"])
    rows.extend(["- Automated checks verify structure, exact quotations, speaker attribution, and source binding. They do not verify whether each quotation supports the meaning of the summary; review that separately.",
                 "", f"Source SHA-256: `{meta['source_sha256']}`", ""])
    return "\n".join(rows)
