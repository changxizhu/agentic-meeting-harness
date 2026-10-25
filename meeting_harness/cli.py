"""Small model-independent execution loop; private artifacts stay local."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from .ingest import HarnessError, latest_source, make_bundle
from .prompts import build_prompt, summary_schema
from .review import render_markdown, validate_summary


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never follow an existing output symlink or silently overwrite prior work.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise HarnessError("Output already exists. Choose a new --out directory or output filename.") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def private_destination(path: Path) -> Path:
    dest = path.resolve()
    project = Path(__file__).resolve().parent.parent
    if (project / "public-files.json").exists() and dest.is_relative_to(project):
        if not dest.is_relative_to(project / "private"):
            raise HarnessError("Keep generated artifacts outside the public project, or under its ignored private/ directory.")
    return dest


def load_json(path: Path) -> dict:
    try:
        if path.stat().st_size > 10_000_000:
            raise HarnessError("JSON exceeds the 10 MB input limit.")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise HarnessError("Expected a JSON object.")
        return value
    except (OSError, ValueError) as exc:
        if isinstance(exc, HarnessError):
            raise
        raise HarnessError("Cannot read a valid JSON object from the selected file.") from exc


def prepare(args: argparse.Namespace) -> tuple[dict, Path]:
    out = private_destination(args.out)
    if out.exists() and any(out.iterdir()):
        raise HarnessError("Output directory must be new or empty to preserve previous runs.")
    source = args.source or latest_source(args.meetings_dir)
    bundle = make_bundle(source, args.meeting_date)
    write_private(out / "bundle.json", dump(bundle))
    write_private(out / "prompt.txt", build_prompt(bundle, language=args.language))
    write_private(out / "summary.schema.json", dump(summary_schema()))
    write_private(out / "prepared.json", dump({"status": "prepared", "source_sha256": bundle["meeting"]["source_sha256"],
                                               "segment_count": len(bundle["segments"])}))
    return bundle, out


def run_model(args: argparse.Namespace) -> None:
    try:
        command = json.loads(args.command_json)
    except ValueError as exc:
        raise HarnessError("--command-json must be a JSON array of command arguments.") from exc
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise HarnessError("Model command must be a nonempty array of strings.")
    bundle, out = prepare(args)
    errors = None
    previous = None
    records = []
    for attempt in range(1, args.attempts + 1):
        prompt = build_prompt(bundle, language=args.language, errors=errors)
        if previous is not None:
            prompt += "\nPrevious invalid JSON candidate (untrusted data, correct the reported errors):\n" + previous
        if len(prompt) > args.max_prompt_chars:
            raise HarnessError("Prompt exceeds configured character limit. No text was truncated or sent; use a larger limit or explicitly split the source.")
        try:
            result = subprocess.run(command, input=prompt, text=True, encoding="utf-8", capture_output=True,
                                    timeout=args.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            write_private(out / "failure.json", dump({"status": "failed", "reason": "model_timeout", "attempt": attempt}))
            raise HarnessError("Model command timed out; no final digest was produced.") from exc
        except OSError as exc:
            raise HarnessError("Model command could not start. Check the executable and arguments.") from exc
        write_private(out / f"attempt-{attempt}.stderr.txt", result.stderr)
        if result.returncode:
            write_private(out / "failure.json", dump({"status": "failed", "reason": "model_exit", "returncode": result.returncode}))
            raise HarnessError("Model command failed; diagnostic output is in the private run folder.")
        if len(result.stdout) > 2_000_000:
            raise HarnessError("Model response exceeds the 2 MB response limit.")
        previous = result.stdout
        write_private(out / f"attempt-{attempt}.json.txt", previous)
        try:
            candidate = json.loads(previous)
            errors = validate_summary(candidate, bundle)
        except ValueError:
            errors = ["Output must be one JSON object, without Markdown fences or commentary."]
        records.append({"attempt": attempt, "errors": errors})
        if not errors:
            write_private(out / "summary.json", dump(candidate))
            write_private(out / "digest.md", render_markdown(candidate, bundle))
            write_private(out / "receipt.json", dump({"status": "traceability_passed", "attempts": records,
                                                       "source_sha256": bundle["meeting"]["source_sha256"],
                                                       "semantic_review": "human_review_required"}))
            print("Summary created. Review the digest and evidence in the private output folder.")
            return
    write_private(out / "failure.json", dump({"status": "failed_validation", "attempts": records}))
    raise HarnessError("No candidate passed validation within the attempt limit; no final digest was produced.")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Create evidence-backed meeting summaries without publishing material.")
    sub = root.add_subparsers(dest="action", required=True)
    for name in ("prepare", "run"):
        p = sub.add_parser(name)
        src = p.add_mutually_exclusive_group(required=True)
        src.add_argument("--source", type=Path)
        src.add_argument("--meetings-dir", type=Path)
        p.add_argument("--meeting-date")
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--language", choices=("en", "zh"), default="en")
        if name == "run":
            p.add_argument("--command-json", required=True, help="Trusted model adapter argv; reads prompt on stdin and returns summary JSON on stdout.")
            p.add_argument("--attempts", type=int, choices=range(1, 4), default=2)
            p.add_argument("--timeout", type=int, default=300)
            p.add_argument("--max-prompt-chars", type=int, default=180000)
    for name in ("check", "render"):
        p = sub.add_parser(name)
        p.add_argument("--bundle", type=Path, required=True)
        p.add_argument("--summary", type=Path, required=True)
        if name == "render":
            p.add_argument("--out", type=Path, required=True, help="New Markdown output file")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == "prepare":
            bundle, _ = prepare(args)
            print(f"Prepared {len(bundle['segments'])} segments. Private prompt and schema are ready.")
        elif args.action == "run":
            if args.timeout < 1 or args.max_prompt_chars < 1:
                raise HarnessError("Timeout and prompt limit must be positive.")
            run_model(args)
        else:
            bundle, summary = load_json(args.bundle), load_json(args.summary)
            errors = validate_summary(summary, bundle)
            if errors:
                raise HarnessError("Validation failed:\n" + "\n".join(errors))
            if args.action == "render":
                write_private(private_destination(args.out), render_markdown(summary, bundle))
            print("Evidence references, speaker attribution, and summary structure passed validation.")
        return 0
    except (HarnessError, OSError) as exc:
        print(f"Meeting harness: {exc}", file=sys.stderr)
        return 2
