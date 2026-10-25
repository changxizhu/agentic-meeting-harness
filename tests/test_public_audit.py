import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout


spec = importlib.util.spec_from_file_location("public_audit", Path(__file__).parents[1] / "tools" / "audit_public.py")
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class PublicAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = audit_module.ROOT
        audit_module.ROOT = self.root
        self.git("init", "-q")
        self.git("config", "user.name", "Fictional Test")
        self.git("config", "user.email", "fiction@example.invalid")
        (self.root / "public-files.json").write_text(json.dumps({"files": ["public-files.json", "README.md"]}))
        (self.root / "README.md").write_text("Fictional public example.\n")
        self.git("add", "public-files.json", "README.md")

    def tearDown(self):
        audit_module.ROOT = self.original
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL)

    def audit(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return audit_module.audit()

    def test_allowlisted_public_index_passes(self):
        self.assertEqual(self.audit(), 0)

    def test_force_added_private_file_is_rejected(self):
        (self.root / ".gitignore").write_text("private/\n")
        (self.root / "private").mkdir()
        (self.root / "private" / "fictional.txt").write_text("SYNTHETIC_PRIVATE_CANARY")
        self.git("add", "-f", "private/fictional.txt")
        self.assertEqual(self.audit(), 1)

    def test_deleted_private_file_in_history_still_rejected(self):
        (self.root / "fictional-secret.txt").write_text("SYNTHETIC_PRIVATE_CANARY")
        self.git("add", "fictional-secret.txt")
        self.git("commit", "-qm", "Synthetic history case")
        self.git("rm", "-q", "fictional-secret.txt")
        self.git("commit", "-qm", "Remove synthetic case")
        self.assertEqual(self.audit(), 1)

    def test_scans_indexed_content_not_working_copy(self):
        # Build the fake credential in pieces so this test file is safe to publish.
        (self.root / "README.md").write_text("ghp_" + "Q" * 35)
        self.git("add", "README.md")
        (self.root / "README.md").write_text("Clean working copy does not change the staged bytes.")
        self.assertEqual(self.audit(), 1)


if __name__ == "__main__":
    unittest.main()
