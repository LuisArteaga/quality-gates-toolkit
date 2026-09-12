"""Structural contract tests for the toolkit's workflow collection.

These tests are the deterministic enforcement of the policy decisions
recorded in DECISIONS.md. A workflow edit that violates any of them fails
`uv run pytest -q` before it can ever reach a consumer:

- D-0006 hybrid architecture: micro-workflows are callable only via
  workflow_call; the composite orchestrates JOBS with relative ./ refs.
- D-0019 self-test harness: ci.yml calls the micro-workflows directly
  (never the composite) and the composite canary runs on schedule +
  workflow_dispatch only — never on pull_request.
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
- D-0020 per-language composites: python-checks.yml / js-checks.yml restate
  the ordering policy internally, default their own language's gates ON,
  keep the judge optional (exactly-one rule across composites), and the
  polyglot composite neither grows nor references them.
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
LANGUAGE_COMPOSITES = ["python-checks.yml", "js-checks.yml"]
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
    assert inputs["toolkit-ref"]["default"] == "v1.6.0"
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
        assert inputs["toolkit-ref"].get("default") == "v1.6.0", (
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
    for name in [
        *MICRO_WORKFLOWS,
        *LANGUAGE_COMPOSITES,
        "pr-checks.yml",
        "ci.yml",
        "composite-canary.yml",
    ]:
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

    for name in (
        *MICRO_WORKFLOWS,
        *LANGUAGE_COMPOSITES,
        "pr-checks.yml",
        "ci.yml",
        "composite-canary.yml",
    ):
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
# D-0020: per-language composites
# ---------------------------------------------------------------------------


def test_language_composites_are_callable_only():
    for name in LANGUAGE_COMPOSITES:
        triggers = _triggers(_load(name))
        assert set(triggers) == {"workflow_call"}, (
            f"{name} must be callable only (triggers: {sorted(triggers)})"
        )


def test_language_composites_call_micro_workflows_via_relative_refs():
    for name in LANGUAGE_COMPOSITES:
        jobs = _jobs(_load(name))
        for job_id, job in jobs.items():
            uses = job.get("uses", "")
            if uses:
                assert uses.startswith("./.github/workflows/"), (
                    f"{name} job '{job_id}' must use a relative internal ref, got {uses}"
                )


def test_python_checks_ships_the_python_gate_group():
    jobs = _jobs(_load("python-checks.yml"))
    assert set(jobs) == {
        "lint",
        "test",
        "security",
        "secretscan",
        "diffcoverage",
        "llmreview",
    }
    inputs = _call_inputs(_load("python-checks.yml"))
    for name in (
        "enable-lint",
        "enable-test",
        "enable-security",
        "enable-semgrep",
        "enable-pip-audit",
        "enable-secret-scan",
        "enable-diff-gate",
    ):
        assert inputs[name].get("default") is True, name
    floor = inputs["coverage-floor"]
    assert floor.get("required") is True, "coverage-floor is a REQUIRED policy input"
    assert "default" not in floor, "coverage-floor must ship no default"
    # A language composite declares only its own language's knobs.
    assert "node-version" not in inputs, "python-checks.yml has no JS jobs"
    assert jobs["diffcoverage"]["needs"] == ["test"]
    with_ = jobs["llmreview"]["with"]
    assert with_["prefetch-tree-sitter"] == "${{ inputs.prefetch-tree-sitter }}"


def test_js_checks_ships_the_js_gate_group():
    """D-0020: on the JS language composite the JS toggles default ON — the
    caller chose the JS entry point, inverting the polyglot's JS-OFF
    default (D-0013). No coverage contract: the JS harness owns the
    environment and has no coverage.json artifact handoff."""
    jobs = _jobs(_load("js-checks.yml"))
    assert set(jobs) == {
        "js-lint",
        "js-test",
        "js-typecheck",
        "secretscan",
        "llmreview",
    }
    inputs = _call_inputs(_load("js-checks.yml"))
    for name in ("enable-js-lint", "enable-js-test", "enable-js-typecheck"):
        toggle = inputs[name]
        assert toggle.get("type") == "boolean", name
        assert toggle.get("default") is True, (
            f"{name} defaults ON on the JS language composite (skip-free by construction)"
        )
    assert "coverage-floor" not in inputs, (
        "js-checks.yml has no coverage artifact contract"
    )
    assert "python-version" not in inputs, "js-checks.yml has no Python gates"
    assert "enable-diff-gate" not in inputs


def test_language_composites_default_language_agnostic_gates():
    for name in LANGUAGE_COMPOSITES:
        inputs = _call_inputs(_load(name))
        assert inputs["enable-llm-review"]["default"] is False, (
            f"{name}: the judges must stay opt-in"
        )
        assert inputs["enable-secret-scan"]["default"] is True, (
            f"{name}: secret scanning is language-agnostic and on by default"
        )


def test_language_composites_llm_review_gated_by_deterministic_gates():
    """D-0001 restated internally per language composite (the accepted
    D-0020 trade-off): the judges run only after every gate of THAT
    composite is success-or-skipped, plus the D-0011 pull_request guard."""
    expected = {
        "python-checks.yml": ("lint", "test", "security", "secretscan", "diffcoverage"),
        "js-checks.yml": ("js-lint", "js-test", "js-typecheck", "secretscan"),
    }
    for name, gates in expected.items():
        llm = _jobs(_load(name))["llmreview"]
        assert set(llm["needs"]) == set(gates), name
        condition = str(llm["if"])
        for gate in gates:
            assert f"needs.{gate}.result == 'success'" in condition, (name, gate)
            assert f"needs.{gate}.result == 'skipped'" in condition, (name, gate)
        assert "inputs.enable-llm-review" in condition, name
        assert "!cancelled()" in condition, name
        assert "github.event_name == 'pull_request'" in condition, name


def test_language_composites_forward_the_judge_contract():
    """D-0002/D-0005/D-0007 plumbing on the embedded judge job: explicit
    secret forwarding, the same-tag toolkit checkout, and the judge knobs."""
    for name in LANGUAGE_COMPOSITES:
        llm = _jobs(_load(name))["llmreview"]
        assert llm["secrets"] == {
            "openrouter-api-key": "${{ secrets.openrouter-api-key }}",
            "judge-token": "${{ secrets.judge-token }}",
        }, name
        with_ = llm["with"]
        assert with_["toolkit-ref"] == "${{ inputs.toolkit-ref }}", name
        assert with_["config-path"] == "${{ inputs.config-path }}", name
        assert with_["diff-exclude"] == "${{ inputs.diff-exclude }}", name
        assert with_["batch-budget-chars"] == "${{ inputs.batch-budget-chars }}", name


def test_language_composites_forward_toolkit_ref_to_gates_that_need_it():
    for name in LANGUAGE_COMPOSITES:
        jobs = _jobs(_load(name))
        assert (
            jobs["secretscan"]["with"]["toolkit-ref"] == "${{ inputs.toolkit-ref }}"
        ), name
        diff = jobs.get("diffcoverage")
        if diff is not None:
            assert diff["with"]["toolkit-ref"] == "${{ inputs.toolkit-ref }}", name


def test_polyglot_composite_does_not_grow_language_toggles():
    """D-0020: new languages add composite FILES — pr-checks.yml keeps its
    exact job set and gains no references to the language composites."""
    jobs = _jobs(_load("pr-checks.yml"))
    assert set(jobs) == {
        "lint",
        "test",
        "security",
        "js-lint",
        "js-test",
        "js-typecheck",
        "secretscan",
        "diffcoverage",
        "llmreview",
    }
    raw = (WORKFLOWS / "pr-checks.yml").read_text()
    assert "python-checks" not in raw, (
        "pr-checks.yml must not reference language composites"
    )
    assert "js-checks" not in raw, (
        "pr-checks.yml must not reference language composites"
    )


# ---------------------------------------------------------------------------
# Self-test harness + composite canary (D-0019)
# ---------------------------------------------------------------------------


def test_ci_self_test_runs_micro_workflows_not_the_composite():
    """ci.yml is a self-test harness against the PR commit — it must not
    call the composite: this repo is Python-only and the composite's JS
    gate group defaults OFF (D-0013), so the called composite would render
    three permanent Skipped checks on every toolkit PR."""
    raw = (WORKFLOWS / "ci.yml").read_text()
    assert "pr-checks.yml" not in raw, "ci.yml must not reference the composite"
    jobs = _jobs(_load("ci.yml"))
    expected = {
        "lint": "lint.yml",
        "test": "test.yml",
        "security": "security.yml",
        "secretscan": "secret-scan.yml",
        "llmreview": "llm-pr-review.yml",
    }
    assert set(jobs) == set(expected), f"unexpected ci.yml jobs: {sorted(jobs)}"
    for job_id, workflow in expected.items():
        assert jobs[job_id]["uses"] == f"./.github/workflows/{workflow}", job_id
    for job_id in ("lint", "test", "security", "secretscan"):
        assert "if" not in jobs[job_id], (
            f"{job_id} must run unconditionally — a disabled job renders as Skipped"
        )


def test_ci_llm_review_gated_by_deterministic_gates_and_fork_guard():
    """D-0001 cost gate + fork guard on the direct caller: the judges start
    only when every deterministic gate is green (default needs success()
    semantics — ci.yml has no gate toggles, so the composite's
    success-or-skipped clauses have nothing to tolerate) and only for
    same-repo PRs (fork PRs cannot access repository secrets)."""
    llm = _jobs(_load("ci.yml"))["llmreview"]
    assert llm["needs"] == ["lint", "test", "security", "secretscan"]
    condition = str(llm["if"])
    guard = "github.event.pull_request.head.repo.full_name == github.repository"
    assert guard in condition, "llmreview must be fork-guarded"


def test_ci_dogfoods_the_head_sha_in_implementation_checkouts():
    """D-0007 on the direct caller: secret-scan and the judges must check
    out THIS PR's implementation, not a published tag."""
    for job_id in ("secretscan", "llmreview"):
        with_ = _jobs(_load("ci.yml"))[job_id]["with"]
        assert "${{ github.event.pull_request.head.sha }}" == with_["toolkit-ref"], (
            f"ci.yml {job_id} must dogfood the PR's implementation"
        )


