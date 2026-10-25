#!/usr/bin/env python3
"""Check the Git index and reachable history against an explicit public allowlist.

This is a backstop, not a detector for all confidential information. Only add
files you have reviewed as public. Run before the first and every later push.
"""

import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
SECRET_PATTERNS = (
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"gh[pousr]_[A-Za-z0-9]{30,}",
    rb"github_pat_[A-Za-z0-9_]{30,}",
    rb"AKIA[A-Z0-9]{16}",
    rb"sk-[A-Za-z0-9_-]{24,}",
)
FORBIDDEN_PARTS = {"private", "material", "materials", "meetings", "outputs", "runs", "privateoutputs", ".env"}
FORBIDDEN_EXTENSIONS = {".docx", ".pdf", ".mp3", ".mp4", ".wav", ".zip", ".png", ".jpg"}


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], stderr=subprocess.DEVNULL)


def audit():
    manifest = json.loads((ROOT / "public-files.json").read_text())
    allowed = set(manifest["files"])
    issues = []
    checked_blobs = set()

    def check(path, blob):
        parts = PurePosixPath(path)
        if path not in allowed:
            issues.append(f"Unapproved tracked path: {path}")
        if any(p.lower() in FORBIDDEN_PARTS or p.lower().startswith('.env') for p in parts.parts):
            issues.append(f"Private path in Git: {path}")
        if parts.suffix.lower() in FORBIDDEN_EXTENSIONS:
            issues.append(f"Source/media file in Git: {path}")
        if blob in checked_blobs:
            return
        checked_blobs.add(blob)
        content = git("cat-file", "blob", blob)
        try:
            content.decode("utf-8")
        except UnicodeError:
            issues.append(f"Non-text blob: {path}")
        if any(re.search(pattern, content) for pattern in SECRET_PATTERNS):
            issues.append(f"Possible credential in: {path}")

    # Inspect indexed bytes, including forced additions to ignored paths.
    for entry in git("ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, blob, stage = metadata.decode().split()
        path = raw_path.decode("utf-8")
        if mode not in {"100644", "100755"} or stage != "0":
            issues.append(f"Unsupported index entry: {path}")
            continue
        check(path, blob)
    # Deleting a private file from the latest commit does not erase its history.
    for entry in git("rev-list", "--objects", "--all").decode().splitlines():
        fields = entry.split(" ", 1)
        if len(fields) == 2 and git("cat-file", "-t", fields[0]).strip() == b"blob":
            check(fields[1], fields[0])
    if issues:
        print("Public audit FAILED:\n" + "\n".join(sorted(set(issues))), file=sys.stderr)
        return 1
    if not checked_blobs:
        print("Public audit FAILED: no indexed or committed files.", file=sys.stderr)
        return 1
    print(f"Public audit passed for {len(checked_blobs)} text blobs in the index and reachable history.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(audit())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        print("Public audit could not complete; do not publish until resolved.", file=sys.stderr)
        raise SystemExit(1)
