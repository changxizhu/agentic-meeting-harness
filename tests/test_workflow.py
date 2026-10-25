"""End-to-end checks using fictional material and a local, offline adapter."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from meeting_harness.cli import private_destination, write_private
from meeting_harness.ingest import HarnessError, latest_source, make_bundle, read_paragraphs
from meeting_harness.prompts import build_prompt, schema_for
from meeting_harness.review import artifact_hash


PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT / "examples" / "2026-01-15-fictional.md"
FIXTURE = runpy.run_path(str(PROJECT / "examples" / "fixture_model.py"))
FICTIONAL_SOURCE = """Fictional agent review
Date: 2026-10-21
Alice: I support a small pilot, but require approval for external actions.
Bob: I support a small pilot. I prefer approval only for irreversible actions.
Alice: The approval rule remains unresolved.
"""


def fictional_artifacts(bundle: dict) -> dict:
    """Use the public offline example without reading any actual meeting."""
    context = {}
    for stage, name in (("discussion", "discussion"), ("coverage_review", "coverage"),
                        ("brief", "brief"), ("agenda", "agenda")):
        context[name] = FIXTURE["stage_output"](stage, bundle, context)
        context[name + "_sha256"] = artifact_hash(context[name])
    return context


class IngestionWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def source(self, name: str, content: str) -> Path:
        target = self.folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def test_latest_uses_meeting_date_even_when_older_file_was_modified_last(self) -> None:
        old = self.source("2026-10-18-old.md", "Old fictional meeting\nAlice: An older remark.\n")
        latest = self.source("nested/2026-10-21-new.md", "Latest fictional meeting\nAlice: A newer remark.\n")
        os.utime(old, (2_000_000_000, 2_000_000_000))
        os.utime(latest, (1_000_000_000, 1_000_000_000))
        self.assertGreater(old.stat().st_mtime, latest.stat().st_mtime)
        self.assertEqual(latest_source(self.folder), latest)

    def test_latest_uses_header_date_not_a_later_date_in_discussion(self) -> None:
        self.source("earlier.md", "Earlier fictional meeting\nDate: 2026-10-18\nAgenda\nAttendees\nAlice: Discuss 2026-12-31 later.\n")
        newest = self.source("newest.txt", "Newest fictional meeting\nDate: 2026-10-21\nAlice: Review.\n")
        self.assertEqual(latest_source(self.folder), newest)

    def test_equal_latest_dates_require_explicit_selection(self) -> None:
        self.source("2026-10-21-one.md", "Fictional one\nAlice: Hello.\n")
        self.source("2026-10-21-two.txt", "Fictional two\nBob: Hello.\n")
        with self.assertRaisesRegex(HarnessError, "Multiple sources"):
            latest_source(self.folder)

    def test_undated_source_blocks_latest_but_explicit_source_preserves_unknown(self) -> None:
        self.source("2026-10-21-known.md", "Dated fictional meeting\nAlice: Hello.\n")
        undated = self.source("undated.md", "Undated fictional meeting\nBob: Hello.\n")
        with self.assertRaisesRegex(HarnessError, "undated meeting"):
            latest_source(self.folder)
        bundle = make_bundle(undated)
        self.assertIsNone(bundle["meeting"]["date"])
        self.assertEqual(bundle["meeting"]["date_basis"], "not_recorded")
        self.assertTrue(any("date not recorded" in text for text in bundle["warnings"]))

    def test_conflicting_filename_header_requires_explicit_override(self) -> None:
        source = self.source("2026-10-21-fictional.md", "Fictional review\nDate: 2026-10-20\nAlice: Hello.\n")
        with self.assertRaisesRegex(HarnessError, "Conflicting"):
            make_bundle(source)
        bundle = make_bundle(source, "2026-10-20")
        self.assertEqual(bundle["meeting"]["date"], "2026-10-20")
        self.assertEqual(bundle["meeting"]["date_basis"], "explicit_override")

    def test_invalid_calendar_date_override_is_rejected(self) -> None:
        source = self.source("fictional.md", FICTIONAL_SOURCE)
        with self.assertRaisesRegex(HarnessError, "YYYY-MM-DD"):
            make_bundle(source, "2026-02-30")

    def test_hidden_files_and_word_lock_files_are_ignored(self) -> None:
        newest = self.source("2026-10-21-fictional.md", "Fictional review\nAlice: Hello.\n")
        self.source(".cache/2026-12-31-hidden.md", "Hidden fictional meeting\n")
        self.source("~$2026-12-31-locked.docx", "Not a real document")
        self.assertEqual(latest_source(self.folder), newest)

    def test_adjacent_reports_and_hidden_run_files_do_not_compete_with_source(self) -> None:
        newest = self.source("2026-10-21/transcript.txt", "Fictional review\nDate: 2026-10-21\nAlice: Hello.\n")
        self.source("2026-10-21/summary.md", "Summary\nMeeting date: 2026-10-21\n")
        self.source("2026-10-21/.harness/prompt.txt", "Private generated prompt without a meeting date.\n")
        self.source("2026-10-21/.harness/bundle.json", "{}")
        self.assertEqual(latest_source(self.folder), newest)

    def test_legacy_adjacent_digest_is_ignored(self) -> None:
        newest = self.source("2026-10-21/transcript.txt", "Fictional review\nDate: 2026-10-21\nAlice: Hello.\n")
        self.source("2026-10-21/digest.md", "Summary\nMeeting date: 2026-12-31\n")
        self.assertEqual(latest_source(self.folder), newest)

    def test_docx_extraction_includes_tables_and_preserves_body_order(self) -> None:
        source = self.folder / "2026-10-21-fictional.docx"
        document = '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>Fictional DOCX review</w:t></w:r></w:p>
<w:p><w:r><w:t>Date: 2026-10-21</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Alice: A table-cell point.</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>Bob: Another table-cell point.</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:p><w:r><w:t>Alice: Closing</w:t><w:tab/><w:t>remark.</w:t><w:br/><w:t>Unattributed note.</w:t></w:r></w:p>
</w:body></w:document>'''
        with ZipFile(source, "w") as archive:
            archive.writestr("word/document.xml", document)
        paragraphs = read_paragraphs(source)
        self.assertEqual(paragraphs, ["Fictional DOCX review", "Date: 2026-10-21",
                                     "Alice: A table-cell point.", "Bob: Another table-cell point.",
                                     "Alice: Closing\tremark.\nUnattributed note."])
        bundle = make_bundle(source)
        self.assertEqual([segment["speaker"] for segment in bundle["segments"]],
                         [None, None, "Alice", "Bob", "Alice"])

    def test_docx_soft_break_preserves_teams_speaker_header_with_its_body(self) -> None:
        source = self.folder / "2026-10-21-soft-break.docx"
        document = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>Fictional Teams export</w:t></w:r></w:p>
<w:p><w:r><w:t>Alice 8:20</w:t><w:br/><w:t>A fictional point across</w:t><w:br/><w:t>two lines.</w:t></w:r></w:p>
</w:body></w:document>'''
        with ZipFile(source, "w") as archive:
            archive.writestr("word/document.xml", document)
        bundle = make_bundle(source)
        self.assertEqual(len(bundle["segments"]), 2)
        self.assertEqual(bundle["segments"][1]["speaker"], "Alice")
        self.assertEqual(bundle["segments"][1]["timestamp"], "8:20")
        self.assertEqual(bundle["segments"][1]["text"], "A fictional point across\ntwo lines.")

    def test_export_time_metadata_is_not_mistaken_for_a_speaker(self) -> None:
        source = self.source("2026-10-21-time-header.txt",
                             "Fictional meeting\nOctober 21, 2026, 8:20AM\nAlice: A fictional point.\n")
        bundle = make_bundle(source)
        self.assertIsNone(bundle["segments"][1]["speaker"])
        self.assertIsNone(bundle["segments"][1]["timestamp"])
        self.assertEqual(bundle["segments"][2]["speaker"], "Alice")

    def test_corrupt_or_entity_bearing_docx_is_rejected(self) -> None:
        source = self.folder / "corrupt.docx"
        source.write_bytes(b"not a document")
        with self.assertRaises(HarnessError):
            read_paragraphs(source)
        with ZipFile(source, "w") as archive:
            archive.writestr("word/document.xml", '<!DOCTYPE fictional><document/>')
        with self.assertRaisesRegex(HarnessError, "entities"):
            read_paragraphs(source)


class CommandWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / EXAMPLE.name
        self.source.write_bytes(EXAMPLE.read_bytes())
        self.bundle = make_bundle(self.source)
        self.artifacts = fictional_artifacts(self.bundle)

    def cli(self, *args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "meeting_harness", *map(str, args)], cwd=PROJECT,
                              capture_output=True, text=True, timeout=20, check=False)

    def json_file(self, name: str, value: object) -> Path:
        target = self.folder / name
        target.write_text(json.dumps(value), encoding="utf-8")
        return target

    def artifact_flags(self, artifacts: dict | None = None, bundle: dict | None = None) -> list[str]:
        values = self.artifacts if artifacts is None else artifacts
        flags = ["--bundle", str(self.json_file("bundle-input.json", self.bundle if bundle is None else bundle))]
        for name in ("discussion", "coverage", "brief", "agenda"):
            flags.extend(["--" + name, str(self.json_file(name + "-input.json", values[name]))])
        return flags

    def adapter(self, mode: str) -> Path:
        target = self.folder / f"adapter-{mode}.py"
        target.write_text(
            "import json, pathlib, runpy, sys, time\n"
            f"fixture = runpy.run_path({str(PROJECT / 'examples' / 'fixture_model.py')!r})\n"
            "stage, payload = fixture['parse_prompt'](sys.stdin.read())\n"
            "state = pathlib.Path(__file__).with_suffix('.calls.json')\n"
            "calls = json.loads(state.read_text()) if state.exists() else []\n"
            "calls.append({'stage': stage, 'feedback': bool(payload['validation_feedback']), "
            "'context_keys': sorted(payload['context']), 'segments_present': 'segments' in payload['bundle']})\n"
            "state.write_text(json.dumps(calls))\n"
            "count = sum(c['stage'] == stage for c in calls)\n"
            f"mode = {mode!r}\n"
            "if mode == 'timeout': time.sleep(3)\n"
            "if mode == 'exit': print('fictional diagnostic', file=sys.stderr); sys.exit(7)\n"
            "if mode == 'invalid' or (mode == 'repair' and stage == 'discussion' and count == 1):\n"
            "    print('{invalid json'); sys.exit(0)\n"
            "value = fixture['stage_output'](stage, payload['bundle'], payload['context'])\n"
            "if stage == 'coverage_review' and (mode == 'coverage-always' or (mode == 'coverage-once' and count == 1)):\n"
            "    value['status'] = 'revise'\n"
            "    value['issues'] = [{'kind': 'missing_comment', 'topic_id': 'T2', 'segment_ids': ['S0005'], 'detail': 'Recheck Casey timing comment against the source.'}]\n"
            "if mode == 'missing-topic' and stage == 'brief' and count == 1: value['topics'].pop()\n"
            "if mode == 'missing-comment' and stage == 'brief' and count == 1: value['topics'][1]['discussion'].pop()\n"
            "if mode == 'attribution' and stage == 'discussion': value['topics'][0]['presentation']['evidence'] = value['topics'][0]['discussion'][0]['evidence']\n"
            "print(json.dumps(value))\n", encoding="utf-8")
        return target

    def run_adapter(self, mode: str, *, attempts: int = 2,
                    extra: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        adapter = self.adapter(mode)
        out = self.folder / f"run-{mode}"
        result = self.cli("run", "--source", self.source, "--out", out, "--attempts", attempts,
                          "--command-json", json.dumps([sys.executable, str(adapter)]), *extra)
        return result, out, adapter

    def calls(self, adapter: Path) -> list[dict]:
        return json.loads(adapter.with_suffix(".calls.json").read_text())

    def stages(self, adapter: Path) -> list[str]:
        return [call["stage"] for call in self.calls(adapter)]

    def test_prepare_check_render_complete_workflow(self) -> None:
        out = self.folder / "prepared"
        result = self.cli("prepare", "--source", self.source, "--out", out, "--language", "zh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(path.name for path in out.iterdir()),
                         {"bundle.json", "discussion.prompt.txt", "discussion.schema.json", "prepared.json"})
        self.assertEqual(json.loads((out / "bundle.json").read_text()), self.bundle)
        self.assertIn("Chinese (简体中文)", (out / "discussion.prompt.txt").read_text())
        self.assertEqual((out / "bundle.json").stat().st_mode & 0o777, 0o600)
        flags = self.artifact_flags()
        result = self.cli("check", *flags)
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = self.folder / "digest.md"
        result = self.cli("render", *flags, "--out", rendered)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = rendered.read_text()
        for required in ("完整会议讨论", "评论与回应", "讨论结果", "分歧与开放问题", "下次讨论议题", "原文依据"):
            self.assertIn(required, text)
        self.assertIn("S0003", text)
        self.assertIn("Alex and Blair support a small workshop", text)
        self.assertLess(text.index("Workshop timing"), text.index("下次讨论议题"))

    def test_run_only_calls_agenda_after_coverage_and_complete_brief(self) -> None:
        result, out, adapter = self.run_adapter("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stages(adapter), ["discussion", "coverage_review", "brief", "agenda"])
        for name in ("discussion", "coverage", "brief", "agenda"):
            self.assertEqual(json.loads((out / (name + ".json")).read_text()), self.artifacts[name])
        receipt = json.loads((out / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "ordered_workflow_completed")
        self.assertEqual(receipt["stage_order"], self.stages(adapter))
        self.assertEqual(receipt["topic_count"], 2)
        self.assertIn("not proof", receipt["review_limits"])
        self.assertTrue((out / "digest.md").exists())
        self.assertFalse((out / "failure.json").exists())
        self.assertEqual([call["segments_present"] for call in self.calls(adapter)], [True, True, True, False])

    def test_run_repairs_malformed_first_response_with_feedback(self) -> None:
        result, out, adapter = self.run_adapter("repair")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stages(adapter), ["discussion", "discussion", "coverage_review", "brief", "agenda"])
        records = json.loads((out / "receipt.json").read_text())["attempts"]
        self.assertTrue(records[0]["errors"])
        self.assertEqual(records[1]["errors"], [])
        self.assertFalse(self.calls(adapter)[0]["feedback"])
        self.assertTrue(self.calls(adapter)[1]["feedback"])
        self.assertIn("previous_candidate", self.calls(adapter)[1]["context_keys"])

    def test_coverage_revision_repairs_discussion_before_shortening(self) -> None:
        result, out, adapter = self.run_adapter("coverage-once")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stages(adapter), ["discussion", "coverage_review", "discussion", "coverage_review", "brief", "agenda"])
        repaired = self.calls(adapter)[2]
        self.assertIn("coverage_feedback", repaired["context_keys"])
        self.assertIn("previous_discussion", repaired["context_keys"])
        records = json.loads((out / "receipt.json").read_text())["attempts"]
        self.assertEqual(records[2]["stage"], "coverage_revision")

    def test_persistent_coverage_revision_never_calls_brief_or_agenda(self) -> None:
        result, out, adapter = self.run_adapter("coverage-always")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.stages(adapter), ["discussion", "coverage_review", "discussion", "coverage_review"])
        self.assertIn("Coverage review", result.stderr)
        self.assertFalse((out / "brief.json").exists())
        self.assertFalse((out / "agenda.json").exists())
        self.assertFalse((out / "digest.md").exists())
        self.assertTrue((out / "failure.json").exists())

    def test_brief_cannot_drop_a_topic_or_comment_and_is_repaired_before_agenda(self) -> None:
        for mode in ("missing-topic", "missing-comment"):
            with self.subTest(mode=mode):
                result, out, adapter = self.run_adapter(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.stages(adapter), ["discussion", "coverage_review", "brief", "brief", "agenda"])
                records = json.loads((out / "receipt.json").read_text())["attempts"]
                self.assertTrue(records[2]["errors"])
                self.assertTrue(any("preserve every" in error for error in records[2]["errors"]))
                self.assertEqual(records[3]["errors"], [])
                self.assertEqual(json.loads((out / "brief.json").read_text()), self.artifacts["brief"])

    def test_permanent_invalid_response_stops_without_final_digest(self) -> None:
        result, out, adapter = self.run_adapter("invalid")
        self.assertEqual(result.returncode, 2)
        failure = json.loads((out / "failure.json").read_text())
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(len(failure["attempts"]), 2)
        self.assertEqual(self.stages(adapter), ["discussion", "discussion"])
        self.assertFalse((out / "discussion.json").exists())
        self.assertFalse((out / "digest.md").exists())

    def test_wrong_speaker_evidence_is_rejected_through_cli(self) -> None:
        result, out, adapter = self.run_adapter("attribution", attempts=1)
        self.assertEqual(result.returncode, 2)
        failure = json.loads((out / "failure.json").read_text())
        self.assertTrue(any("speaker" in error or "Alex" in error for error in failure["attempts"][0]["errors"]))
        self.assertEqual(self.stages(adapter), ["discussion"])
        self.assertFalse((out / "digest.md").exists())

    def test_malformed_bundle_is_rejected_cleanly_by_check_and_render(self) -> None:
        for index, broken_segments in enumerate((None, 3, [{"id": "S0001"}])):
            with self.subTest(segments=broken_segments):
                broken = deepcopy(self.bundle)
                broken["segments"] = broken_segments
                flags = self.artifact_flags(bundle=broken)
                checked = self.cli("check", *flags)
                self.assertEqual(checked.returncode, 2)
                self.assertIn("bundle.segments", checked.stderr)
                self.assertNotIn("Traceback", checked.stderr)
                destination = self.folder / f"broken-digest-{index}.md"
                rendered = self.cli("render", *flags, "--out", destination)
                self.assertEqual(rendered.returncode, 2)
                self.assertNotIn("Traceback", rendered.stderr)
                self.assertFalse(destination.exists())

    def test_manual_prompts_refuse_early_brief_or_agenda(self) -> None:
        flags = self.artifact_flags()
        bundle_path = flags[1]
        discussion_path = flags[3]
        coverage_path = flags[5]
        brief_path = flags[7]
        cases = [
            ("agenda", []),
            ("agenda", ["--discussion", discussion_path]),
            ("agenda", ["--discussion", discussion_path, "--coverage", coverage_path]),
            ("brief", ["--discussion", discussion_path]),
        ]
        for index, (stage, prior) in enumerate(cases):
            with self.subTest(stage=stage, prior=prior):
                out = self.folder / f"early-prompt-{index}.txt"
                result = self.cli("prompt", "--stage", stage, "--bundle", bundle_path, *prior, "--out", out)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(out.exists())
        agenda_prompt = self.folder / "agenda.prompt.txt"
        result = self.cli("prompt", "--stage", "agenda", "--bundle", bundle_path,
                          "--discussion", discussion_path, "--coverage", coverage_path,
                          "--brief", brief_path, "--out", agenda_prompt)
        self.assertEqual(result.returncode, 0, result.stderr)
        stage, payload = FIXTURE["parse_prompt"](agenda_prompt.read_text())
        self.assertEqual(stage, "agenda")
        self.assertNotIn("segments", payload["bundle"])

    def test_manual_prompt_rejects_revision_and_incomplete_brief(self) -> None:
        flags = self.artifact_flags()
        revised = deepcopy(self.artifacts["coverage"])
        revised["status"] = "revise"
        revised["issues"] = [{"kind": "missing_response", "topic_id": "T2",
                              "segment_ids": ["S0006"], "detail": "Check Alex's reply."}]
        coverage_path = self.json_file("coverage-revise.json", revised)
        for stage in ("brief", "agenda"):
            out = self.folder / (stage + "-blocked.txt")
            result = self.cli("prompt", "--stage", stage, "--bundle", flags[1],
                              "--discussion", flags[3], "--coverage", coverage_path,
                              "--brief", flags[7], "--out", out)
            self.assertEqual(result.returncode, 2)
            self.assertIn("revision", result.stderr)
            self.assertFalse(out.exists())
        broken = deepcopy(self.artifacts["brief"])
        broken["topics"].pop()
        brief_path = self.json_file("brief-incomplete.json", broken)
        out = self.folder / "agenda-incomplete.txt"
        result = self.cli("prompt", "--stage", "agenda", "--bundle", flags[1],
                          "--discussion", flags[3], "--coverage", flags[5],
                          "--brief", brief_path, "--out", out)
        self.assertEqual(result.returncode, 2)
        self.assertIn("preserve every topic", result.stderr)
        self.assertFalse(out.exists())

    def test_manual_discussion_prompt_rejects_malformed_bundle(self) -> None:
        for index, broken in enumerate(({}, {**self.bundle, "segments": None},
                                         {**self.bundle, "segments": [{"id": "S0001"}]})):
            with self.subTest(bundle=broken):
                bundle_path = self.json_file(f"malformed-prompt-bundle-{index}.json", broken)
                out = self.folder / f"malformed-discussion-{index}.prompt.txt"
                result = self.cli("prompt", "--stage", "discussion", "--bundle", bundle_path,
                                  "--out", out)
                self.assertEqual(result.returncode, 2)
                self.assertIn("bundle", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(out.exists())

    def test_topic_count_is_unlimited_until_final_agenda_selection(self) -> None:
        source = self.folder / "2026-01-15-seven-topics.txt"
        source.write_text("Fictional seven-topic meeting\nDate: 2026-01-15\n" +
                          "\n".join(f"Alex: Independent topic {n}." for n in range(1, 8)) + "\n")
        bundle = make_bundle(source)
        discussion = deepcopy(self.artifacts["discussion"])
        discussion["meeting"] = {key: bundle["meeting"][key] for key in ("title", "date", "source_sha256")}
        discussion["rounds"] = [{"id": "R1", "presenter": "Alex", "topic_ids": [f"T{n}" for n in range(1, 8)]}]
        discussion["topics"] = []
        for n, segment in enumerate(bundle["segments"][2:], 1):
            discussion["topics"].append({
                "id": f"T{n}", "round_id": "R1", "presenter": "Alex", "title": f"Independent topic {n}",
                "segment_ids": [segment["id"]],
                "presentation": {"summary": segment["text"], "evidence": [{"segment_id": segment["id"], "quote": segment["text"]}]},
                "discussion": [], "outcome": {"status": "no_conclusion", "summary": "No conclusion recorded.", "participants": [], "evidence": []},
                "unresolved": [], "recorded_next_steps": []})
        values = {"discussion": discussion}
        values["coverage"] = FIXTURE["stage_output"]("coverage_review", bundle, values)
        values["brief"] = FIXTURE["stage_output"]("brief", bundle, values)
        values["agenda"] = {"schema_version": 2, "meeting": discussion["meeting"],
                            "discussion_sha256": artifact_hash(discussion), "brief_sha256": artifact_hash(values["brief"]),
                            "items": [{"id": "A1", "kind": "synthesizer_suggestion", "title": "Explore the first topic.",
                                       "reason": "Summarizer suggestion based on Alex's first share.", "based_on": ["T1"], "recorded_proposal_id": None}],
                            "deferred_recorded_proposals": []}
        result = self.cli("check", *self.artifact_flags(values, bundle))
        self.assertEqual(result.returncode, 0, result.stderr)
        context = {**values, **{name + "_sha256": artifact_hash(value) for name, value in values.items()}}
        for stage in ("discussion", "coverage_review", "brief"):
            prompt = build_prompt(stage, bundle, context)
            instructions = prompt.split("UNTRUSTED INPUT (JSON DATA):", 1)[0]
            self.assertNotIn("1–3", instructions)
            self.assertNotIn("1-3", instructions)
            schema = schema_for(stage)
            self.assertNotIn('"maxItems": 3', json.dumps(schema))
        self.assertEqual(schema_for("agenda")["properties"]["items"]["maxItems"], 3)
        values["agenda"]["items"] *= 4
        result = self.cli("check", *self.artifact_flags(values, bundle))
        self.assertEqual(result.returncode, 2)
        self.assertIn("1–3", result.stderr)

    def test_timeout_records_failure_without_final_digest(self) -> None:
        result, out, _ = self.run_adapter("timeout", extra=("--timeout", "1"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("timed out", result.stderr)
        self.assertIn("timed out", json.loads((out / "failure.json").read_text())["reason"])
        self.assertFalse((out / "digest.md").exists())

    def test_nonzero_adapter_exit_preserves_private_diagnostics(self) -> None:
        result, out, _ = self.run_adapter("exit")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("fictional diagnostic", result.stderr)
        self.assertIn("fictional diagnostic", (out / "01-discussion.stderr.txt").read_text())
        self.assertEqual(json.loads((out / "failure.json").read_text())["status"], "failed")
        self.assertFalse((out / "digest.md").exists())

    def test_prompt_limit_stops_before_model_adapter_starts(self) -> None:
        result, out, adapter = self.run_adapter("limit", extra=("--max-prompt-chars", "10"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("No text was truncated or sent", result.stderr)
        self.assertFalse(adapter.with_suffix(".calls.json").exists())
        self.assertFalse((out / "digest.md").exists())

    def test_source_size_limit_rejects_before_any_output_or_model_call(self) -> None:
        oversized = self.folder / "2026-01-15-oversized.txt"
        with oversized.open("wb") as stream:
            stream.truncate(20_000_001)
        out = self.folder / "oversized-run"
        adapter = self.adapter("oversized")
        result = self.cli("run", "--source", oversized, "--out", out,
                          "--command-json", json.dumps([sys.executable, str(adapter)]))
        self.assertEqual(result.returncode, 2)
        self.assertIn("20 MB", result.stderr)
        self.assertFalse(out.exists())
        self.assertFalse(adapter.with_suffix(".calls.json").exists())

    def test_prepare_and_render_do_not_overwrite_previous_outputs(self) -> None:
        out = self.folder / "prepared"
        first = self.cli("prepare", "--source", self.source, "--out", out)
        self.assertEqual(first.returncode, 0, first.stderr)
        original = (out / "bundle.json").read_bytes()
        second = self.cli("prepare", "--source", self.source, "--out", out)
        self.assertEqual(second.returncode, 2)
        self.assertEqual((out / "bundle.json").read_bytes(), original)
        rendered = self.folder / "digest.md"
        rendered.write_text("Existing fictional notes", encoding="utf-8")
        result = self.cli("render", *self.artifact_flags(), "--out", rendered)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(rendered.read_text(), "Existing fictional notes")

    def test_outputs_cannot_overwrite_original_source(self) -> None:
        original = self.source.read_bytes()
        flags = self.artifact_flags()
        result = self.cli("render", *flags, "--out", self.source)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.source.read_bytes(), original)
        result = self.cli("prompt", "--stage", "discussion", "--bundle", flags[1], "--out", self.source)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.source.read_bytes(), original)
        result = self.cli("prepare", "--source", self.source, "--out", self.source)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.source.read_bytes(), original)

    def test_private_file_writer_refuses_existing_symlink(self) -> None:
        target = self.folder / "existing.txt"
        target.write_text("Keep existing fictional text", encoding="utf-8")
        link = self.folder / "link.txt"
        link.symlink_to(target)
        with self.assertRaisesRegex(HarnessError, "already exists"):
            write_private(link, "replacement")
        self.assertEqual(target.read_text(), "Keep existing fictional text")

    def test_public_project_output_is_blocked_but_ignored_private_is_allowed(self) -> None:
        with self.assertRaisesRegex(HarnessError, "outside the public project"):
            private_destination(PROJECT / "synthetic-generated-output" / "bundle.json")
        self.assertEqual(private_destination(PROJECT / "private" / "synthetic-run"),
                         PROJECT / "private" / "synthetic-run")
        self.assertEqual(private_destination(PROJECT / "Meetings" / "2026-01-15" / ".harness" / "run"),
                         PROJECT / "Meetings" / "2026-01-15" / ".harness" / "run")
        for path in (PROJECT / "Meetings-public" / "report.md",
                     PROJECT / "Meetings" / ".." / "meeting_harness" / "report.md"):
            with self.subTest(path=path), self.assertRaises(HarnessError):
                private_destination(path)

    def test_meetings_symlinks_cannot_authorize_writes_to_public_code(self) -> None:
        checkout = self.folder / "fictional-checkout"
        code = checkout / "meeting_harness"
        code.mkdir(parents=True)
        (checkout / "public-files.json").write_text('{}', encoding='utf-8')
        fake_cli = code / "cli.py"
        fake_cli.write_text('# Fictional guard test', encoding='utf-8')
        meetings = checkout / "Meetings"
        with patch('meeting_harness.cli.__file__', str(fake_cli)):
            meetings.symlink_to(code, target_is_directory=True)
            with self.assertRaises(HarnessError):
                private_destination(meetings / "report.md")
            meetings.unlink()
            meetings.mkdir()
            (meetings / "run").symlink_to(code, target_is_directory=True)
            with self.assertRaises(HarnessError):
                private_destination(meetings / "run" / "report.md")


if __name__ == "__main__":
    unittest.main()
