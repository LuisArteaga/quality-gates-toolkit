"""Shared unittest base building an isolated git repository.

Used by tests that exercise git-aware CLIs (diff-coverage gate, secret
scanner): each test gets a throwaway repo with one committed README so the
merge-base/tracked-file machinery has real history to work against.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path


class TempGitRepoTestCase(unittest.TestCase):
    """Base helper building an isolated git repository."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self._git("init", "-b", "main")
        self._write("README.md", "# temp repo\n")
        self._commit("initial")

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def _write(self, rel_path: str, content: str) -> Path:
        target = self.repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _commit(self, message: str) -> None:
        self._git("add", "-A")
        self._git(
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-m",
            message,
        )
