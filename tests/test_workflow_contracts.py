"""Structural contract tests for the toolkit's workflow collection.

These tests are the deterministic enforcement of the policy decisions
recorded in DECISIONS.md. A workflow edit that violates any of them fails
`make verify` before it can ever reach a consumer:

- D-0006 hybrid architecture: micro-workflows are callable only via
  workflow_call; the composite orchestrates JOBS with relative ./ refs.
- D-0001 gate ordering: the LLM review job runs only after every enabled
  deterministic gate is green (success OR skipped — disabled gates must
  not block the cost gate).
- D-0004 neutral defaults: no origin-repository fossils in the public API;
  coverage-floor is a REQUIRED policy input.
- D-0005 secrets contract: explicit forwarding only (no blanket secret
  propagation shortcuts)
  anywhere), optional judge-token with github.token fallback, fail-fast
  OpenRouter validation.
- D-0007 tagged execution: third-party actions SHA-pinned; coverage.json
  handed from test.yml to diff-coverage.yml as an artifact.
"""

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

MICRO_WORKFLOWS = [
    "lint.yml",
    "test.yml",
    "security.yml",
    "secret-scan.yml",
    "diff-coverage.yml",
    "llm-pr-review.yml",
]
TOOLKIT_REPO = "LuisArteaga/quality-gates-toolkit"
TOOLKIT_CHECKOUT_PATH = "toolkit"
CALLER_CHECKOUT_PATH = "repo"


def _load(name: str) -> dict[str, Any]:
    path = WORKFLOWS / name
    assert path.exists(), f"missing workflow: {path}"
    with path.open() as f:
        return yaml.safe_load(f)


def _triggers(wf: dict[str, Any]) -> dict[str, Any]:
    # PyYAML parses the bare YAML key `on:` as boolean True (YAML 1.1).
    raw: dict[Any, Any] = wf
    on = raw.get("on")
    if on is None:
        on = raw.get(True)
    assert isinstance(on, dict), "workflow must define a trigger map under `on:`"
    return on


def _call_inputs(wf: dict[str, Any]) -> dict[str, Any]:
    call = _triggers(wf).get("workflow_call")
    assert isinstance(call, dict), "workflow must expose a workflow_call trigger"
    inputs = call.get("inputs")
    assert isinstance(inputs, dict), "workflow_call must declare its input contract"
    return inputs


def _jobs(wf: dict[str, Any]) -> dict[str, Any]:
    jobs = wf.get("jobs")
    assert isinstance(jobs, dict) and jobs, "workflow must define jobs"
    return jobs


# ---------------------------------------------------------------------------
# D-0006: micro-workflow surface
# ---------------------------------------------------------------------------


def test_micro_workflows_are_pure_reusable():
    for name in MICRO_WORKFLOWS:
        triggers = _triggers(_load(name))
        assert set(triggers) == {"workflow_call"}, (
            f"{name} must be callable only (triggers: {sorted(triggers)})"
        )


def test_composite_calls_micro_workflows_via_relative_refs():
    jobs = _jobs(_load("pr-checks.yml"))
    for job_id, job in jobs.items():
        uses = job.get("uses", "")
        if uses:
            assert uses.startswith("./.github/workflows/"), (
                f"composite job '{job_id}' must use a relative internal ref, got {uses}"
            )


def test_composite_declares_coverage_floor_required_without_default():
    inputs = _call_inputs(_load("pr-checks.yml"))
    floor = inputs["coverage-floor"]
    assert floor.get("required") is True, "coverage-floor is a REQUIRED policy input"
    assert "default" not in floor, "coverage-floor must ship no default"


def test_composite_ships_neutral_public_defaults():
    inputs = _call_inputs(_load("pr-checks.yml"))
    assert inputs["enable-llm-review"]["default"] is False
    assert inputs["prefetch-tree-sitter"]["default"] is False
    assert inputs["lint-paths"]["default"] == "."
    assert inputs["cov-paths"]["default"] == "."
    assert inputs["extra-pip-packages"]["default"] == "none"
    assert inputs["toolkit-ref"]["default"] == "v1.0.0"
    assert inputs["config-path"]["default"] == "config/factory.json"


