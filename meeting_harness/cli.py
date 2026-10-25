"""Ordered discussion, coverage review, brief, and only then next agenda."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from .ingest import HarnessError, latest_source, make_bundle
from .prompts import build_prompt, schema_for
from .review import (artifact_hash, render_concise_markdown, render_markdown, validate_agenda, validate_brief,
                     validate_bundle, validate_coverage, validate_discussion)

STAGES = ('discussion', 'coverage_review', 'brief', 'agenda')


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def private_destination(path: Path) -> Path:
    dest = path.resolve()
    project = Path(__file__).resolve().parent.parent
    if (project / 'public-files.json').exists() and dest.is_relative_to(project):
        # Compare the resolved destination with literal local-only roots. Resolving
        # these roots themselves would trust a symlink into public code folders.
        local_roots = (project / 'Meetings', project / 'private')
        if not any(dest.is_relative_to(root) for root in local_roots):
            raise HarnessError('Keep generated artifacts outside the public project, or under its ignored Meetings/ or private/ directory.')
    return dest


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise HarnessError('Output already exists. Choose a new output folder or filename.') from exc
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def write_report_pair(path: Path, concise: str, referenced: str) -> tuple[Path, Path]:
    """Always produce both views, preserving either existing output on failure."""
    requested = (path, path.with_name(path.stem + '_with_references.md'))
    if path.suffix.lower() != '.md' or path.stem.lower().endswith('_with_references'):
        raise HarnessError('Use a concise Markdown filename such as summary.md; its reference companion is created automatically.')
    # Check literal paths too: resolving a dangling symlink must not authorize
    # replacing it or writing to its missing target.
    if any(item.exists() or item.is_symlink() for item in requested):
        raise HarnessError('A report already exists. Archive both versions privately or choose a new output filename.')
    destinations = tuple(private_destination(item) for item in requested)
    if any(item.exists() or item.is_symlink() for item in destinations):
        raise HarnessError('A report already exists. Archive both versions privately or choose a new output filename.')
    created = []
    try:
        for target, content in zip(destinations, (concise, referenced)):
            write_private(target, content)
            created.append(target)
    except Exception:
        for target in created:
            target.unlink(missing_ok=True)
        raise
    return destinations


def load_json(path: Path) -> dict:
    try:
        if path.stat().st_size > 10_000_000:
            raise HarnessError('JSON exceeds the 10 MB input limit.')
        value = json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError) as exc:
        if isinstance(exc, HarnessError):
            raise
        raise HarnessError('Cannot read a valid JSON object from the selected file.') from exc


def require_valid(errors: list[str]) -> None:
    if errors:
        raise HarnessError('Validation failed:\n' + '\n'.join(errors))


def stage_context(discussion=None, coverage=None, brief=None) -> dict:
    context = {}
    for name, value in (('discussion', discussion), ('coverage', coverage), ('brief', brief)):
        if value is not None:
            context[name] = value
            context[name + '_sha256'] = artifact_hash(value)
    return context


def prepare(args) -> tuple[dict, Path]:
    out = private_destination(args.out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise HarnessError('Output directory must be new or empty to preserve previous runs.')
    source = args.source or latest_source(args.meetings_dir)
    bundle = make_bundle(source, args.meeting_date)
    write_private(out / 'bundle.json', dump(bundle))
    write_private(out / 'discussion.prompt.txt', build_prompt('discussion', bundle, language=args.language))
    write_private(out / 'discussion.schema.json', dump(schema_for('discussion')))
    write_private(out / 'prepared.json', dump({'status': 'prepared', 'schema_version': 2,
                  'source_sha256': bundle['meeting']['source_sha256'], 'segment_count': len(bundle['segments'])}))
    return bundle, out


def load_prior(args, stage: str):
    bundle = load_json(args.bundle)
    require_valid(validate_bundle(bundle))
    discussion = coverage = brief = agenda = None
    if stage != 'discussion':
        if not args.discussion:
            raise HarnessError('Complete the discussion stage first; --discussion is required.')
        discussion = load_json(args.discussion)
        require_valid(validate_discussion(discussion, bundle))
    if stage in ('brief', 'agenda', 'complete'):
        if not args.coverage:
            raise HarnessError('A passed coverage review is required before shortening the meeting or proposing an agenda.')
        coverage = load_json(args.coverage)
        require_valid(validate_coverage(coverage, discussion, bundle))
        if coverage['status'] != 'pass':
            raise HarnessError('Coverage review requires revision; repair the discussion before proceeding.')
    if stage in ('agenda', 'complete'):
        if not args.brief:
            raise HarnessError('Complete the meeting brief first; --brief is required.')
        brief = load_json(args.brief)
        require_valid(validate_brief(brief, discussion, coverage, bundle))
    if stage == 'complete':
        if not args.agenda:
            raise HarnessError('The final agenda artifact is required.')
        agenda = load_json(args.agenda)
        require_valid(validate_agenda(agenda, discussion, brief, bundle))
    return bundle, discussion, coverage, brief, agenda


class ModelRunner:
    def __init__(self, args, bundle, out):
        self.args, self.bundle, self.out = args, bundle, out
        self.records = []
        self.counter = 0
        try:
            self.command = json.loads(args.command_json)
        except ValueError as exc:
            raise HarnessError('--command-json must be a JSON array of command arguments.') from exc
        if not isinstance(self.command, list) or not self.command or not all(isinstance(x, str) and x for x in self.command):
            raise HarnessError('Model command must be a nonempty array of strings.')

    def generate(self, stage, context, validator):
        errors = []
        previous = None
        for attempt in range(1, self.args.attempts + 1):
            supplied = dict(context)
            if previous is not None:
                supplied['previous_candidate'] = previous
            prompt = build_prompt(stage, self.bundle, context=supplied, language=self.args.language, errors=errors)
            if len(prompt) > self.args.max_prompt_chars:
                raise HarnessError('Prompt exceeds configured character limit. No text was truncated or sent; increase the limit or explicitly split the source.')
            self.counter += 1
            prefix = f'{self.counter:02d}-{stage}'
            write_private(self.out / (prefix + '.prompt.txt'), prompt)
            try:
                result = subprocess.run(self.command, input=prompt, text=True, encoding='utf-8', capture_output=True,
                                        timeout=self.args.timeout, check=False)
            except subprocess.TimeoutExpired as exc:
                raise HarnessError('Model command timed out; no final digest was produced.') from exc
            except (OSError, UnicodeError) as exc:
                raise HarnessError('Model command could not run or returned invalid text.') from exc
            write_private(self.out / (prefix + '.stderr.txt'), result.stderr)
            if result.returncode:
                raise HarnessError('Model command failed; diagnostic output is in the private run folder.')
            if len(result.stdout) > 2_000_000:
                raise HarnessError('Model response exceeds the 2 MB response limit.')
            previous = result.stdout
            write_private(self.out / (prefix + '.response.txt'), previous)
            try:
                candidate = json.loads(previous, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                errors = validator(candidate)
            except ValueError:
                errors = ['Output must be one JSON object without Markdown fences, commentary, or non-JSON numbers.']
            self.records.append({'stage': stage, 'attempt': attempt, 'errors': errors})
            if not errors:
                return candidate
        raise HarnessError(f'{stage} did not pass validation within the attempt limit; no final digest was produced.')


def run_model(args) -> None:
    # Validate the trusted command before creating a run folder.
    try:
        command = json.loads(args.command_json)
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise ValueError()
    except ValueError as exc:
        raise HarnessError('Model command must be a nonempty JSON array of strings.') from exc
    bundle, out = prepare(args)
    runner = ModelRunner(args, bundle, out)
    try:
        repair = {}
        for review_round in range(1, args.attempts + 1):
            discussion = runner.generate('discussion', repair, lambda x: validate_discussion(x, bundle))
            coverage = runner.generate('coverage_review', stage_context(discussion),
                                       lambda x: validate_coverage(x, discussion, bundle))
            if coverage['status'] == 'pass':
                break
            runner.records.append({'stage': 'coverage_revision', 'round': review_round, 'issues': coverage['issues']})
            repair = {'previous_discussion': discussion, 'coverage_feedback': coverage}
        else:
            raise HarnessError('Coverage review still identifies missing or misrepresented discussion; no brief or agenda was generated.')
        write_private(out / 'discussion.json', dump(discussion))
        write_private(out / 'coverage.json', dump(coverage))
        brief = runner.generate('brief', stage_context(discussion, coverage),
                                lambda x: validate_brief(x, discussion, coverage, bundle))
        write_private(out / 'brief.json', dump(brief))
        agenda = runner.generate('agenda', stage_context(discussion, coverage, brief),
                                 lambda x: validate_agenda(x, discussion, brief, bundle))
        write_private(out / 'agenda.json', dump(agenda))
        reports = write_report_pair(out / 'summary.md',
                                    render_concise_markdown(discussion, coverage, brief, agenda, bundle),
                                    render_markdown(discussion, coverage, brief, agenda, bundle))
        write_private(out / 'receipt.json', dump({'schema_version': 2, 'status': 'ordered_workflow_completed',
                      'source_sha256': bundle['meeting']['source_sha256'], 'discussion_sha256': artifact_hash(discussion),
                      'topic_count': len(discussion['topics']), 'stage_order': STAGES, 'attempts': runner.records,
                      'reports': {'concise': reports[0].name, 'with_references': reports[1].name},
                      'review_limits': 'A coverage-review pass is a model assessment, not proof of semantic completeness. Human review remains useful.'}))
    except HarnessError as exc:
        write_private(out / 'failure.json', dump({'status': 'failed', 'reason': str(exc), 'attempts': runner.records}))
        raise
    print('Both reports created: summary.md (concise) and summary_with_references.md (with source references).')


def parser():
    root = argparse.ArgumentParser(description='Restore complete meeting discussion before selecting the next agenda.')
    sub = root.add_subparsers(dest='action', required=True)
    for action in ('prepare', 'run'):
        p = sub.add_parser(action)
        src = p.add_mutually_exclusive_group(required=True)
        src.add_argument('--source', type=Path)
        src.add_argument('--meetings-dir', type=Path)
        p.add_argument('--meeting-date')
        p.add_argument('--out', type=Path, required=True)
        p.add_argument('--language', choices=('en', 'zh'), default='zh')
        if action == 'run':
            p.add_argument('--command-json', required=True)
            p.add_argument('--attempts', type=int, choices=range(1, 4), default=2)
            p.add_argument('--timeout', type=int, default=300)
            p.add_argument('--max-prompt-chars', type=int, default=400000)
    for action in ('prompt', 'check', 'render'):
        p = sub.add_parser(action)
        p.add_argument('--bundle', type=Path, required=True)
        for name in ('discussion', 'coverage', 'brief', 'agenda'):
            p.add_argument('--' + name, type=Path)
        if action == 'prompt':
            p.add_argument('--stage', choices=STAGES, required=True)
            p.add_argument('--language', choices=('en', 'zh'), default='zh')
        if action in ('prompt', 'render'):
            p.add_argument('--out', type=Path, required=True,
                           help='For render: concise Markdown filename; the reference companion is always created beside it.')
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == 'prepare':
            bundle, _ = prepare(args)
            print(f"Prepared {len(bundle['segments'])} source segments. Restore all discussion before selecting an agenda.")
        elif args.action == 'run':
            if args.timeout < 1 or args.max_prompt_chars < 1:
                raise HarnessError('Timeout and prompt limit must be positive.')
            run_model(args)
        else:
            stage = args.stage if args.action == 'prompt' else 'complete'
            bundle, discussion, coverage, brief, agenda = load_prior(args, stage)
            if args.action == 'prompt':
                content = build_prompt(stage, bundle, context=stage_context(discussion, coverage, brief), language=args.language)
                write_private(private_destination(args.out), content)
                print('Prompt created after checking the required prior stages.')
            elif args.action == 'render':
                reports = write_report_pair(args.out,
                                            render_concise_markdown(discussion, coverage, brief, agenda, bundle),
                                            render_markdown(discussion, coverage, brief, agenda, bundle))
                print(f'Both reports rendered: {reports[0].name} (concise) and {reports[1].name} (with source references).')
            else:
                print('Stage order, complete source mapping, discussion links, exact evidence and agenda provenance passed structural checks.')
        return 0
    except (HarnessError, OSError, ValueError) as exc:
        print(f'Meeting harness: {exc}', file=sys.stderr)
        return 2
