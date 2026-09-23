#!/usr/bin/env python3
"""Semgrep scan wrapper with a bounded ruleset-fetch retry (D-0025).

``semgrep-scan <args...>`` is ``semgrep scan <args...>`` plus one policy:
when a run fails because its *configuration* could not be loaded, it is
retried a bounded number of times with a short backoff before the failure
is reported. Nothing else about the scan changes — the last attempt's
output and exit status are passed through untouched.

Why the configuration bucket rather than only the download message: the
documented contract is ``--config=auto``, which fetches the ruleset from
semgrep.dev at run time. A transient anonymous rate-limit (HTTP 403) makes
semgrep exit 7 — the same code it uses for a genuinely invalid ruleset —
and ``--quiet`` (which the toolkit's args guidance leads consumers to)
suppresses the two ``[ERROR]`` lines that would name the cause, so the
exit code is the only signal left. Retrying that bucket costs a few
seconds on a real configuration error and never hides the failure, so the
diagnosability contract (exit code + output) is preserved either way.

Output is captured per attempt and replayed verbatim (stdout to stdout,
stderr to stderr): the retry decision has to read semgrep's own message,
and the replayed bytes are the bytes semgrep printed.

Deliberately stdlib-only and self-contained — the toolkit's workflows run
this file directly from the toolkit checkout
(``python3 <checkout>/scripts/semgrep_scan.py ...``), so it must not
import the toolkit package.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

SEMGREP_COMMAND = ("semgrep", "scan")
# Semgrep's "invalid configuration file found" / "missing configuration"
# bucket — the exit code both a broken ruleset and an unfetchable one land
# on (verified against semgrep 1.177.0: a registry 404/403 yields rc 7).
CONFIG_ERROR_EXIT_CODE = 7
# Present in both fetch-failure shapes: "Failed to download configuration
# from <url> HTTP 403." and the transport-failure form "Failed to download
# config from <url>: HTTP request failed: ...". Only names the cause in the
# log — the retry decision does not depend on it (see module docstring).
FETCH_FAILURE_SIGNATURE = "Failed to download config"
MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (2.0, 5.0)
SEMGREP_MISSING_EXIT_CODE = 2


def _fetch_failure_cause(stderr: str) -> str | None:
    """Return semgrep's own fetch-failure line, when it is visible."""
    for line in stderr.splitlines():
        if FETCH_FAILURE_SIGNATURE in line:
            return line.strip()
    return None


def _is_config_load_failure(returncode: int, stderr: str) -> bool:
    """True when the run failed on its configuration, not on findings."""
    return (
        returncode == CONFIG_ERROR_EXIT_CODE or _fetch_failure_cause(stderr) is not None
    )


def _replay(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.stdout:
        sys.stdout.write(completed.stdout)
        sys.stdout.flush()
    if completed.stderr:
        sys.stderr.write(completed.stderr)
        sys.stderr.flush()


def _retry_delay(attempt: int) -> float:
    return RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]


def main(
    argv: Sequence[str] | None = None,
    runner: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """Run ``semgrep scan`` with the bounded configuration-load retry."""
    args = list(sys.argv[1:] if argv is None else argv)
    run = subprocess.run if runner is None else runner
    wait = time.sleep if sleep is None else sleep

    attempt = 1
    while True:
        try:
            completed = run(
                [*SEMGREP_COMMAND, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            sys.stderr.write(
                f"[semgrep-scan] error: '{SEMGREP_COMMAND[0]}' is not on PATH — "
                "install it (the pre-commit hook pins it via "
                "additional_dependencies; CI installs it before the scan)\n"
            )
            return SEMGREP_MISSING_EXIT_CODE

        _replay(completed)

        if not _is_config_load_failure(completed.returncode, completed.stderr):
            if attempt > 1 and completed.returncode == 0:
                sys.stderr.write(
                    f"[semgrep-scan] attempt {attempt}/{MAX_ATTEMPTS} succeeded "
                    "after retrying the semgrep configuration load\n"
                )
            return completed.returncode

        if attempt == MAX_ATTEMPTS:
            sys.stderr.write(
                f"[semgrep-scan] semgrep could not load its configuration after "
                f"{MAX_ATTEMPTS} attempts — reporting exit "
                f"{completed.returncode} unchanged\n"
            )
            return completed.returncode

        delay = _retry_delay(attempt)
        sys.stderr.write(
            f"[semgrep-scan] attempt {attempt}/{MAX_ATTEMPTS}: semgrep could not "
            f"load its configuration (exit {completed.returncode}); retrying in "
            f"{delay:g}s\n"
        )
        cause = _fetch_failure_cause(completed.stderr)
        if cause:
            sys.stderr.write(f"[semgrep-scan] cause: {cause}\n")
        wait(delay)
        attempt += 1


if __name__ == "__main__":
    sys.exit(main())
