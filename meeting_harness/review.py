"""Validate a complete discussion map before shortening it and choosing an agenda.

Exact quotes and complete segment accounting are mechanical checks, not proof that
all topics, responses or meanings have been captured. The separate coverage audit
is required before the brief can be rendered.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re


def artifact_hash(artifact: object) -> str:
    """Bind a later stage to the exact JSON content of an earlier stage."""
    encoded = json.dumps(artifact, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_UNSPECIFIED = object()


class _Check:
    def __init__(self):
        self.errors: list[str] = []
        self.segments: dict[str, dict] = {}
        self.speakers: set[str] = set()
        self.meta: dict = {}

    def obj(self, value, path, keys):
        if not isinstance(value, dict):
            self.errors.append(f"{path}: expected an object")
            return {}
        for key in sorted(set(keys) - value.keys()):
            self.errors.append(f"{path}.{key}: required field is missing")
        for key in sorted(value.keys() - set(keys), key=str):
            self.errors.append(f"{path}: unexpected field {key!r}")
        return value

    def string(self, value, path):
        if not isinstance(value, str) or not value.strip():
            self.errors.append(f"{path}: expected a nonempty string")
            return False
        return True

    def seq(self, value, path, minimum=0, maximum=None):
        if not isinstance(value, list):
            self.errors.append(f"{path}: expected an array")
            return []
        if len(value) < minimum or (maximum is not None and len(value) > maximum):
            limit = f"{minimum}–{maximum}" if maximum is not None else f"at least {minimum}"
            self.errors.append(f"{path}: expected {limit} items")
        return value

    def enum(self, value, path, options):
        if not isinstance(value, str) or value not in options:
            self.errors.append(f"{path}: expected one of {', '.join(options)}")

    def version(self, root, path, expected=2):
        if type(root.get("schema_version")) is not int or root.get("schema_version") != expected:
            self.errors.append(f"{path}.schema_version: expected integer {expected}")

    def digest(self, value, path):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            self.errors.append(f"{path}: expected a lowercase SHA-256 digest")

    def binding(self, value, artifact, path):
        self.digest(value, path)
        try:
            expected = artifact_hash(artifact)
        except (TypeError, ValueError):
            self.errors.append(f"{path}: referenced artifact is not valid JSON")
            return
        if value != expected:
            self.errors.append(f"{path}: must match the referenced artifact")

    def iso_date(self, value, path):
        if value is not None:
            try:
                if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except (ValueError, TypeError):
                self.errors.append(f"{path}: expected an ISO date (YYYY-MM-DD) or null")

    def meeting(self, value, path):
        meta = self.obj(value, path, {"title", "date", "source_sha256"})
        self.string(meta.get("title"), path + ".title")
        self.iso_date(meta.get("date"), path + ".date")
        self.digest(meta.get("source_sha256"), path + ".source_sha256")
        for key in ("title", "date", "source_sha256"):
            if key not in meta or meta.get(key) != self.meta.get(key):
                self.errors.append(f"{path}.{key}: must match the supplied source bundle")
        return meta

    def source(self, value):
        root = self.obj(value, "bundle", {"schema_version", "meeting", "segments", "warnings"})
        self.version(root, "bundle", 1)
        self.meta = self.obj(root.get("meeting"), "bundle.meeting",
                             {"title", "date", "date_basis", "source_sha256"})
        self.string(self.meta.get("title"), "bundle.meeting.title")
        self.string(self.meta.get("date_basis"), "bundle.meeting.date_basis")
        self.iso_date(self.meta.get("date"), "bundle.meeting.date")
        self.digest(self.meta.get("source_sha256"), "bundle.meeting.source_sha256")
        for i, warning in enumerate(self.seq(root.get("warnings"), "bundle.warnings")):
            self.string(warning, f"bundle.warnings[{i}]")
        for i, raw in enumerate(self.seq(root.get("segments"), "bundle.segments", 1)):
            path = f"bundle.segments[{i}]"
            segment = self.obj(raw, path, {"id", "speaker", "timestamp", "text"})
            sid = segment.get("id")
            if not isinstance(sid, str) or not re.fullmatch(r"S[0-9]{4,}", sid):
                self.errors.append(f"{path}.id: expected a source ID such as S0001")
            elif sid in self.segments:
                self.errors.append(f"{path}.id: duplicate source segment {sid!r}")
            else:
                self.segments[sid] = segment
            self.string(segment.get("text"), path + ".text")
            for key in ("speaker", "timestamp"):
                if segment.get(key) is not None:
                    self.string(segment.get(key), path + "." + key)
            speaker = segment.get("speaker")
            if isinstance(speaker, str) and speaker.strip():
                self.speakers.add(speaker)

    def name(self, name, path):
        if name is None:
            return None
        if self.string(name, path) and name not in self.speakers:
            self.errors.append(f"{path}: unknown named speaker {name!r}")
        return name if isinstance(name, str) else None

    def ids(self, value, path, allowed, minimum=0, ordered=False):
        result = []
        for i, sid in enumerate(self.seq(value, path, minimum)):
            p = f"{path}[{i}]"
            if self.string(sid, p):
                if sid not in allowed:
                    self.errors.append(f"{p}: unknown ID {sid!r}")
                if sid in result:
                    self.errors.append(f"{p}: duplicate ID {sid!r}")
                result.append(sid)
        if ordered:
            positions = {sid: i for i, sid in enumerate(allowed)}
            mapped = [positions[sid] for sid in result if sid in positions]
            if mapped != sorted(mapped):
                self.errors.append(f"{path}: IDs must follow source order")
        return result

    def evidence(self, value, path, scope, minimum=1, speaker=_UNSPECIFIED):
        owners = set()
        for i, raw in enumerate(self.seq(value, path, minimum)):
            p = f"{path}[{i}]"
            item = self.obj(raw, p, {"segment_id", "quote"})
            sid, quote = item.get("segment_id"), item.get("quote")
            valid_sid = self.string(sid, p + ".segment_id")
            valid_quote = self.string(quote, p + ".quote")
            segment = self.segments.get(sid) if valid_sid else None
            if valid_sid and segment is None:
                self.errors.append(f"{p}.segment_id: unknown source segment {sid!r}")
            if valid_sid and sid not in scope:
                self.errors.append(f"{p}.segment_id: evidence must be inside topic segment_ids")
            if segment is not None:
                text = segment.get("text")
                if valid_quote and (not isinstance(text, str) or quote not in text):
                    self.errors.append(f"{p}.quote: must be an exact substring of {sid}")
                owner = segment.get("speaker")
                if isinstance(owner, str) and owner in self.speakers:
                    owners.add(owner)
                if speaker is not _UNSPECIFIED and owner != speaker:
                    self.errors.append(f"{p}: evidence belongs to {owner!r}, not {speaker!r}")
        return owners

    def ordered_id(self, value, expected, path):
        if value != expected:
            self.errors.append(f"{path}: expected ordered ID {expected}")


def _dicts(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _list_ids(value):
    return [item.get("id") for item in _dicts(value)]


def validate_bundle(bundle: object) -> list[str]:
    """Validate a source bundle before constructing even the first stage prompt."""
    c = _Check()
    c.source(bundle)
    return c.errors


def validate_discussion(discussion: object, bundle: object) -> list[str]:
    """Check complete source accounting, chronology, ownership and discussion links."""
    c = _Check()
    c.source(bundle)
    root = c.obj(discussion, "discussion", {"schema_version", "meeting", "rounds", "topics",
                                            "excluded_segments", "limitations"})
    c.version(root, "discussion")
    c.meeting(root.get("meeting"), "discussion.meeting")
    rounds = c.seq(root.get("rounds"), "rounds", 1)
    topics = c.seq(root.get("topics"), "topics", 1)
    source_ids = list(c.segments)
    source_order = {sid: i for i, sid in enumerate(source_ids)}
    topic_ids = [f"T{i + 1}" for i in range(len(topics))]
    round_ids = [f"R{i + 1}" for i in range(len(rounds))]
    round_items = {}
    all_round_refs = []
    for i, raw in enumerate(rounds):
        path = f"rounds[{i}]"
        item = c.obj(raw, path, {"id", "presenter", "topic_ids"})
        rid = round_ids[i]
        c.ordered_id(item.get("id"), rid, path + ".id")
        c.name(item.get("presenter"), path + ".presenter")
        refs = c.ids(item.get("topic_ids"), path + ".topic_ids", topic_ids, 1, True)
        round_items[rid] = item
        all_round_refs.extend(refs)
    if all_round_refs != topic_ids:
        c.errors.append("rounds.topic_ids: must reference every topic exactly once in topic order")
    covered = set()
    first_positions = []
    for i, raw in enumerate(topics):
        path = f"topics[{i}]"
        item = c.obj(raw, path, {"id", "round_id", "presenter", "title", "segment_ids",
                                "presentation", "discussion", "outcome", "unresolved", "recorded_next_steps"})
        tid = topic_ids[i]
        c.ordered_id(item.get("id"), tid, path + ".id")
        c.string(item.get("title"), path + ".title")
        presenter = c.name(item.get("presenter"), path + ".presenter")
        rid = item.get("round_id")
        round_item = round_items.get(rid) if isinstance(rid, str) else None
        if round_item is None:
            c.errors.append(f"{path}.round_id: unknown round")
        else:
            refs = round_item.get("topic_ids")
            if not isinstance(refs, list) or tid not in refs:
                c.errors.append(f"{path}.round_id: round must reference this topic")
        mapped = c.ids(item.get("segment_ids"), path + ".segment_ids", source_ids, 1, True)
        scope = set(mapped)
        covered.update(scope)
        positions = [source_order[sid] for sid in scope if sid in source_order]
        if positions:
            first_positions.append(min(positions))
        presentation = c.obj(item.get("presentation"), path + ".presentation", {"summary", "evidence"})
        c.string(presentation.get("summary"), path + ".presentation.summary")
        c.evidence(presentation.get("evidence"), path + ".presentation.evidence", scope, speaker=presenter)
        earlier = set()
        comment_starts = {}
        last_comment_start = None
        for j, comment_raw in enumerate(c.seq(item.get("discussion"), path + ".discussion")):
            p = f"{path}.discussion[{j}]"
            comment = c.obj(comment_raw, p, {"id", "speaker", "kind", "reply_to", "summary", "evidence"})
            cid = f"{tid}.C{j + 1}"
            c.ordered_id(comment.get("id"), cid, p + ".id")
            name = c.name(comment.get("speaker"), p + ".speaker")
            c.enum(comment.get("kind"), p + ".kind", ("question", "suggestion", "challenge", "support", "clarification", "addition", "response"))
            target = comment.get("reply_to")
            if not isinstance(target, str) or target not in ({"presentation"} | earlier):
                c.errors.append(f"{p}.reply_to: must reference the presentation or an earlier comment in this topic")
            c.string(comment.get("summary"), p + ".summary")
            c.evidence(comment.get("evidence"), p + ".evidence", scope, speaker=name)
            starts = [source_order[ev["segment_id"]]
                      for ev in _dicts(comment.get("evidence"))
                      if isinstance(ev.get("segment_id"), str) and ev["segment_id"] in source_order]
            if starts:
                start = min(starts)
                if last_comment_start is not None and start < last_comment_start:
                    c.errors.append(f"{p}: comments must follow the chronological order of their earliest cited source segment")
                if isinstance(target, str) and target in comment_starts and start < comment_starts[target]:
                    c.errors.append(f"{p}.reply_to: cannot target a comment whose cited source begins later")
                comment_starts[cid] = start
                last_comment_start = start
            earlier.add(cid)
        outcome = c.obj(item.get("outcome"), path + ".outcome", {"status", "summary", "participants", "evidence"})
        status = outcome.get("status")
        c.enum(status, path + ".outcome.status", ("agreement", "partial_agreement", "answered", "open", "no_conclusion"))
        c.string(outcome.get("summary"), path + ".outcome.summary")
        participants = c.ids(outcome.get("participants"), path + ".outcome.participants", c.speakers)
        owners = c.evidence(outcome.get("evidence"), path + ".outcome.evidence", scope,
                            minimum=0 if status == "no_conclusion" else 1)
        if status in ("agreement", "partial_agreement"):
            if len(set(participants) & c.speakers) < 2:
                c.errors.append(f"{path}.outcome: agreement requires at least two distinct named participants")
            for name in sorted(set(participants) - owners):
                c.errors.append(f"{path}.outcome: no direct evidence from participant {name!r}")
            for name in sorted(owners - set(participants)):
                c.errors.append(f"{path}.outcome: evidence from unlisted participant {name!r}")
        elif participants:
            c.errors.append(f"{path}.outcome.participants: must be empty without an agreement status")
        for j, unresolved_raw in enumerate(c.seq(item.get("unresolved"), path + ".unresolved")):
            p = f"{path}.unresolved[{j}]"
            unresolved = c.obj(unresolved_raw, p, {"id", "kind", "summary", "positions", "evidence"})
            c.ordered_id(unresolved.get("id"), f"{tid}.U{j + 1}", p + ".id")
            c.enum(unresolved.get("kind"), p + ".kind", ("disagreement", "open_question"))
            c.string(unresolved.get("summary"), p + ".summary")
            c.evidence(unresolved.get("evidence"), p + ".evidence", scope)
            names = []
            for k, position_raw in enumerate(c.seq(unresolved.get("positions"), p + ".positions")):
                q = f"{p}.positions[{k}]"
                position = c.obj(position_raw, q, {"speaker", "view", "evidence"})
                name = c.name(position.get("speaker"), q + ".speaker")
                c.string(position.get("view"), q + ".view")
                c.evidence(position.get("evidence"), q + ".evidence", scope, speaker=name)
                if name is not None:
                    if name in names:
                        c.errors.append(f"{q}.speaker: duplicate position speaker {name!r}")
                    names.append(name)
            if unresolved.get("kind") == "disagreement" and len(set(names) & c.speakers) < 2:
                c.errors.append(f"{p}: disagreement requires at least two distinct named positions")
        for j, next_raw in enumerate(c.seq(item.get("recorded_next_steps"), path + ".recorded_next_steps")):
            p = f"{path}.recorded_next_steps[{j}]"
            step = c.obj(next_raw, p, {"id", "proposer", "proposal", "evidence"})
            c.ordered_id(step.get("id"), f"{tid}.N{j + 1}", p + ".id")
            name = c.name(step.get("proposer"), p + ".proposer")
            c.string(step.get("proposal"), p + ".proposal")
            c.evidence(step.get("evidence"), p + ".evidence", scope, speaker=name)
    if first_positions != sorted(first_positions):
        c.errors.append("topics: must be ordered chronologically by their first mapped source segment")
    excluded = set()
    for i, raw in enumerate(c.seq(root.get("excluded_segments"), "excluded_segments")):
        path = f"excluded_segments[{i}]"
        item = c.obj(raw, path, {"segment_ids", "reason", "note"})
        ids = c.ids(item.get("segment_ids"), path + ".segment_ids", source_ids, 1, True)
        for sid in ids:
            if sid in excluded:
                c.errors.append(f"{path}: segment {sid} is excluded more than once")
            if sid in covered:
                c.errors.append(f"{path}: segment {sid} cannot be both mapped and excluded")
            excluded.add(sid)
        c.enum(item.get("reason"), path + ".reason", ("metadata", "acknowledgment", "social", "technical_interruption", "unclear"))
        c.string(item.get("note"), path + ".note")
    for sid in source_ids:
        if sid not in covered | excluded:
            c.errors.append(f"discussion: source segment {sid} is not mapped or explicitly excluded")
    for i, limitation in enumerate(c.seq(root.get("limitations"), "limitations")):
        c.string(limitation, f"limitations[{i}]")
    return c.errors


def _stage(artifact, name, keys, bundle):
    c = _Check()
    c.source(bundle)
    root = c.obj(artifact, name, {"schema_version", "meeting"} | set(keys))
    c.version(root, name)
    c.meeting(root.get("meeting"), name + ".meeting")
    return c, root


def validate_coverage(coverage: object, discussion: object, bundle: object) -> list[str]:
    c, root = _stage(coverage, "coverage", {"discussion_sha256", "status", "checked_topic_ids", "checked_segment_ids", "issues"}, bundle)
    c.errors.extend(validate_discussion(discussion, bundle))
    c.binding(root.get("discussion_sha256"), discussion, "coverage.discussion_sha256")
    d = discussion if isinstance(discussion, dict) else {}
    tids = _list_ids(d.get("topics"))
    checked_topics = c.ids(root.get("checked_topic_ids"), "coverage.checked_topic_ids", tids)
    checked_segments = c.ids(root.get("checked_segment_ids"), "coverage.checked_segment_ids", c.segments)
    if checked_topics != tids:
        c.errors.append("coverage.checked_topic_ids: must check every topic in order")
    if checked_segments != list(c.segments):
        c.errors.append("coverage.checked_segment_ids: must check every source segment in order")
    c.enum(root.get("status"), "coverage.status", ("pass", "revise"))
    issues = c.seq(root.get("issues"), "coverage.issues")
    if root.get("status") == "pass" and issues:
        c.errors.append("coverage.issues: pass requires no issues")
    if root.get("status") == "revise" and not issues:
        c.errors.append("coverage.issues: revise requires at least one issue")
    for i, raw in enumerate(issues):
        path = f"coverage.issues[{i}]"
        item = c.obj(raw, path, {"kind", "topic_id", "segment_ids", "detail"})
        c.enum(item.get("kind"), path + ".kind", ("omitted_topic", "missing_comment", "missing_response", "wrong_owner", "wrong_link", "unsupported_outcome", "other"))
        tid = item.get("topic_id")
        if tid is not None and (not isinstance(tid, str) or tid not in tids):
            c.errors.append(f"{path}.topic_id: expected an existing topic ID or null")
        c.ids(item.get("segment_ids"), path + ".segment_ids", c.segments, 1, True)
        c.string(item.get("detail"), path + ".detail")
    return c.errors


def validate_brief(brief: object, discussion: object, coverage: object, bundle: object) -> list[str]:
    c, root = _stage(brief, "brief", {"discussion_sha256", "coverage_sha256", "topics"}, bundle)
    c.errors.extend(validate_coverage(coverage, discussion, bundle))
    c.binding(root.get("discussion_sha256"), discussion, "brief.discussion_sha256")
    c.binding(root.get("coverage_sha256"), coverage, "brief.coverage_sha256")
    if not isinstance(coverage, dict) or coverage.get("status") != "pass":
        c.errors.append("brief: coverage audit must pass before compression")
    _brief_body(c, root, discussion)
    return c.errors


def _brief_body(c, root, discussion):
    d = discussion if isinstance(discussion, dict) else {}
    source_topics = _dicts(d.get("topics"))
    topics = c.seq(root.get("topics"), "brief.topics", 1)
    if _list_ids(topics) != _list_ids(source_topics):
        c.errors.append("brief.topics: must preserve every topic ID in the same order")
    for i, raw in enumerate(topics):
        path = f"brief.topics[{i}]"
        item = c.obj(raw, path, {"id", "title", "presentation", "discussion", "outcome", "unresolved", "recorded_next_steps"})
        source = source_topics[i] if i < len(source_topics) else {}
        for field in ("id", "title", "presentation", "outcome"):
            c.string(item.get(field), path + "." + field)
        if item.get("title") != source.get("title"):
            c.errors.append(f"{path}.title: must preserve the approved discussion topic title")
        for field, text_field in (("discussion", "summary"), ("unresolved", "summary"), ("recorded_next_steps", "proposal")):
            values = c.seq(item.get(field), path + "." + field)
            if _list_ids(values) != _list_ids(source.get(field)):
                c.errors.append(f"{path}.{field}: must preserve every original ID in the same order")
            for j, value_raw in enumerate(values):
                p = f"{path}.{field}[{j}]"
                value = c.obj(value_raw, p, {"id", text_field})
                c.string(value.get("id"), p + ".id")
                c.string(value.get(text_field), p + "." + text_field)


def validate_agenda(agenda: object, discussion: object, brief: object, bundle: object) -> list[str]:
    c, root = _stage(agenda, "agenda", {"discussion_sha256", "brief_sha256", "items", "deferred_recorded_proposals"}, bundle)
    c.errors.extend(validate_discussion(discussion, bundle))
    c.binding(root.get("discussion_sha256"), discussion, "agenda.discussion_sha256")
    c.binding(root.get("brief_sha256"), brief, "agenda.brief_sha256")
    # Coverage is not an argument here. Validate the brief shape and discussion
    # binding without claiming to have checked its absent coverage artifact.
    b = c.obj(brief, "brief", {"schema_version", "meeting", "discussion_sha256", "coverage_sha256", "topics"})
    c.version(b, "brief")
    c.meeting(b.get("meeting"), "brief.meeting")
    c.binding(b.get("discussion_sha256"), discussion, "brief.discussion_sha256")
    c.digest(b.get("coverage_sha256"), "brief.coverage_sha256")
    _brief_body(c, b, discussion)
    d = discussion if isinstance(discussion, dict) else {}
    topics = _dicts(d.get("topics"))
    references = set()
    proposals = {}
    for topic in topics:
        tid = topic.get("id")
        if isinstance(tid, str):
            references.add(tid)
        for unresolved in _dicts(topic.get("unresolved")):
            if isinstance(unresolved.get("id"), str):
                references.add(unresolved["id"])
        for proposal in _dicts(topic.get("recorded_next_steps")):
            if isinstance(proposal.get("id"), str):
                proposals[proposal["id"]] = tid
    selected = set()
    synthetic_seen = False
    has_synthetic = False
    for i, raw in enumerate(c.seq(root.get("items"), "agenda.items", 1, 3)):
        path = f"agenda.items[{i}]"
        item = c.obj(raw, path, {"id", "kind", "title", "reason", "based_on", "recorded_proposal_id"})
        c.ordered_id(item.get("id"), f"A{i + 1}", path + ".id")
        c.enum(item.get("kind"), path + ".kind", ("meeting_proposal", "synthesizer_suggestion"))
        c.string(item.get("title"), path + ".title")
        c.string(item.get("reason"), path + ".reason")
        refs = c.ids(item.get("based_on"), path + ".based_on", references, 1)
        pid = item.get("recorded_proposal_id")
        if item.get("kind") == "meeting_proposal":
            if synthetic_seen:
                c.errors.append(f"{path}: recorded meeting proposals must precede derived suggestions")
            if not isinstance(pid, str) or pid not in proposals:
                c.errors.append(f"{path}.recorded_proposal_id: expected an original recorded proposal ID")
            else:
                if pid in selected:
                    c.errors.append(f"{path}.recorded_proposal_id: proposal selected more than once")
                selected.add(pid)
                if proposals[pid] not in refs:
                    c.errors.append(f"{path}.based_on: must include the recorded proposal's original topic")
        elif item.get("kind") == "synthesizer_suggestion":
            synthetic_seen = has_synthetic = True
            if pid is not None:
                c.errors.append(f"{path}.recorded_proposal_id: derived suggestion must use null")
    deferred = set()
    for i, raw in enumerate(c.seq(root.get("deferred_recorded_proposals"), "agenda.deferred_recorded_proposals")):
        path = f"agenda.deferred_recorded_proposals[{i}]"
        item = c.obj(raw, path, {"proposal_id", "reason"})
        pid = item.get("proposal_id")
        c.string(item.get("reason"), path + ".reason")
        if not isinstance(pid, str) or pid not in proposals:
            c.errors.append(f"{path}.proposal_id: expected an original recorded proposal ID")
        else:
            if pid in deferred:
                c.errors.append(f"{path}.proposal_id: proposal deferred more than once")
            if pid in selected:
                c.errors.append(f"{path}.proposal_id: proposal cannot be both selected and deferred")
            deferred.add(pid)
    for pid in sorted(set(proposals) - selected - deferred):
        c.errors.append(f"agenda: recorded proposal {pid} must be selected or explicitly deferred")
    if has_synthetic and deferred:
        c.errors.append("agenda: cannot add derived suggestions while recorded proposals are deferred")
    return c.errors


def _md(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>#!|()])", r"\\\1", value).replace("\r\n", "\n").replace("\r", "\n")


def render_markdown(discussion: dict, coverage: dict, brief: dict, agenda: dict, bundle: dict) -> str:
    """Render the full meeting first; agenda selection appears only afterwards."""
    errors = validate_brief(brief, discussion, coverage, bundle)
    errors.extend(validate_agenda(agenda, discussion, brief, bundle))
    if errors:
        raise ValueError("Artifacts failed validation:\n" + "\n".join(errors))
    segments = {item["id"]: item for item in bundle["segments"]}
    citations = []
    numbers = {}

    def line(value):
        return " ".join(_md(value).splitlines())

    def cite(evidence):
        labels = []
        for item in evidence:
            key = (item["segment_id"], item["quote"])
            if key not in numbers:
                citations.append(key)
                numbers[key] = len(citations)
            label = f"E{numbers[key]}"
            if label not in labels:
                labels.append(label)
        return " ".join(f"[{label}]" for label in labels)

    topic_by_id = {item["id"]: item for item in discussion["topics"]}
    brief_by_id = {item["id"]: item for item in brief["topics"]}
    proposal_by_id = {item["id"]: item for topic in discussion["topics"] for item in topic["recorded_next_steps"]}
    kinds = {"question": "提问", "suggestion": "建议", "challenge": "质疑", "support": "支持", "clarification": "澄清", "addition": "补充", "response": "回应"}
    statuses = {"agreement": "形成共识", "partial_agreement": "局部共识", "answered": "问题得到回应", "open": "仍待讨论", "no_conclusion": "未形成结论"}
    rows = [f"# {line(discussion['meeting']['title'])}", "", f"会议日期：{discussion['meeting']['date'] or '未记录'}", "", "## 完整会议讨论", ""]
    for round_item in discussion["rounds"]:
        rows.extend([f"### {line(round_item['presenter'] or '未标注发言人')} 的分享", ""])
        for tid in round_item["topic_ids"]:
            topic, short = topic_by_id[tid], brief_by_id[tid]
            rows.extend([f"#### {line(short['title'])}", "", f"**分享内容（{line(topic['presenter'] or '未标注发言人')}）：** {line(short['presentation'])} {cite(topic['presentation']['evidence'])}", "", "**评论与回应：**", ""])
            comments_by_id = {comment["id"]: comment for comment in topic["discussion"]}
            for original, compact in zip(topic["discussion"], short["discussion"]):
                if original["reply_to"] == "presentation":
                    target = line(topic["presenter"] or "未标注发言人") + " 的分享"
                else:
                    replied = comments_by_id[original["reply_to"]]
                    target = line(replied["speaker"] or "未标注发言人") + "的" + kinds[replied["kind"]]
                rows.append(f"- **{line(original['speaker'] or '未标注发言人')} · {kinds[original['kind']]}（针对 {target}）：** {line(compact['summary'])} {cite(original['evidence'])}")
            if not topic["discussion"]:
                rows.append("未记录针对本话题的评论。")
            outcome = topic["outcome"]
            rows.extend(["", f"**讨论结果 · {statuses[outcome['status']]}：** {line(short['outcome'])} {cite(outcome['evidence'])}".rstrip()])
            if outcome["participants"]:
                rows.append("有直接依据的共识参与者：" + "、".join(line(name) for name in outcome["participants"]) + "。")
            if topic["unresolved"]:
                rows.extend(["", "**分歧与开放问题：**", ""])
            for original, compact in zip(topic["unresolved"], short["unresolved"]):
                kind = "未解决分歧" if original["kind"] == "disagreement" else "开放问题"
                rows.append(f"- **{kind}：** {line(compact['summary'])} {cite(original['evidence'])}")
                for position in original["positions"]:
                    rows.append(f"  - **{line(position['speaker'] or '未标注发言人')}：** {line(position['view'])} {cite(position['evidence'])}")
            if topic["recorded_next_steps"]:
                rows.extend(["", "**会上提出的后续建议：**", ""])
            for original, compact in zip(topic["recorded_next_steps"], short["recorded_next_steps"]):
                rows.append(f"- **{line(original['proposer'] or '未标注发言人')}：** {line(compact['proposal'])} {cite(original['evidence'])}")
            rows.append("")
    rows.extend(["## 下次讨论议题（完成本次总结后提炼）", "", "以下区分会上已有提议与总结者延伸建议；议题入选不代表会议已作出决定。", ""])
    for item in agenda["items"]:
        label = "会上已有提议" if item["kind"] == "meeting_proposal" else "总结者延伸建议"
        original_proposal = proposal_by_id.get(item["recorded_proposal_id"])
        attribution = ""
        evidence = ""
        if original_proposal:
            attribution = f"（原提议者：{line(original_proposal['proposer'] or '未标注发言人')}）"
            evidence = cite(original_proposal["evidence"])
        rows.append(f"- **{label}：{line(item['title'])}** {line(item['reason'])}{attribution} {evidence}".rstrip())
    if agenda["deferred_recorded_proposals"]:
        rows.extend(["", "**未入选的会上提议及原因：**", ""])
        for item in agenda["deferred_recorded_proposals"]:
            proposal = proposal_by_id[item["proposal_id"]]
            rows.append(f"- **{line(proposal['proposal'])}：** {line(item['reason'])} {cite(proposal['evidence'])}")
    rows.extend(["", "## 原文依据", ""])
    for i, (sid, quote) in enumerate(citations, 1):
        segment = segments[sid]
        timestamp = f" · {line(segment['timestamp'])}" if segment["timestamp"] else ""
        rows.extend([f"**E{i} · {sid} · {line(segment['speaker'] or '未标注发言人')}{timestamp}**", ""])
        rows.extend("> " + _md(part) for part in quote.splitlines())
        rows.append("")
    rows.extend(["## 覆盖检查与局限", "", "- 已完成独立覆盖复核，并记录所有原文片段的归属或排除原因。", "- 自动检查可以核对结构、原文摘录、发言归属和前后版本关联；不能证明所有话题均已识别，也不能证明摘录支持总结的含义。覆盖复核仍需逐段判断。"])
    rows.extend("- " + line(value) for value in discussion["limitations"])
    if discussion["excluded_segments"]:
        rows.extend(["", "**排除片段记录：**", ""])
        for item in discussion["excluded_segments"]:
            rows.append(f"- {', '.join(item['segment_ids'])} · {item['reason']}：{line(item['note'])}")
    rows.extend(["", f"Source SHA-256: `{discussion['meeting']['source_sha256']}`", ""])
    return "\n".join(rows)
