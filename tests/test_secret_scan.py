"""Tests for scripts/secret_scan.py — the toolkit's regex secret scanner.

Unit tests cover pattern detection, snippet truncation, binary/skip-file
handling. E2E tests drive the CLI inside an isolated git repository,
covering the CI mode (`git ls-files`), the pre-commit mode (`--staged`),
and explicit-path mode — including exit codes. An additional in-process
class drives main() directly so the CLI dispatch and git-collection
helpers stay visible to the coverage tracer.

All secret-shaped fixture strings are BUILT AT RUNTIME by concatenation
so that this file's own source text contains no literal matching the
scanner patterns — otherwise the toolkit's self-scan would flag its own
test suite.
"""

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "secret_scan.py"

# secret_scan is importable via the conftest.py sys.path bootstrap.
import secret_scan  # noqa: E402
from git_repo_base import TempGitRepoTestCase  # noqa: E402

# Runtime-built fake credentials (see module docstring).
PAT_FAKE = "ghp_" + "a" * 20
OR_KEY_FAKE = "sk-or-v1-" + "abc123"
PEM_RSA_FAKE = "-----BEGIN " + "RSA PRIVATE KEY-----"
PEM_OPENSSH_FAKE = "-----BEGIN " + "OPENSSH " + "PRIVATE KEY-----"
LEAK_LINE = f't = "{PAT_FAKE}"\n'


class TestIsBinary(unittest.TestCase):
    def test_text_file_is_not_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clean.py"
            path.write_text("x = 1\n", encoding="utf-8")
            self.assertFalse(secret_scan.is_binary(path))

    def test_file_with_nul_byte_is_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob.bin"
            path.write_bytes(b"abc\x00def")
            self.assertTrue(secret_scan.is_binary(path))

    def test_unreadable_path_treated_as_binary(self):
        # A directory raises on open() — the defensive branch treats it as
        # binary and skips it.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(secret_scan.is_binary(Path(tmp)))


class TestScanText(unittest.TestCase):
    def test_clean_text_has_no_findings(self):
        self.assertEqual(secret_scan.scan_text("x = 1\n", "a.py"), [])

    def test_detects_github_pat(self):
        text = f'token = "{PAT_FAKE}"\n'
        findings = secret_scan.scan_text(text, "a.py")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][0], "a.py")
        self.assertEqual(findings[0][1], "github-pat")

    def test_detects_openrouter_key(self):
        findings = secret_scan.scan_text(f"key={OR_KEY_FAKE}\n", "cfg.py")
        self.assertEqual(findings[0][1], "openrouter-key")

    def test_detects_pem_private_key_header(self):
        findings = secret_scan.scan_text(PEM_RSA_FAKE + "\n", "k.pem")
        self.assertEqual(findings[0][1], "pem-private-key")

    def test_detects_openssh_private_key_header(self):
        findings = secret_scan.scan_text(PEM_OPENSSH_FAKE + "\n", "k.pem")
        self.assertEqual(findings[0][1], "pem-private-key")

    def test_detects_high_entropy_base64_block(self):
        # Two consecutive lines of 40+ base64 chars each. Built at runtime
        # so the raw source text never contains the pattern itself.
        b64_line = "QUJD" * 10 + "QUJD"  # 44 base64 alphabet chars
        text = f"{b64_line}\n{b64_line}\n"
        findings = secret_scan.scan_text(text, "blob.txt")
        self.assertEqual([f[1] for f in findings], ["high-entropy-base64"])

    def test_single_short_base64_line_is_ignored(self):
        self.assertEqual(secret_scan.scan_text("c2hvcnQ=\n", "a.py"), [])

    def test_snippet_truncated_to_80_chars(self):
        long_pat = "ghp_" + "a" * 120
        findings = secret_scan.scan_text(long_pat + "\n", "a.py")
        self.assertLessEqual(len(findings[0][2]), 80)