def test_every_toolkit_ref_input_defaults_to_the_release_tag():
    # D-0007 (amended): the default is the concrete release tag — GitHub
    # resolves no floating major alias, so a default naming a nonexistent
    # ref would break default-consuming callers.
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = _load(path.name)
        inputs = _triggers(wf).get("workflow_call") or {}
        inputs = inputs.get("inputs") or {}
        if "toolkit-ref" not in inputs:
            continue
        assert inputs["toolkit-ref"].get("default") == "v1.0.0", (
            f"{path.name}: toolkit-ref must default to the concrete release tag"
        )


def test_batch_budget_input_is_forwarded_to_the_judge_workflow():
    composite = _load("pr-checks.yml")
    assert "batch-budget-chars" in _call_inputs(composite), (
        "pr-checks.yml must expose the batch-budget-chars knob"
    )
    llm = _jobs(composite)["llmreview"]
    assert llm["with"]["batch-budget-chars"] == "${{ inputs.batch-budget-chars }}"
    judge = _load("llm-pr-review.yml")
    env = judge["jobs"]["llm-pr-review"]["env"]
    assert env["REVIEW_BATCH_BUDGET_CHARS"] == (
        "${{ inputs.batch-budget-chars || '200000' }}"
    )


# ---------------------------------------------------------------------------
# D-0001: gate ordering / LLM cost gate
# ---------------------------------------------------------------------------


def test_llm_review_requires_all_deterministic_gates():
    jobs = _jobs(_load("pr-checks.yml"))
    llm = jobs["llmreview"]
    assert set(llm["needs"]) == {
        "lint",
        "test",
        "security",
        "secretscan",
        "diffcoverage",
    }


def test_llm_review_cost_gate_tolerates_only_skipped_gates():
    """The if-expression must demand success from every gate while allowing
    explicitly disabled gates (skipped) — never always()-style fallbacks."""
    jobs = _jobs(_load("pr-checks.yml"))
    condition = jobs["llmreview"]["if"]
    for gate in ("lint", "test", "security", "secretscan", "diffcoverage"):
        assert f"needs.{gate}.result == 'success'" in condition, gate
        assert f"needs.{gate}.result == 'skipped'" in condition, gate
    assert "inputs.enable-llm-review" in condition
    assert "!cancelled()" in condition


def test_diff_coverage_runs_only_after_test_success_and_on_pr_events():
    jobs = _jobs(_load("pr-checks.yml"))
    diff = jobs["diffcoverage"]
    assert diff["needs"] == ["test"]
    condition = diff["if"]
    assert "inputs.enable-diff-gate" in condition
    assert "needs.test.result == 'success'" in condition
    assert "github.event_name == 'pull_request'" in condition


# ---------------------------------------------------------------------------
# D-0005: secrets contract
# ---------------------------------------------------------------------------


def test_no_secrets_inherit_anywhere():
    for name in [*MICRO_WORKFLOWS, "pr-checks.yml", "ci.yml"]:
        raw = (WORKFLOWS / name).read_text()
        assert "secrets: inherit" not in raw, f"{name} must forward secrets explicitly"


def test_llm_review_forwards_secrets_explicitly():
    jobs = _jobs(_load("pr-checks.yml"))
    secrets = jobs["llmreview"].get("secrets")
    assert secrets == {
        "openrouter-api-key": "${{ secrets.openrouter-api-key }}",
        "judge-token": "${{ secrets.judge-token }}",
    }


def test_judge_token_is_optional_with_github_token_fallback():
    wf = _load("llm-pr-review.yml")
    secrets = _triggers(wf)["workflow_call"]["secrets"]
    assert secrets["judge-token"]["required"] is False
    assert secrets["openrouter-api-key"]["required"] is True
    raw = (WORKFLOWS / "llm-pr-review.yml").read_text()
    assert "${{ secrets.judge-token || github.token }}" in raw


def test_llm_review_validates_openrouter_key_before_use():
    raw = (WORKFLOWS / "llm-pr-review.yml").read_text()
    assert "openrouter-api-key secret not configured" in raw


# ---------------------------------------------------------------------------
# D-0007: tagged toolkit execution + artifact handoff
# ---------------------------------------------------------------------------


