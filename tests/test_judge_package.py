"""D-0017: the judge API is importable as the quality_gates_toolkit package.

Cross-repo consumers (agentic-planner-core's eval harness) pip-install this
distribution and import the judge engine without the top-level ``scripts``
package-name collision. Three contracts are pinned here:

1. The importable surface exposes the judge API (the four SYSTEM_PROMPT_*
   constants, evaluate_response, load_architecture_context) from
   ``quality_gates_toolkit.review``.
2. The ``scripts`` shims are sys.modules ALIASES of the implementation
   modules — identity, not re-export copies — so flat imports, qualified
   imports, and ``mock.patch("review.<name>")`` targets all keep working
   against the moved code. Identity is also what makes duplicated prompt
   literals impossible through the shim surface: a re-export copy of a
   prompt constant would fail the ``is`` checks below.
3. The package is the self-sufficient home of the judge API: it imports
   cleanly (and uses package-internal support modules) even when a
   consumer's local ``scripts`` package shadows the toolkit's.
"""

import subprocess
import sys
from pathlib import Path

import pytest

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent

# The calibratable judge surface: everything the planner's eval harness
# imports today plus the shared neutrality instructions it composes with.
JUDGE_SURFACE = (
    "SYSTEM_PROMPT_ARCH",
    "SYSTEM_PROMPT_SECURITY",
    "SYSTEM_PROMPT_SYNTAX_LINT",
    "SYSTEM_PROMPT_TEST_COVERAGE",
    "evaluate_response",
    "load_architecture_context",
)

# Modules moved to the package (D-0017); each keeps a scripts/ alias shim.
MOVED_MODULES = ("review", "telemetry", "judge_config", "redaction", "enrichment")

# Private helpers the existing suite reaches through the scripts/ surface;
# they must survive as identity-aliased attributes, not copies.
PRIVATE_PATCH_TARGETS = {
    "review": (
        "_call_with_api_retry",
        "_enrich_chunk",
        "_get_batch_budget",
        "_is_empty_content",
        "_run_layered_retry",
    ),
    "telemetry": ("_build_langfuse_auth_header", "_is_langfuse_configured"),
}


def test_judge_api_surface_is_importable_from_the_package():
    from quality_gates_toolkit import review as impl

    for name in JUDGE_SURFACE:
        assert hasattr(impl, name), f"quality_gates_toolkit.review must export {name}"
    for prompt in JUDGE_SURFACE[:4]:
        value = getattr(impl, prompt)
        assert isinstance(value, str)
        assert len(value) > 0
    assert callable(impl.evaluate_response)
    assert callable(impl.load_architecture_context)


@pytest.mark.parametrize("module_name", MOVED_MODULES)
def test_both_import_surfaces_resolve_to_the_same_module(module_name):
    """Flat (`import review`), qualified (`import scripts.review`) and
    package (`import quality_gates_toolkit.review`) imports must all bind the
    SAME module object — the shims are aliases, so state and patch targets
    are shared across surfaces."""
    import importlib

    flat = importlib.import_module(module_name)
    qualified = importlib.import_module(f"scripts.{module_name}")
    impl = importlib.import_module(f"quality_gates_toolkit.{module_name}")
    assert flat is impl, f"flat import of {module_name} must alias the impl module"
    assert qualified is impl, f"scripts.{module_name} must alias the impl module"


@pytest.mark.parametrize(
    "module_name", sorted(PRIVATE_PATCH_TARGETS), ids=sorted(PRIVATE_PATCH_TARGETS)
)
def test_scripts_surface_keeps_private_patch_targets(module_name):
    """mock.patch('review._helper') must hit the module whose code runs —
    i.e. the implementation module — so patched behaviour reaches callers."""
    import importlib
    from unittest import mock

    flat = importlib.import_module(module_name)
    impl = importlib.import_module(f"quality_gates_toolkit.{module_name}")
    for name in PRIVATE_PATCH_TARGETS[module_name]:
        assert hasattr(flat, name), f"{module_name}.{name} must stay reachable"
        sentinel = f"patched::{name}"
        with mock.patch.object(flat, name, sentinel, create=True):
            assert getattr(impl, name) == sentinel


def test_public_judge_names_are_identity_shared_across_surfaces():
    """Object identity is the behavioral form of the single-source-of-truth
    requirement (D-0017: prompt literals live in one place): the alias shim
    cannot carry its own copies of the judge prompts, because a re-export
    copy would fail the ``is`` comparison."""
    import importlib

    flat = importlib.import_module("review")
    impl = importlib.import_module("quality_gates_toolkit.review")
    for name in (*JUDGE_SURFACE, "JUDGE_NEUTRALITY_INSTRUCTIONS", "JUDGE_PROMPTS"):
        assert getattr(flat, name) is getattr(impl, name), (
            f"review.{name} must be the same object on both surfaces"
        )


def test_consumer_with_local_scripts_package_can_import_the_judge_api(
    tmp_path: Path,
):
    """The headline D-0017 scenario: a consumer repo that has its own local
    ``scripts/`` package (which shadows any installed ``scripts`` package)
    still imports the judge API cleanly, because the implementation never
    imports through the ``scripts`` name."""
    consumer = tmp_path / "consumer"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "__init__.py").write_text("")
    (consumer / "scripts" / "redaction.py").write_text('redact_secrets = "DECOY"\n')
    probe = (
        "import sys\n"
        "import scripts.redaction as decoy\n"
        "assert decoy.redact_secrets == 'DECOY'\n"
        "from quality_gates_toolkit.review import (\n"
        "    SYSTEM_PROMPT_ARCH,\n"
        "    evaluate_response,\n"
        ")\n"
        "import quality_gates_toolkit.telemetry\n"
        "assert callable(evaluate_response) and len(SYSTEM_PROMPT_ARCH) > 0\n"
        "impl_redaction = sys.modules['quality_gates_toolkit.redaction']\n"
        "assert impl_redaction is not sys.modules['scripts.redaction']\n"
        "assert getattr(impl_redaction, 'redact_secrets') != 'DECOY'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(consumer),
        env={"PYTHONPATH": str(TOOLKIT_ROOT), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"judge import failed under scripts-shadowing consumer:\n{result.stderr}"
    )
