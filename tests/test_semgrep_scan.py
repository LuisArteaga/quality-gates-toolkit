#!/usr/bin/env python3
"""Tests for scripts/semgrep_scan.py — the bounded configuration-load retry.

The policy under test (D-0025): `semgrep-scan <args>` forwards to
`semgrep scan <args>`; when a run fails on its *configuration* rather than
on findings, it is retried a bounded number of times with a short backoff,
and the final attempt's exit code and output are passed through unchanged.

Two contract details come from probes against semgrep 1.177.0 (recorded on
issue #50) and are pinned here:

- a registry fetch failure (HTTP 403/404) exits 7 with
  `[ERROR] Failed to download configuration from <url> HTTP <status>.` on
  stderr;
- with `--quiet` — which the README's args guidance leads consumers to —
  that message is suppressed entirely while the exit code stays 7, so the
  retry decision cannot depend on the message.

An E2E class drives the real CLI with a fake `semgrep` on PATH, covering
the deployment shape the workflows use (`python3 <checkout>/scripts/
semgrep_scan.py ...`).
"""

import ast
import contextlib
import io
import os
import runpy
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "semgrep_scan.py"

# semgrep_scan is importable via the conftest.py sys.path bootstrap.
import semgrep_scan  # noqa: E402

# The exact stderr shape a registry fetch failure produces (semgrep 1.177.0,
# verified with a nonexistent ruleset name — a 404 rather than the reported
# 403, same message shape and exit code).
FETCH_FAILURE_STDERR = (
    "[ERROR] Failed to download configuration from "
    "https://semgrep.dev/c/auto HTTP 403.\n"
    "[ERROR] invalid configuration file found (1 configs were invalid)\n"
)
FINDINGS_STDOUT = "Ran 1 rule on 1 file: 1 finding.\n"


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["semgrep", "scan"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class _ScriptedRunner:
    """Fake subprocess.run: pops one scripted result per call."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _Recorder:
    def __init__(self):
        self.delays = []

    def __call__(self, delay):
        self.delays.append(delay)


class SemgrepScanRetryTests(unittest.TestCase):
    def _run(self, results, argv=("--config=auto", "src")):
        runner = _ScriptedRunner(results)
        sleep = _Recorder()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = semgrep_scan.main(list(argv), runner=runner, sleep=sleep)
        return code, runner, sleep, stdout.getvalue(), stderr.getvalue()

    def test_arguments_are_forwarded_verbatim_to_semgrep_scan(self):
        """The wrapper is `semgrep scan` plus a policy — nothing else."""
        _, runner, _, _, _ = self._run([_completed(0)])
        assert runner.calls[0][0] == ["semgrep", "scan", "--config=auto", "src"]

    def test_clean_run_passes_through_without_retrying(self):
        code, runner, sleep, stdout, stderr = self._run(
            [_completed(0, stdout="Ran 1 rule on 1 file: 0 findings.\n")]
        )
        assert code == 0
        assert len(runner.calls) == 1
        assert sleep.delays == []
        assert "0 findings" in stdout
        assert "[semgrep-scan]" not in stderr

    def test_findings_failure_is_not_retried(self):
        """Exit 1 with `--error` means findings — a real verdict, not a
        configuration problem, and the only case that must never be retried."""
        code, runner, sleep, stdout, stderr = self._run(
            [_completed(1, stdout=FINDINGS_STDOUT)]
        )
        assert code == 1
        assert len(runner.calls) == 1
        assert sleep.delays == []
        assert FINDINGS_STDOUT in stdout
        assert "[semgrep-scan]" not in stderr

    def test_transient_fetch_failure_is_retried_and_reported(self):
        code, runner, sleep, stdout, stderr = self._run(
            [
                _completed(7, stderr=FETCH_FAILURE_STDERR),
                _completed(0, stdout="Ran 1 rule on 1 file: 0 findings.\n"),
            ]
        )
        assert code == 0
        assert len(runner.calls) == 2
        assert sleep.delays == [semgrep_scan.RETRY_DELAYS_SECONDS[0]]
        # The failed attempt's own output is never swallowed.
        assert "Failed to download configuration" in stderr
        assert "retrying in 2s" in stderr
        assert "succeeded after retrying" in stderr

    def test_quiet_fetch_failure_is_retried_without_the_signature(self):
        """The reported consumer case: `--quiet` hides the `[ERROR]` line
        while the exit code stays 7, so the exit code must drive the retry."""
        code, runner, sleep, _, stderr = self._run(
            [_completed(7), _completed(0, stdout="Ran 1 rule on 1 file: 0 findings.\n")]
        )
        assert code == 0
        assert len(runner.calls) == 2
        assert "exit 7" in stderr
        # No cause line is invented when semgrep printed nothing.
        assert "cause:" not in stderr

    def test_transport_failure_shape_is_retried_via_the_signature(self):
        """The other fetch-failure shape (a connection failure) does not
        necessarily land on exit 7; its message is what identifies it."""
        transport_stderr = (
            "[ERROR] Failed to download config from https://semgrep.dev/c/auto: "
            "HTTP request failed: connection failed: timeout\n"
        )
        code, runner, sleep, _, stderr = self._run(
            [_completed(2, stderr=transport_stderr), _completed(0)]
        )
        assert code == 0
        assert len(runner.calls) == 2
        assert "cause: [ERROR] Failed to download config from" in stderr

    def test_retries_are_bounded_and_the_last_exit_code_is_preserved(self):
        code, runner, sleep, _, stderr = self._run(
            [_completed(7, stderr=FETCH_FAILURE_STDERR)] * semgrep_scan.MAX_ATTEMPTS
        )
        assert code == 7, "the final exit code must be semgrep's, unchanged"
        assert len(runner.calls) == semgrep_scan.MAX_ATTEMPTS
        assert sleep.delays == list(semgrep_scan.RETRY_DELAYS_SECONDS)
        # One retry line per retry — the last attempt reports the give-up
        # instead of scheduling another retry.
        last = semgrep_scan.MAX_ATTEMPTS
        for attempt in range(1, last):
            assert f"attempt {attempt}/{last}" in stderr
        assert f"attempt {last}/{last}" not in stderr, (
            "the final attempt must not schedule another retry"
        )
        assert "reporting exit 7 unchanged" in stderr
        assert "Failed to download configuration" in stderr

    def test_missing_semgrep_binary_fails_loudly(self):
        code, runner, sleep, _, stderr = self._run([FileNotFoundError("semgrep")])
        assert code == semgrep_scan.SEMGREP_MISSING_EXIT_CODE
        assert len(runner.calls) == 1
        assert sleep.delays == []
        assert "'semgrep' is not on PATH" in stderr

    def test_wrapper_imports_only_the_standard_library(self):
        """The workflows run this file straight from the toolkit checkout
        (`python3 ../toolkit/scripts/semgrep_scan.py`), so it must stay
        importable without the toolkit package on sys.path."""
        tree = ast.parse(SCRIPT_PATH.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        non_stdlib = imported - set(sys.stdlib_module_names) - {"__future__"}
        assert non_stdlib == set(), f"non-stdlib imports: {sorted(non_stdlib)}"

    def test_file_execution_propagates_the_scan_status(self):
        """Executed as a file (how security.yml runs it), the wrapper must
        exit with the scan's status. Driven in-process so the unit under
        test is the module's own entry point, not a subprocess."""
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", [str(SCRIPT_PATH), "--config=auto"]):
            with mock.patch(
                "subprocess.run", return_value=_completed(0, stdout="0 findings\n")
            ):
                with contextlib.redirect_stdout(stdout):
                    with self.assertRaises(SystemExit) as caught:
                        runpy.run_module("semgrep_scan", run_name="__main__")
        assert caught.exception.code == 0
        assert "0 findings" in stdout.getvalue()


class SemgrepScanEndToEndTests(unittest.TestCase):
    """Drive the real CLI with a fake `semgrep` on PATH: the same invocation
    shape the pre-commit hook (console script) and security.yml (file path)
    use, including the retry actually waiting and re-running."""

    def _write_fake_semgrep(self, directory: Path) -> None:
        binary = directory / "semgrep"
        binary.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                state="$0.count"
                attempts=0
                [ -f "$state" ] && attempts=$(cat "$state")
                attempts=$((attempts + 1))
                echo "$attempts" > "$state"
                if [ "$attempts" -lt 2 ]; then
                  echo "[ERROR] Failed to download configuration from https://semgrep.dev/c/auto HTTP 403." >&2
                  echo "[ERROR] invalid configuration file found (1 configs were invalid)" >&2
                  exit 7
                fi
                echo "Ran 1 rule on 1 file: 0 findings."
                exit 0
                """
            )
        )
        binary.chmod(0o755)

    def test_cli_retries_a_transient_fetch_failure_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._write_fake_semgrep(bin_dir)
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "src",
                    "--config=auto",
                    "--error",
                ],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        assert proc.returncode == 0, proc.stderr
        assert "retrying in 2s" in proc.stderr
        assert "succeeded after retrying" in proc.stderr
        assert "0 findings" in proc.stdout


if __name__ == "__main__":
    unittest.main()
