"""Structural contract tests for the toolkit's workflow collection.

These tests are the deterministic enforcement of the policy decisions
recorded in DECISIONS.md. A workflow edit that violates any of them fails
`make verify` before it can ever reach a consumer:

- D-0006 hybrid architecture: micro-workflows are callable only via
  workflow_call; the composite orchestrates JOBS with relative ./ refs.
- D-0001 gate ordering: the LLM review job runs only after every enabled
  deterministic gate is green (success OR skipped — disabled gates must
  not block the cost gate) and only on pull_request events (D-0011).
- D-0004 neutral defaults: no origin-repository fossils in the public API;
  coverage-floor is a REQUIRED policy input.
- D-0005 secrets contract: explicit forwarding only (no blanket secret
  propagation shortcuts)
  anywhere), optional judge-token with github.token fallback, fail-fast
  OpenRouter validation.
- D-0007 tagged execution: third-party actions SHA-pinned; coverage.json
  handed from test.yml to diff-coverage.yml as an artifact.
- D-0012 JS gates: the harness owns the environment, the project owns the
  tools — fixed npm script contracts, node-version as the only input.
- D-0013 language contract: the composite's JS gate group (enable-js-*)
  defaults OFF so existing Python callers are unaffected, and the cost gate
  spans both language groups (every enabled deterministic gate precedes the
  LLM review).
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
    "js-test.yml",
    "js-typecheck.yml",
    "js-lint.yml",
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
    assert inputs["toolkit-ref"]["default"] == "v1.4.0"
    assert inputs["config-path"]["default"] == "config/factory.json"
    assert inputs["node-version"]["default"] == "22"


def test_composite_js_language_toggles_default_off():
    """D-0013: the JS gate group must default OFF so a Python caller
    upgrading the toolkit is unaffected (npm ci fails loudly without a
    package-lock.json), while the Python toggles keep their ON defaults —
    the backward-compatibility anchor of the language contract."""
    inputs = _call_inputs(_load("pr-checks.yml"))
    for name in ("enable-js-lint", "enable-js-test", "enable-js-typecheck"):
        toggle = inputs[name]
        assert toggle.get("type") == "boolean", name
        assert toggle.get("default") is False, (
            f"{name} must default to false (existing callers must be unaffected)"
        )
    for name in ("enable-lint", "enable-test", "enable-security", "enable-secret-scan"):
        assert inputs[name].get("default") is True, (
            f"{name} must keep its ON default (backward compatibility)"
        )


def test_composite_js_jobs_call_the_js_micro_workflows():
    jobs = _jobs(_load("pr-checks.yml"))
    expected = {
        "js-lint": ("./.github/workflows/js-lint.yml", "enable-js-lint"),
        "js-test": ("./.github/workflows/js-test.yml", "enable-js-test"),
        "js-typecheck": ("./.github/workflows/js-typecheck.yml", "enable-js-typecheck"),
    }
    for job_id, (ref, toggle) in expected.items():
        job = jobs[job_id]
        assert job["uses"] == ref, job_id
        assert job["if"] == f"inputs.{toggle}", job_id
        assert job["permissions"] == {"contents": "read"}, job_id
        assert job["with"]["node-version"] == "${{ inputs.node-version }}", job_id


def test_lint_job_installs_caller_dependencies_for_mypy():
    """lint.yml must give mypy the caller's dependency surface: under strict
    settings with no global ignore_missing_imports, a bare environment fails
    on every third-party import (first observed on a consumer on v1.0.0,
    whose lint previously ran inside a pip install -e .[dev] monolith), and a
    runtime-only install fails on tests/ imports like pytest/httpx (observed
    on the same consumer's canary at v1.0.1)."""
    raw = (WORKFLOWS / "lint.yml").read_text()
    assert "Install caller project" in raw
    # The dev-extra install and its fallback must ship together: removing the
    # plain editable fallback would break callers without a dev extra.
    assert 'pip install -e ".[dev]" || pip install -e .' in raw
    assert raw.index("Install caller project") < raw.index("Mypy typecheck")
    inputs = _call_inputs(_load("lint.yml"))
    assert inputs["extra-pip-packages"]["default"] == "none"


def test_test_job_install_is_strict_no_lint_style_fallback():
    """D-0014: test.yml installs the caller strictly via the dev extra —
    no lint-style runtime-only fallback. pytest comes from the caller's
    [dev] extra, so a fallback could not rescue the job; it would only
    move the failure site. Pins the documented lint/test asymmetry
    against accidental alignment."""
    raw = (WORKFLOWS / "test.yml").read_text()
    assert "Install dependencies" in raw
    assert "pip install -e .[dev]" in raw
    assert "|| pip install -e ." not in raw


def test_composite_forwards_extra_pip_packages_to_lint():
    jobs = _jobs(_load("pr-checks.yml"))
    assert jobs["lint"]["with"]["extra-pip-packages"] == (
        "${{ inputs.extra-pip-packages }}"
    )


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
        assert inputs["toolkit-ref"].get("default") == "v1.4.0", (
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
        "js-lint",
        "js-test",
        "js-typecheck",
        "secretscan",
        "diffcoverage",
    }


def test_llm_review_cost_gate_tolerates_only_skipped_gates():
    """The if-expression must demand success from every gate while allowing
    explicitly disabled gates (skipped) — never always()-style fallbacks.
    D-0013: the policy spans both language groups."""
    jobs = _jobs(_load("pr-checks.yml"))
    condition = jobs["llmreview"]["if"]
    gates = (
        "lint",
        "test",
        "security",
        "js-lint",
        "js-test",
        "js-typecheck",
        "secretscan",
        "diffcoverage",
    )
    for gate in gates:
        assert f"needs.{gate}.result == 'success'" in condition, gate
        assert f"needs.{gate}.result == 'skipped'" in condition, gate
    assert "inputs.enable-llm-review" in condition
    assert "!cancelled()" in condition


def test_llm_review_runs_only_on_pull_request_events():
    """Judges need a PR to review — the judge workflow fail-fasts on an
    empty PR number, so a caller triggered on other events (e.g. push)
    with enable-llm-review: true would otherwise turn permanently red
    (observed as a trap on a consumer repository)."""
    jobs = _jobs(_load("pr-checks.yml"))
    condition = jobs["llmreview"]["if"]
    assert "github.event_name == 'pull_request'" in condition


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
# D-0012: JavaScript / TypeScript gates
# ---------------------------------------------------------------------------


JS_GATES = {
    "js-test.yml": "npm test",
    "js-typecheck.yml": "npm run typecheck",
    "js-lint.yml": "npm run lint",
}


def test_js_gates_ship_the_fixed_script_contract():
    """The harness owns the environment, the project owns the tools
    (D-0012): exactly one input (node-version) and one fixed npm script per
    gate — no command inputs that would drift the toolkit into tool
    ownership."""
    for name, script in JS_GATES.items():
        inputs = _call_inputs(_load(name))
        assert set(inputs) == {"node-version"}, (
            f"{name} must expose only node-version, got {sorted(inputs)}"
        )
        assert inputs["node-version"].get("default") == "22", (
            f"{name}: node-version must default to '22'"
        )
        steps = next(iter(_jobs(_load(name)).values()))["steps"]
        runs = [step.get("run", "") for step in steps]
        install = [run for run in runs if run.strip().startswith("npm ci")]
        assert install, f"{name} must install dependencies with npm ci"
        assert runs[-1].strip() == script, (
            f"{name} must end with the fixed script '{script}'"
        )


def test_js_gates_declare_no_toolkit_implementation_checkout():
    """The JS gates run no Python implementation checkout — the harness only
    needs Node and the caller's package.json (same as lint.yml)."""
    for name in JS_GATES:
        raw = (WORKFLOWS / name).read_text()
        assert "toolkit-ref" not in raw, f"{name} must not take a toolkit-ref input"
        assert TOOLKIT_REPO not in raw, f"{name} must not check out the toolkit"


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


def test_self_check_lints_and_measures_the_judge_package():
    """D-0017: ruff/mypy/coverage run against the importable package too —
    the judge implementation left scripts/, so a scripts-only self-check
    would leave the moved code unlinted and unmeasured."""
    with_ = _jobs(_load("ci.yml"))["quality"]["with"]
    for key in ("lint-paths", "cov-paths"):
        paths = str(with_[key]).split()
        assert "quality_gates_toolkit" in paths, f"ci.yml {key} must cover the package"
        assert "scripts" in paths, f"ci.yml {key} must keep covering scripts/"


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
# Public pre-commit hook interface (README "Pre-commit hook")
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
    assert hook.get("language_version") == "python3.12"
    assert hook.get("pass_filenames") is False
    assert hook.get("always_run") is True


def test_pre_commit_hooks_declare_js_gates():
    """D-0012: the js-* hooks run the same fixed scripts as the workflows —
    system language (consumer's npm + node_modules), full-project scope."""
    hooks_path = WORKFLOWS.parent.parent / ".pre-commit-hooks.yaml"
    with hooks_path.open() as f:
        hooks = yaml.safe_load(f)
    expected = {
        "js-typecheck": "npm run typecheck",
        "js-test": "npm test",
        "js-lint": "npm run lint",
    }
    for hook_id, entry in expected.items():
        hook = next((h for h in hooks if h.get("id") == hook_id), None)
        assert hook is not None, f"hook id '{hook_id}' must exist (D-0012)"
        assert hook.get("entry") == entry
        assert hook.get("language") == "system"
        assert hook.get("pass_filenames") is False
        assert hook.get("always_run") is True


def test_pre_commit_hooks_declare_python_tool_hooks():
    """D-0016: the Python tool hooks split by version ownership — mypy runs
    from the consumer environment (system, advisory), semgrep/pip-audit run
    in pre-commit's isolated env at toolkit-pinned versions."""
    hooks_path = WORKFLOWS.parent.parent / ".pre-commit-hooks.yaml"
    with hooks_path.open() as f:
        hooks = yaml.safe_load(f)
    mypy_hook = next((h for h in hooks if h.get("id") == "mypy"), None)
    assert mypy_hook is not None, "hook id 'mypy' must exist (D-0016)"
    assert mypy_hook.get("entry") == "mypy"
    assert mypy_hook.get("language") == "system"
    assert mypy_hook.get("pass_filenames") is False
    assert mypy_hook.get("always_run") is True
    pinned = {
        "semgrep": ("semgrep scan", ["semgrep==1.177.0"]),
        "pip-audit": ("pip-audit", ["pip-audit==2.10.1"]),
    }
    for hook_id, (entry, deps) in pinned.items():
        hook = next((h for h in hooks if h.get("id") == hook_id), None)
        assert hook is not None, f"hook id '{hook_id}' must exist (D-0016)"
        assert hook.get("entry") == entry
        assert hook.get("language") == "python"
        assert hook.get("language_version") == "python3.12"
        assert hook.get("additional_dependencies") == deps, (
            f"{hook_id} must pin {deps[0]} exactly (bump via toolkit release)"
        )
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
    # D-0017: the importable judge package ships in the same distribution.
    assert "quality_gates_toolkit" in data["tool"]["setuptools"]["packages"]
    # The version field tracks the release train (annotated tags vX.Y.Z):
    # bump it together with the toolkit-ref pin sites in the release PR.
    # Mirrors the hardcoded-tag discipline of the toolkit-ref contract test.
    assert data["project"]["version"] == "1.6.0"