def test_self_check_lints_and_measures_the_judge_package():
    """D-0017: ruff/mypy/coverage run against the importable package too —
    the judge implementation left scripts/, so a scripts-only self-check
    would leave the moved code unlinted and unmeasured."""
    jobs = _jobs(_load("ci.yml"))
    for job_id, key in (("lint", "lint-paths"), ("test", "cov-paths")):
        paths = str(jobs[job_id]["with"][key]).split()
        assert "quality_gates_toolkit" in paths, f"ci.yml {key} must cover the package"
        assert "scripts" in paths, f"ci.yml {key} must keep covering scripts/"


def test_composite_canary_triggers_on_schedule_and_dispatch_only():
    """The canary must never add a check to a pull request — PR-event
    composite runs are the consumers' business; the canary exists so the
    nightly and pre-release path exercises the composite without touching
    PR checks lists (D-0019)."""
    triggers = _triggers(_load("composite-canary.yml"))
    assert set(triggers) == {"schedule", "workflow_dispatch"}, (
        f"canary triggers must be schedule+dispatch, got {sorted(triggers)}"
    )


def test_composite_canary_runs_the_composite_with_judges_disabled():
    jobs = _jobs(_load("composite-canary.yml"))
    assert set(jobs) == {"canary"}, "the canary runs exactly one composite call"
    job = jobs["canary"]
    with_ = job["with"]
    assert job["uses"] == "./.github/workflows/pr-checks.yml"
    assert with_["enable-llm-review"] is False, (
        "an unattended canary must never spend judge tokens"
    )
    assert with_["toolkit-ref"] == "${{ github.sha }}", (
        "the canary must exercise the default-branch tip, not a published tag"
    )
    assert "coverage-floor" in with_, "coverage-floor is a REQUIRED composite input"
    permissions = job["permissions"]
    assert permissions == {"contents": "read", "pull-requests": "write"}, (
        "canary must grant pull-requests: write (static escalation validation"
        " startup-fails the call without it, even for the skipped judge job)"
    )


def test_ci_carries_the_coverage_floor_policy():
    """D-0004: coverage-floor is a REQUIRED policy input — the direct
    caller keeps stating it explicitly (migrated from the composite call)."""
    with_ = _jobs(_load("ci.yml"))["test"]["with"]
    assert "coverage-floor" in with_, "ci.yml test job must state the coverage floor"


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
    # Language composites follow the same shape as the polyglot composite:
    # contents: read at the top level, one writing job (the judges).
    for name in LANGUAGE_COMPOSITES:
        assert _load(name).get("permissions") == {"contents": "read"}, name
        llm = _jobs(_load(name))["llmreview"]
        assert llm["permissions"] == {"contents": "read", "pull-requests": "write"}, (
            name
        )


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
