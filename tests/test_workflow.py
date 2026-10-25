"""End-to-end checks using fictional material and a local, offline adapter."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from meeting_harness.cli import private_destination, write_private
from meeting_harness.ingest import HarnessError, latest_source, make_bundle, read_paragraphs


PROJECT = Path(__file__).resolve().parents[1]
FICTIONAL_SOURCE = """Fictional agent review
Date: 2026-10-21
Alice: I support a small pilot, but require approval for external actions.
Bob: I support a small pilot. I prefer approval only for irreversible actions.
Alice: The approval rule remains unresolved.
"""


def fictional_summary(bundle: dict) -> dict:
    def ev(segment_id: str, quote: str) -> dict:
        return {"segment_id": segment_id, "quote": quote}

    alice = ev("S0003", "I support a small pilot, but require approval for external actions.")
    bob = ev("S0004", "I support a small pilot. I prefer approval only for irreversible actions.")
    return {
        "schema_version": 1,
        "meeting": {key: bundle["meeting"][key] for key in ("title", "date", "source_sha256")},
        "core_points": [{"id": "C1", "point": "A small pilot was supported; approval rules remain unresolved.",
                         "evidence": [alice, bob]}],
        "participant_views": [
            {"speaker": "Alice", "view": "Supports a pilot with approval for external actions.", "evidence": [alice]},
            {"speaker": "Bob", "view": "Supports a pilot with approval for irreversible actions.", "evidence": [bob]},
        ],
        "consensus": {"status": "partial", "summary": "Both speakers support a small pilot.",
                      "participants": ["Alice", "Bob"], "evidence": [alice, bob]},
        "unresolved": [{"issue": "Which actions require approval?", "status": "disagreement",
                        "positions": [
                            {"speaker": "Alice", "view": "Require approval for external actions.", "evidence": [alice]},
                            {"speaker": "Bob", "view": "Require approval for irreversible actions.", "evidence": [bob]},
                        ], "evidence": [ev("S0005", "The approval rule remains unresolved.")]}],
        "next_discussion": [{"question": "Which concrete pilot actions require approval?",
                             "why": "A shared rule is still unresolved.", "based_on": ["C1", "U1"],
                             "kind": "proposal"}],
        "limitations": ["Fictional test material; no real participants or meeting content."],
    }


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
        self.source = self.folder / "2026-10-21-fictional.txt"
        self.source.write_text(FICTIONAL_SOURCE, encoding="utf-8")
        self.bundle = make_bundle(self.source)
        self.summary = fictional_summary(self.bundle)

    def cli(self, *args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "meeting_harness", *map(str, args)], cwd=PROJECT,
                              capture_output=True, text=True, timeout=15, check=False)

    def json_file(self, name: str, value: object) -> Path:
        target = self.folder / name
        target.write_text(json.dumps(value), encoding="utf-8")
        return target

    def adapter(self, mode: str, summary: dict | None = None) -> Path:
        target = self.folder / f"adapter-{mode}.py"
        payload = json.dumps(self.summary if summary is None else summary)
        target.write_text(
            "import json, pathlib, sys, time\n"
            "prompt = sys.stdin.read()\n"
            "if 'UNTRUSTED SOURCE BUNDLE (JSON DATA):' not in prompt: sys.exit(8)\n"
            "state = pathlib.Path(__file__).with_suffix('.calls.json')\n"
            "calls = json.loads(state.read_text()) if state.exists() else []\n"
            "calls.append({'feedback': 'VALIDATION FEEDBACK' in prompt, 'previous': 'Previous invalid JSON candidate' in prompt})\n"
            "state.write_text(json.dumps(calls))\n"
            f"mode = {mode!r}\n"
            "if mode == 'timeout': time.sleep(3)\n"
            "if mode == 'exit': print('fictional diagnostic', file=sys.stderr); sys.exit(7)\n"
            "if mode == 'invalid' or (mode == 'repair' and len(calls) == 1): print('{invalid json')\n"
            f"else: print({payload!r})\n", encoding="utf-8")
        return target

    def run_adapter(self, mode: str, *, attempts: int = 2, summary: dict | None = None,
                    extra: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        adapter = self.adapter(mode, summary)
        out = self.folder / f"run-{mode}"
        result = self.cli("run", "--source", self.source, "--out", out, "--attempts", attempts,
                          "--command-json", json.dumps([sys.executable, str(adapter)]), *extra)
        return result, out, adapter

    def test_prepare_check_render_complete_workflow(self) -> None:
        out = self.folder / "prepared"
        result = self.cli("prepare", "--source", self.source, "--out", out, "--language", "zh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(path.name for path in out.iterdir()),
                         {"bundle.json", "prompt.txt", "summary.schema.json", "prepared.json"})
        self.assertEqual(json.loads((out / "bundle.json").read_text()), self.bundle)
        self.assertIn("Chinese (简体中文)", (out / "prompt.txt").read_text())
        self.assertEqual((out / "bundle.json").stat().st_mode & 0o777, 0o600)
        summary = self.json_file("summary.json", self.summary)
        result = self.cli("check", "--bundle", out / "bundle.json", "--summary", summary)
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = self.folder / "digest.md"
        result = self.cli("render", "--bundle", out / "bundle.json", "--summary", summary, "--out", rendered)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = rendered.read_text()
        for required in ("Core discussion", "Participant views", "Agreement", "Unresolved", "For next discussion", "Evidence appendix"):
            self.assertIn(required, text)
        self.assertIn("S0003", text)
        self.assertIn("Both speakers support a small pilot.", text)

    def test_run_valid_adapter_succeeds_and_records_review_limit(self) -> None:
        result, out, adapter = self.run_adapter("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((out / "summary.json").read_text()), self.summary)
        receipt = json.loads((out / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "traceability_passed")
        self.assertEqual(receipt["semantic_review"], "human_review_required")
        self.assertEqual(len(receipt["attempts"]), 1)
        self.assertTrue((out / "digest.md").exists())
        self.assertFalse((out / "failure.json").exists())
        self.assertEqual(json.loads(adapter.with_suffix(".calls.json").read_text()),
                         [{"feedback": False, "previous": False}])

    def test_run_repairs_malformed_first_response_with_feedback(self) -> None:
        result, out, adapter = self.run_adapter("repair")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((out / "receipt.json").read_text())
        self.assertEqual(len(receipt["attempts"]), 2)
        self.assertTrue(receipt["attempts"][0]["errors"])
        self.assertEqual(receipt["attempts"][1]["errors"], [])
        self.assertEqual(json.loads(adapter.with_suffix(".calls.json").read_text()),
                         [{"feedback": False, "previous": False}, {"feedback": True, "previous": True}])

    def test_permanent_invalid_response_stops_without_final_digest(self) -> None:
        result, out, _ = self.run_adapter("invalid")
        self.assertEqual(result.returncode, 2)
        failure = json.loads((out / "failure.json").read_text())
        self.assertEqual(failure["status"], "failed_validation")
        self.assertEqual(len(failure["attempts"]), 2)
        self.assertFalse((out / "summary.json").exists())
        self.assertFalse((out / "digest.md").exists())

    def test_wrong_speaker_evidence_is_rejected_through_cli(self) -> None:
        candidate = deepcopy(self.summary)
        candidate["participant_views"][0]["evidence"] = candidate["participant_views"][1]["evidence"]
        result, out, _ = self.run_adapter("attribution", attempts=1, summary=candidate)
        self.assertEqual(result.returncode, 2)
        failure = json.loads((out / "failure.json").read_text())
        self.assertTrue(any("not 'Alice'" in error for error in failure["attempts"][0]["errors"]))
        self.assertFalse((out / "digest.md").exists())

    def test_timeout_records_failure_without_final_digest(self) -> None:
        result, out, _ = self.run_adapter("timeout", extra=("--timeout", "1"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("timed out", result.stderr)
        self.assertEqual(json.loads((out / "failure.json").read_text())["reason"], "model_timeout")
        self.assertFalse((out / "digest.md").exists())

    def test_nonzero_adapter_exit_preserves_private_diagnostics(self) -> None:
        result, out, _ = self.run_adapter("exit")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("fictional diagnostic", result.stderr)
        self.assertIn("fictional diagnostic", (out / "attempt-1.stderr.txt").read_text())
        self.assertEqual(json.loads((out / "failure.json").read_text())["returncode"], 7)
        self.assertFalse((out / "digest.md").exists())

    def test_prompt_limit_stops_before_model_adapter_starts(self) -> None:
        result, out, adapter = self.run_adapter("limit", extra=("--max-prompt-chars", "10"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("No text was truncated or sent", result.stderr)
        self.assertFalse(adapter.with_suffix(".calls.json").exists())
        self.assertFalse((out / "digest.md").exists())

    def test_prepare_and_render_do_not_overwrite_previous_outputs(self) -> None:
        out = self.folder / "prepared"
        first = self.cli("prepare", "--source", self.source, "--out", out)
        self.assertEqual(first.returncode, 0, first.stderr)
        original = (out / "bundle.json").read_bytes()
        second = self.cli("prepare", "--source", self.source, "--out", out)
        self.assertEqual(second.returncode, 2)
        self.assertEqual((out / "bundle.json").read_bytes(), original)
        summary = self.json_file("summary.json", self.summary)
        rendered = self.folder / "digest.md"
        rendered.write_text("Existing fictional notes", encoding="utf-8")
        result = self.cli("render", "--bundle", out / "bundle.json", "--summary", summary, "--out", rendered)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(rendered.read_text(), "Existing fictional notes")

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


if __name__ == "__main__":
    unittest.main()