def test_toolkit_checkouts_are_public_repo_siblings_without_persisted_credentials():
    for name in ("secret-scan.yml", "diff-coverage.yml", "llm-pr-review.yml"):
        # Job ids vary per workflow; scan every job's steps for the toolkit
        # checkout instead of assuming the id from the filename.
        all_steps = [
            step for job in _jobs(_load(name)).values() for step in job.get("steps", [])
        ]
        toolkit_steps = [
            s
            for s in all_steps
            if isinstance(s.get("with"), dict)
            and s["with"].get("repository") == TOOLKIT_REPO
        ]
        assert toolkit_steps, f"{name} must check out the toolkit implementation"
        for step in toolkit_steps:
            with_ = step["with"]
            assert with_["path"] == TOOLKIT_CHECKOUT_PATH
            assert with_["persist-credentials"] is False
            assert "${{ inputs.toolkit-ref }}" == with_["ref"]


def test_caller_checkouts_do_not_persist_credentials():
    for name in (*MICRO_WORKFLOWS, "lint.yml", "test.yml", "security.yml"):
        raw = (WORKFLOWS / name).read_text()
        assert "persist-credentials: false" in raw, name


def test_coverage_artifact_handoff_from_test_to_diff_coverage():
    test_steps = _jobs(_load("test.yml"))["test"]["steps"]
    uploads = [s for s in test_steps if "upload-artifact" in str(s.get("uses", ""))]
    assert uploads, "test.yml must upload coverage.json"
    artifact_name = uploads[0]["with"]["name"]
    assert artifact_name == "coverage-json"

    diff_inputs = _call_inputs(_load("diff-coverage.yml"))
    assert diff_inputs["coverage-artifact"]["default"] == artifact_name


def test_third_party_actions_are_sha_pinned():
    import re

    for name in (*MICRO_WORKFLOWS, "pr-checks.yml", "ci.yml"):
        raw = (WORKFLOWS / name).read_text()
        for match in re.finditer(r"uses:\s*(\S+)", raw):
            ref = match.group(1)
            if ref.startswith("./"):
                continue
            assert "@" in ref, f"{name}: {ref} lacks a ref"
            ref_part = ref.split("@", 1)[1]
            assert re.fullmatch(r"[0-9a-f]{40}", ref_part), (
                f"{name}: {ref} must pin a full commit SHA, got {ref_part}"
            )


# ---------------------------------------------------------------------------
# Self-dogfooding contract
# ---------------------------------------------------------------------------


def test_ci_dogfoods_same_commit_not_published_tag():
    jobs = _jobs(_load("ci.yml"))
    quality = jobs["quality"]
    assert quality["uses"] == "./.github/workflows/pr-checks.yml"
    with_ = quality["with"]
    assert "${{ github.event.pull_request.head.sha }}" == with_["toolkit-ref"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in str(
        with_["enable-llm-review"]
    )
    assert "coverage-floor" in with_


def test_micro_workflows_use_least_privilege_permissions():
    for name in MICRO_WORKFLOWS:
        if name == "llm-pr-review.yml":
            # The only workflow allowed to write (posts PR reviews).
            continue
        permissions = _load(name).get("permissions")
        assert permissions == {"contents": "read"}, name
    # Only the review workflow may write.
    llm_permissions = _load("llm-pr-review.yml")["permissions"]
    assert llm_permissions == {"contents": "read", "pull-requests": "write"}


# ---------------------------------------------------------------------------
# Public pre-commit hook interface (README "Using as a pre-commit hook")
# ---------------------------------------------------------------------------


def test_pre_commit_hooks_file_declares_secret_scan():
    hooks_path = WORKFLOWS.parent.parent / ".pre-commit-hooks.yaml"
    assert hooks_path.exists(), "remote hook consumers need .pre-commit-hooks.yaml"
    with hooks_path.open() as f:
        hooks = yaml.safe_load(f)
    assert isinstance(hooks, list) and hooks, "must declare at least one hook"
    hook = next((h for h in hooks if h.get("id") == "secret-scan"), None)
    assert hook is not None, "hook id 'secret-scan' must exist (README quick start)"
    assert hook.get("entry") == "secret-scan"
    assert hook.get("language") == "python"
    assert hook.get("pass_filenames") is False
    assert hook.get("always_run") is True


def test_pyproject_is_installable_and_exposes_secret_scan_script():
    import tomllib

    pyproject = WORKFLOWS.parent.parent / "pyproject.toml"
    assert pyproject.exists()
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    # `language: python` remote hooks are installed via pip — the package
    # must be buildable.
    assert data["build-system"]["build-backend"] == "setuptools.build_meta"
    # The hook entry point must resolve to a real console script.
    assert data["project"]["scripts"]["secret-scan"] == "scripts.secret_scan:main"
    assert "scripts" in data["tool"]["setuptools"]["packages"]