class TestScanFile(unittest.TestCase):
    def test_env_example_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.example"
            path.write_text(f"{PAT_FAKE}\n", encoding="utf-8")
            self.assertEqual(secret_scan.scan_file(path), [])

    def test_uv_lock_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "uv.lock"
            path.write_text(f"{OR_KEY_FAKE}\n", encoding="utf-8")
            self.assertEqual(secret_scan.scan_file(path), [])

    def test_package_lock_json_is_skipped(self):
        # npm lockfiles carry sha512 base64 integrity hashes of public
        # package tarballs — content addresses, not secrets.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "package-lock.json"
            b64 = "QUJD" * 10 + "QUJD"
            path.write_text(f'"integrity": "sha512-{b64}"\n', encoding="utf-8")
            self.assertEqual(secret_scan.scan_file(path), [])

    def test_yarn_lock_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yarn.lock"
            b64 = "QUJD" * 10 + "QUJD"
            path.write_text(f'integrity "sha512-{b64}"\n', encoding="utf-8")
            self.assertEqual(secret_scan.scan_file(path), [])

    def test_real_secrets_still_scanned_outside_lockfiles(self):
        # The lockfile skip must not leak into normal source files.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "package.json"
            path.write_text(f'{{"k": "{PAT_FAKE}"}}\n', encoding="utf-8")
            findings = secret_scan.scan_file(path)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0][1], "github-pat")

    def test_binary_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob.bin"
            path.write_bytes(f"{PAT_FAKE}\x00".encode())
            self.assertEqual(secret_scan.scan_file(path), [])

    def test_text_file_is_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak.py"
            path.write_text(LEAK_LINE, encoding="utf-8")
            findings = secret_scan.scan_file(path)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0][1], "github-pat")


class TestCliInGitRepo(TempGitRepoTestCase):
    def _run_scanner(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

    def test_clean_tracked_tree_passes(self):
        result = self._run_scanner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No secrets detected", result.stdout)

    def test_tracked_secret_fails(self):
        self._write("leak.py", LEAK_LINE)
        self._commit("add leak")
        result = self._run_scanner()
        self.assertEqual(result.returncode, 1)
        self.assertIn("github-pat", result.stderr)
        self.assertIn("leak.py", result.stderr)

    def test_explicit_path_mode_scans_only_named_paths(self):
        self._write("leak.py", LEAK_LINE)
        self._commit("add leak")
        # Naming only the clean file must pass even though the tree is dirty.
        result = self._run_scanner("README.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        # Naming the leaking file must fail.
        result = self._run_scanner("leak.py")
        self.assertEqual(result.returncode, 1)

    def test_nonexistent_explicit_paths_are_filtered(self):
        result = self._run_scanner("does-not-exist.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No secrets detected", result.stdout)

    def test_staged_mode_ignores_unstaged_secret(self):
        self._write("unstaged_leak.py", LEAK_LINE)
        # File exists but is NOT staged -> staged scan passes.
        result = self._run_scanner("--staged")
        self.assertEqual(result.returncode, 0, result.stderr)
        # Staging it -> staged scan fails.
        self._git("add", "unstaged_leak.py")
        result = self._run_scanner("--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("github-pat", result.stderr)


class TestMainInProcess(TempGitRepoTestCase):
    """Drive main() in-process so CLI dispatch and the git-collection
    helpers are visible to the coverage tracer (the subprocess E2E class
    above proves the same behavior end-to-end, but coverage.py cannot see
    into child processes)."""

    def _run_main(self, *args: str):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["secret_scan.py", *args]
        with contextlib.chdir(self.repo), mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = secret_scan.main()
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_clean_tracked_tree_passes_in_process(self):
        rc, out, err = self._run_main()
        self.assertEqual(rc, 0)
        self.assertIn("No secrets detected", out)

    def test_tracked_secret_fails_in_process(self):
        self._write("leak.py", LEAK_LINE)
        self._commit("add leak")
        rc, out, err = self._run_main()
        self.assertEqual(rc, 1)
        self.assertIn("github-pat", err)

    def test_staged_mode_fails_in_process(self):
        self._write("staged_leak.py", LEAK_LINE)
        self._git("add", "staged_leak.py")
        rc, out, err = self._run_main("--staged")
        self.assertEqual(rc, 1)
        self.assertIn("github-pat", err)

    def test_git_collection_helpers(self):
        self._write("leak.py", "t = 1\n")
        self._git("add", "leak.py")
        with contextlib.chdir(self.repo):
            staged = secret_scan.get_staged_files()
            tracked = secret_scan.get_all_files()
        self.assertIn(Path("leak.py"), staged)
        self.assertIn(Path("README.md"), tracked)


if __name__ == "__main__":
    unittest.main()
