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

from meeting_harness.cli import private_destination, write_private, write_report_pair
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
                        ("brief", "brief"), ("concise", "concise"), ("agenda", "agenda")):
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

    def test_undated_shared_resource_does_not_block_latest_meeting(self) -> None:
        newest = self.source("2026-10-21/transcript.txt",
                             "Fictional review\nDate: 2026-10-21\nAlice: Hello.\n")
        self.source("resources/Journal-README.md",
                    "# Fictional Journal\nShared project instructions, not a meeting.\n")
        self.assertEqual(latest_source(self.folder), newest)

    def test_nested_resource_directories_are_ignored_case_insensitively(self) -> None:
        newest = self.source("2026-10-21/transcript.txt",
                             "Fictional review\nDate: 2026-10-21\nAlice: Hello.\n")
        self.source("nested/ReSoUrCeS/presentations/2026-12-31-notes.md",
                    "Fictional presentation notes\nAlice: Not a meeting transcript.\n")
        resource = self.folder / "2026-10-21/RESOURCES/nested/2027-01-01-example.docx"
        resource.parent.mkdir(parents=True)
        with ZipFile(resource, "w") as archive:
            archive.writestr("word/document.xml", '''
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>Fictional resource document</w:t></w:r></w:p></w:body>
</w:document>''')
        self.assertEqual(latest_source(self.folder), newest)

    def test_markdown_transcript_outside_resources_is_still_selected(self) -> None:
        self.source("2026-10-18/transcript.txt", "Earlier fictional meeting\nDate: 2026-10-18\n")
        self.source("resources/2027-01-01-later.md", "Dated fictional resource\n")
        newest = self.source("2026-10-21/original.md",
                             "Newest fictional meeting\nDate: 2026-10-21\nAlice: Hello.\n")
        self.assertEqual(latest_source(self.folder), newest)

    def test_adjacent_reports_and_hidden_run_files_do_not_compete_with_source(self) -> None:
        newest = self.source("2026-10-21/transcript.txt", "Fictional review\nDate: 2026-10-21\nAlice: Hello.\n")
        self.source("2026-10-21/summary.md", "Summary\nMeeting date: 2026-10-21\n")
        self.source("2026-10-21/summary_with_references.md", "References\nMeeting date: 2026-12-31\n")
        self.source("2026-10-21/custom_with_references.md", "References without a source date.\n")
        self.source("2026-10-21/custom.md", "Summary without a source date.\n")
        self.source("2026-10-21/.harness/prompt.txt", "Private generated prompt without a meeting date.\n")
        self.source("2026-10-21/.harness/bundle.json", "{}")
        self.assertEqual(latest_source(self.folder), newest)

    def test_legacy_adjacent_digest_is_ignored(self) -> None:
        newest = self.source("2026-10-21/transcript.txt", "Fictional review\nDate: 2026-10-21\nAlice: Hello.\n")
        self.source("2026-10-21/digest.md", "Summary\nMeeting date: 2026-12-31\n")
        self.assertEqual(latest_source(self.folder), newest)

    def test_dated_report_pairs_and_reference_marker_do_not_compete_with_source(self) -> None:
        newest = self.source('2026-10-21/transcript.txt', 'Fictional review\nDate: 2026-10-21\nAlice: Hello.\n')
        for title in ('learning group [catch-up]', 'Fictional research circle [monthly review]'):
            self.source(f'2026-10-21/2026-10-21 {title}.md', 'Condensed report without a source date.\n')
            self.source(f'2026-10-21/2026-10-21 {title} [with references].md', 'Evidence without a source date.\n')
        self.source('2026-10-21/archived companion [with references].md', 'References alone.\n')
        self.assertEqual(latest_source(self.folder), newest)

    def test_dated_bracketed_original_is_not_mistaken_for_a_generated_report(self) -> None:
        original = self.source('2026-10-21 Fictional study [monthly review].md', 'Original discussion\nAlice: Hello.\n')
        self.assertEqual(latest_source(self.folder), original)

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
        self.report_name = '2026-01-15 learning group [catch-up].md'
        self.reference_name = '2026-01-15 learning group [catch-up] [with references].md'
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
        for name in ("discussion", "coverage", "brief", "concise", "agenda"):
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
            "if mode == 'concise-missing-topic' and stage == 'concise' and count == 1: value['topics'].pop()\n"
            "if mode == 'concise-invalid' and stage == 'concise': value['topics'][1]['comments'] = []\n"
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
        text = rendered.with_name("digest [with references].md").read_text()
        concise = rendered.read_text()
        self.assertNotIn("原文依据", concise)
        self.assertNotIn("[E1]", concise)
        self.assertNotIn("S0003", concise)
        self.assertLess(len(concise), len(text))
        for report in (rendered, rendered.with_name("digest [with references].md")):
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
        for required in ("完整会议讨论", "评论与回应", "讨论结果", "分歧与开放问题", "下次讨论议题", "原文依据"):
            self.assertIn(required, text)
        self.assertIn("S0003", text)
        self.assertIn("Alex and Blair support a small workshop", text)
        self.assertLess(text.index("Workshop timing"), text.index("下次讨论议题"))

    def test_run_only_calls_agenda_after_coverage_and_both_complete_summaries(self) -> None:
        result, out, adapter = self.run_adapter("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stages(adapter), ["discussion", "coverage_review", "brief", "concise", "agenda"])
        for name in ("discussion", "coverage", "brief", "concise", "agenda"):
            self.assertEqual(json.loads((out / (name + ".json")).read_text()), self.artifacts[name])
        receipt = json.loads((out / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "ordered_workflow_completed")
        self.assertEqual(receipt["stage_order"], self.stages(adapter))
        self.assertEqual(receipt["topic_count"], 2)
        self.assertIn("not proof", receipt["review_limits"])
        self.assertTrue((out / self.report_name).exists())
        self.assertTrue((out / self.reference_name).exists())
        self.assertFalse((out / "digest.md").exists())
        self.assertEqual(receipt["reports"], {"concise": self.report_name, "with_references": self.reference_name})
        self.assertEqual(receipt["concise_sha256"], artifact_hash(self.artifacts["concise"]))
        self.assertFalse((out / "failure.json").exists())
        self.assertEqual([call["segments_present"] for call in self.calls(adapter)], [True, True, True, False, False])
        self.assertIn('concise', self.calls(adapter)[-1]['context_keys'])
        self.assertIn('concise_sha256', self.calls(adapter)[-1]['context_keys'])

    def test_report_names_use_recorded_meeting_date_instead_of_current_date(self) -> None:
        result, out, _ = self.run_adapter('dated-name')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((out / self.report_name).is_file())
        self.assertTrue((out / self.reference_name).is_file())
        self.assertNotIn(self.bundle['meeting']['title'], self.report_name)
        self.assertNotIn('summary.md', {p.name for p in out.iterdir()})
        for name in (self.report_name, self.reference_name):
            heading = (out / name).read_text().splitlines()[0]
            self.assertEqual(heading, r'# 2026-01-15 learning group \[catch-up\]')
            self.assertNotIn(self.bundle['meeting']['title'], heading)

    def test_run_reads_nearest_private_naming_settings_and_explicit_override_wins(self) -> None:
        setting = self.folder / '.harness' / 'report-naming.json'
        setting.parent.mkdir()
        setting.write_text(json.dumps({'report_title': 'Fictional research circle [monthly review]'}))
        nested = self.folder / 'collection' / 'session'
        nested.mkdir(parents=True)
        source = nested / self.source.name
        source.write_bytes(self.source.read_bytes())
        closest = nested.parent / '.harness' / 'report-naming.json'
        closest.parent.mkdir()
        closest.write_text(json.dumps({'report_title': 'Fictional local circle [weekly review]'}))
        self.source = source
        result, out, _ = self.run_adapter('configured')
        self.assertEqual(result.returncode, 0, result.stderr)
        basename = '2026-01-15 Fictional local circle [weekly review]'
        self.assertTrue((out / (basename + '.md')).is_file())
        self.assertTrue((out / (basename + ' [with references].md')).is_file())
        for name in (basename + '.md', basename + ' [with references].md'):
            self.assertEqual((out / name).read_text().splitlines()[0],
                             r'# 2026-01-15 Fictional local circle \[weekly review\]')
        closest.write_text(json.dumps({'report_title': '../unsafe'}))
        result, out, _ = self.run_adapter('override', extra=('--report-title', 'Fictional explicit title'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((out / '2026-01-15 Fictional explicit title.md').is_file())
        self.assertEqual((out / '2026-01-15 Fictional explicit title.md').read_text().splitlines()[0],
                         '# 2026-01-15 Fictional explicit title')

    def test_invalid_naming_settings_fail_before_output_or_model_calls(self) -> None:
        setting = self.folder / '.harness' / 'report-naming.json'
        setting.parent.mkdir()
        for index, title in enumerate(('../escape', 'folder/name', 'folder\\name', 'line\nbreak', 'bad:title', '', '\u0085',
                                      'trailing ', 'name [with references]', 'name_with_references', 'x' * 240)):
            with self.subTest(title=title):
                setting.write_text(json.dumps({'report_title': title}))
                result, out, adapter = self.run_adapter(f'unsafe-title-{index}')
                self.assertEqual(result.returncode, 2)
                self.assertIn('Report title', result.stderr)
                self.assertFalse(out.exists())
                self.assertFalse(adapter.with_suffix('.calls.json').exists())
        setting.write_text(json.dumps({'report_title': 'Fictional circle', 'unexpected': True}))
        result, out, _ = self.run_adapter('unexpected-setting')
        self.assertEqual(result.returncode, 2)
        self.assertIn('exactly one', result.stderr)
        self.assertFalse(out.exists())

    def test_unsafe_explicit_title_fails_before_output_or_model_calls(self) -> None:
        result, out, adapter = self.run_adapter('unsafe-argument', extra=('--report-title', '../escape'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('Report title', result.stderr)
        self.assertFalse(out.exists())
        self.assertFalse(adapter.with_suffix('.calls.json').exists())

    def test_symlinked_naming_settings_are_not_followed(self) -> None:
        setting = self.folder / '.harness' / 'report-naming.json'
        setting.parent.mkdir()
        external = self.json_file('external-naming.json', {'report_title': 'Fictional circle'})
        setting.symlink_to(external)
        result, out, _ = self.run_adapter('symlink-settings')
        self.assertEqual(result.returncode, 2)
        self.assertIn('not a symlink', result.stderr)
        self.assertFalse(out.exists())

    def test_render_directory_uses_bundle_date_and_private_settings(self) -> None:
        flags = self.artifact_flags()
        # A saved bundle can live several levels inside hidden working records;
        # naming remains attached to the meeting folder rather than the run.
        nested_bundle = self.folder / '.harness' / 'records' / 'bundle.json'
        nested_bundle.parent.mkdir(parents=True)
        nested_bundle.write_bytes(Path(flags[1]).read_bytes())
        flags[1] = str(nested_bundle)
        setting = self.folder / '.harness' / 'report-naming.json'
        setting.write_text(json.dumps({'report_title': 'Fictional research circle [monthly review]'}))
        out = self.folder / 'rendered-reports'
        result = self.cli('render', *flags, '--out', out)
        self.assertEqual(result.returncode, 0, result.stderr)
        basename = '2026-01-15 Fictional research circle [monthly review]'
        self.assertEqual({p.name for p in out.iterdir()}, {basename + '.md', basename + ' [with references].md'})
        for path in out.iterdir():
            self.assertEqual(path.read_text().splitlines()[0],
                             r'# 2026-01-15 Fictional research circle \[monthly review\]')
        override = self.folder / 'rendered-override'
        result = self.cli('render', *flags, '--out', override, '--report-title', 'Fictional explicit title')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((override / '2026-01-15 Fictional explicit title.md').is_file())

    def test_reference_style_explicit_output_names_are_rejected_without_creating_files(self) -> None:
        flags = self.artifact_flags()
        for name in ('summary_with_references.md', 'summary [with references].md', 'summary [WITH REFERENCES].md'):
            with self.subTest(name=name):
                out = self.folder / name
                result = self.cli('render', *flags, '--out', out)
                self.assertEqual(result.returncode, 2)
                self.assertIn('concise Markdown filename', result.stderr)
                self.assertFalse(out.exists())

    def test_explicit_markdown_path_uses_trusted_settings_for_both_headings(self) -> None:
        flags = self.artifact_flags()
        setting = self.folder / '.harness' / 'report-naming.json'
        setting.parent.mkdir()
        setting.write_text(json.dumps({'report_title': 'Fictional study circle [weekly review]'}))
        explicit = self.folder / 'custom-file.md'
        result = self.cli('render', *flags, '--out', explicit)
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (explicit, explicit.with_name('custom-file [with references].md')):
            heading = path.read_text().splitlines()[0]
            self.assertEqual(heading, r'# 2026-01-15 Fictional study circle \[weekly review\]')
            self.assertNotIn(self.bundle['meeting']['title'], heading)
        setting.write_text(json.dumps({'report_title': '../unsafe'}))
        rejected = self.folder / 'rejected-file.md'
        result = self.cli('render', *flags, '--out', rejected)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Report title', result.stderr)
        self.assertFalse(rejected.exists())
        override = self.folder / 'override-file.md'
        result = self.cli('render', *flags, '--out', override, '--report-title', 'Fictional override')
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (override, override.with_name('override-file [with references].md')):
            self.assertEqual(path.read_text().splitlines()[0], '# 2026-01-15 Fictional override')

    def test_explicit_markdown_path_preserves_legacy_undated_render_without_naming_settings(self) -> None:
        bundle = deepcopy(self.bundle)
        bundle['meeting']['date'] = None
        bundle['meeting']['date_basis'] = 'not_recorded'
        artifacts = fictional_artifacts(bundle)
        flags = self.artifact_flags(artifacts, bundle)
        explicit = self.folder / 'legacy-explicit.md'
        result = self.cli('render', *flags, '--out', explicit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(explicit.is_file())
        self.assertTrue(explicit.with_name('legacy-explicit [with references].md').is_file())
        for path in (explicit, explicit.with_name('legacy-explicit [with references].md')):
            self.assertEqual(path.read_text().splitlines()[0], r'# Date unknown learning group \[catch-up\]')
        automatic = self.folder / 'undated-auto'
        result = self.cli('render', *flags, '--out', automatic)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--meeting-date YYYY-MM-DD', result.stderr)
        self.assertFalse(automatic.exists())

    def test_unknown_meeting_date_fails_before_preparing_or_calling_model(self) -> None:
        self.source = self.folder / 'undated.txt'
        self.source.write_text('Fictional undated discussion\nAlex: Hello.\n')
        result, out, adapter = self.run_adapter('unknown-date')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--meeting-date YYYY-MM-DD', result.stderr)
        self.assertFalse(out.exists())
        self.assertFalse(adapter.with_suffix('.calls.json').exists())

    def test_explicit_meeting_date_controls_dated_output_name(self) -> None:
        result, out, _ = self.run_adapter('date-override', extra=('--meeting-date', '2026-02-17'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((out / '2026-02-17 learning group [catch-up].md').is_file())
        self.assertTrue((out / '2026-02-17 learning group [catch-up] [with references].md').is_file())
        self.assertEqual(json.loads((out / 'bundle.json').read_text())['meeting']['date'], '2026-02-17')

    def test_concise_stage_rewrites_each_topic_and_groups_comments_into_short_prose(self) -> None:
        result, out, _ = self.run_adapter("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        concise = json.loads((out / "concise.json").read_text())
        self.assertEqual([item['id'] for item in concise['topics']], ['T1', 'T2'])
        for compressed, complete in zip(concise['topics'], self.artifacts['brief']['topics']):
            self.assertIsInstance(compressed['comments'], str)
            self.assertNotEqual(compressed['presentation'], complete['presentation'])
            self.assertNotEqual(compressed['outcome'], complete['outcome'])
            for comment in complete['discussion']:
                self.assertNotIn(comment['summary'], compressed['comments'])
        timing = concise['topics'][1]
        self.assertIn('Casey', timing['comments'])
        self.assertIn('Alex', timing['comments'])
        self.assertIn('weekend', timing['outcome'])
        self.assertIn('open', timing['outcome'])
        self.assertEqual(timing['comment_ids'], ['T2.C1', 'T2.C2'])
        self.assertEqual(timing['unresolved_ids'], ['T2.U1'])
        text = (out / self.report_name).read_text()
        for topic in concise['topics']:
            self.assertIn(topic['title'], text)
            self.assertIn(topic['presentation'], text)
            self.assertIn(topic['comments'], text)
            self.assertIn(topic['outcome'], text)
        for internal in ('T2.C1', 'T2.U1', '[E1]', '原文依据', '评论与回应'):
            self.assertNotIn(internal, text)
        self.assertLess(text.index('Workshop timing'), text.index('下次讨论议题'))

    def test_missing_concise_topic_is_repaired_before_agenda_or_report_generation(self) -> None:
        result, out, adapter = self.run_adapter('concise-missing-topic')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stages(adapter), ['discussion', 'coverage_review', 'brief', 'concise', 'concise', 'agenda'])
        calls = self.calls(adapter)
        self.assertTrue(calls[4]['feedback'])
        self.assertIn('previous_candidate', calls[4]['context_keys'])
        receipt = json.loads((out / 'receipt.json').read_text())
        self.assertTrue(receipt['attempts'][3]['errors'])
        self.assertEqual(receipt['attempts'][4]['errors'], [])
        self.assertEqual(json.loads((out / 'concise.json').read_text()), self.artifacts['concise'])
        self.assertTrue((out / self.report_name).exists())
        self.assertTrue((out / self.reference_name).exists())

    def test_invalid_concise_stage_blocks_agenda_and_both_reports_without_fallback(self) -> None:
        result, out, adapter = self.run_adapter('concise-invalid')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.stages(adapter), ['discussion', 'coverage_review', 'brief', 'concise', 'concise'])
        self.assertTrue((out / 'brief.json').exists())
        self.assertFalse((out / 'concise.json').exists())
        self.assertFalse((out / 'agenda.json').exists())
        self.assertFalse((out / self.report_name).exists())
        self.assertFalse((out / self.reference_name).exists())
        failure = json.loads((out / 'failure.json').read_text())
        self.assertIn('concise', failure['reason'])
        self.assertTrue(failure['attempts'][-1]['errors'])

    def test_run_repairs_malformed_first_response_with_feedback(self) -> None:
        result, out, adapter = self.run_adapter("repair")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stages(adapter), ["discussion", "discussion", "coverage_review", "brief", "concise", "agenda"])
        records = json.loads((out / "receipt.json").read_text())["attempts"]
        self.assertTrue(records[0]["errors"])
        self.assertEqual(records[1]["errors"], [])
        self.assertFalse(self.calls(adapter)[0]["feedback"])
        self.assertTrue(self.calls(adapter)[1]["feedback"])
        self.assertIn("previous_candidate", self.calls(adapter)[1]["context_keys"])

    def test_coverage_revision_repairs_discussion_before_shortening(self) -> None:
        result, out, adapter = self.run_adapter("coverage-once")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stages(adapter), ["discussion", "coverage_review", "discussion", "coverage_review", "brief", "concise", "agenda"])
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
        self.assertFalse((out / self.report_name).exists())
        self.assertFalse((out / self.reference_name).exists())
        self.assertTrue((out / "failure.json").exists())

    def test_brief_cannot_drop_a_topic_or_comment_and_is_repaired_before_agenda(self) -> None:
        for mode in ("missing-topic", "missing-comment"):
            with self.subTest(mode=mode):
                result, out, adapter = self.run_adapter(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.stages(adapter), ["discussion", "coverage_review", "brief", "brief", "concise", "agenda"])
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
        self.assertFalse((out / self.report_name).exists())
        self.assertFalse((out / self.reference_name).exists())

    def test_wrong_speaker_evidence_is_rejected_through_cli(self) -> None:
        result, out, adapter = self.run_adapter("attribution", attempts=1)
        self.assertEqual(result.returncode, 2)
        failure = json.loads((out / "failure.json").read_text())
        self.assertTrue(any("speaker" in error or "Alex" in error for error in failure["attempts"][0]["errors"]))
        self.assertEqual(self.stages(adapter), ["discussion"])
        self.assertFalse((out / "digest.md").exists())
        self.assertFalse((out / self.report_name).exists())
        self.assertFalse((out / self.reference_name).exists())

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
        concise_path = flags[9]
        cases = [
            ("agenda", []),
            ("agenda", ["--discussion", discussion_path]),
            ("agenda", ["--discussion", discussion_path, "--coverage", coverage_path]),
            ("agenda", ["--discussion", discussion_path, "--coverage", coverage_path, "--brief", brief_path]),
            ("brief", ["--discussion", discussion_path]),
            ("concise", ["--discussion", discussion_path, "--coverage", coverage_path]),
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
                          "--brief", brief_path, "--concise", concise_path, "--out", agenda_prompt)
        self.assertEqual(result.returncode, 0, result.stderr)
        stage, payload = FIXTURE["parse_prompt"](agenda_prompt.read_text())
        self.assertEqual(stage, "agenda")
        self.assertNotIn("segments", payload["bundle"])
        self.assertEqual(payload['context']['concise'], self.artifacts['concise'])
        self.assertEqual(payload['context']['concise_sha256'], artifact_hash(self.artifacts['concise']))

    def test_manual_concise_prompt_requires_complete_brief_and_uses_approved_context(self) -> None:
        flags = self.artifact_flags()
        out = self.folder / 'concise.prompt.txt'
        result = self.cli('prompt', '--stage', 'concise', '--bundle', flags[1],
                          '--discussion', flags[3], '--coverage', flags[5], '--brief', flags[7], '--out', out)
        self.assertEqual(result.returncode, 0, result.stderr)
        stage, payload = FIXTURE['parse_prompt'](out.read_text())
        self.assertEqual(stage, 'concise')
        self.assertNotIn('segments', payload['bundle'])
        self.assertEqual(payload['context']['brief'], self.artifacts['brief'])

    def test_manual_check_and_render_require_valid_concise_without_single_version_fallback(self) -> None:
        flags = self.artifact_flags()
        # All historical artifacts alone are insufficient: compression is a
        # separate checked task, never an optional rendering shortcut.
        without_concise = flags[:8] + flags[10:]
        for action in ('check', 'render'):
            with self.subTest(action=action):
                out = self.folder / (action + '-without-concise.md')
                result = self.cli(action, *without_concise, *(['--out', out] if action == 'render' else []))
                self.assertEqual(result.returncode, 2)
                self.assertIn('--concise', result.stderr)
                self.assertFalse(out.exists())
                self.assertFalse(out.with_name(out.stem + ' [with references].md').exists())
        broken = deepcopy(self.artifacts)
        broken['concise']['topics'].pop()
        broken_flags = self.artifact_flags(broken)
        for action in ('check', 'render'):
            with self.subTest(action=action, invalid=True):
                out = self.folder / (action + '-invalid-concise.md')
                result = self.cli(action, *broken_flags, *(['--out', out] if action == 'render' else []))
                self.assertEqual(result.returncode, 2)
                self.assertIn('preserve every topic', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse(out.exists())
                self.assertFalse(out.with_name(out.stem + ' [with references].md').exists())

    def test_manual_prompt_rejects_revision_and_incomplete_brief(self) -> None:
        flags = self.artifact_flags()
        revised = deepcopy(self.artifacts["coverage"])
        revised["status"] = "revise"
        revised["issues"] = [{"kind": "missing_response", "topic_id": "T2",
                              "segment_ids": ["S0006"], "detail": "Check Alex's reply."}]
        coverage_path = self.json_file("coverage-revise.json", revised)
        for stage in ("brief", "concise", "agenda"):
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
        values["concise"] = FIXTURE["stage_output"]("concise", bundle, values)
        values["agenda"] = {"schema_version": 2, "meeting": discussion["meeting"],
                            "discussion_sha256": artifact_hash(discussion), "brief_sha256": artifact_hash(values["brief"]),
                            "items": [{"id": "A1", "kind": "synthesizer_suggestion", "title": "Explore the first topic.",
                                       "reason": "Summarizer suggestion based on Alex's first share.", "based_on": ["T1"], "recorded_proposal_id": None}],
                            "deferred_recorded_proposals": []}
        result = self.cli("check", *self.artifact_flags(values, bundle))
        self.assertEqual(result.returncode, 0, result.stderr)
        context = {**values, **{name + "_sha256": artifact_hash(value) for name, value in values.items()}}
        for stage in ("discussion", "coverage_review", "brief", "concise"):
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
        self.assertFalse((out / self.report_name).exists())
        self.assertFalse((out / self.reference_name).exists())

    def test_nonzero_adapter_exit_preserves_private_diagnostics(self) -> None:
        result, out, _ = self.run_adapter("exit")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("fictional diagnostic", result.stderr)
        self.assertIn("fictional diagnostic", (out / "01-discussion.stderr.txt").read_text())
        self.assertEqual(json.loads((out / "failure.json").read_text())["status"], "failed")
        self.assertFalse((out / "digest.md").exists())
        self.assertFalse((out / self.report_name).exists())
        self.assertFalse((out / self.reference_name).exists())

    def test_prompt_limit_stops_before_model_adapter_starts(self) -> None:
        result, out, adapter = self.run_adapter("limit", extra=("--max-prompt-chars", "10"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("No text was truncated or sent", result.stderr)
        self.assertFalse(adapter.with_suffix(".calls.json").exists())
        self.assertFalse((out / "digest.md").exists())
        self.assertFalse((out / self.report_name).exists())
        self.assertFalse((out / self.reference_name).exists())

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

    def test_render_existing_either_version_preserves_both_paths(self) -> None:
        flags = self.artifact_flags()
        for version in ('summary.md', 'summary [with references].md'):
            with self.subTest(existing=version):
                folder = self.folder / version.removesuffix('.md')
                folder.mkdir()
                existing = folder / version
                existing.write_text('Existing fictional report', encoding='utf-8')
                result = self.cli('render', *flags, '--out', folder / 'summary.md')
                self.assertEqual(result.returncode, 2)
                self.assertIn('already exists', result.stderr)
                self.assertEqual(existing.read_text(), 'Existing fictional report')
                self.assertEqual(list(folder.iterdir()), [existing])

    def test_report_pair_rolls_back_first_if_second_write_fails(self) -> None:
        concise = self.folder / 'summary.md'
        def fail_second(path, content):
            if path.name.endswith(' [with references].md'):
                raise OSError('Simulated disk failure')
            write_private(path, content)
        with patch('meeting_harness.cli.write_private', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                write_report_pair(concise, 'Fictional concise', 'Fictional references')
        self.assertFalse(concise.exists())
        self.assertFalse(concise.with_name('summary [with references].md').exists())

    def test_report_pair_rejects_existing_dangling_companion_symlink(self) -> None:
        concise = self.folder / 'summary.md'
        reference = self.folder / 'summary [with references].md'
        target = self.folder / 'uncreated.md'
        reference.symlink_to(target)
        with self.assertRaisesRegex(HarnessError, 'already exists'):
            write_report_pair(concise, 'Fictional concise', 'Fictional references')
        self.assertTrue(reference.is_symlink())
        self.assertFalse(target.exists())
        self.assertFalse(concise.exists())

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
