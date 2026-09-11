"""Backward-compatibility shim for the moved judge engine (D-0017).

The implementation lives in ``quality_gates_toolkit.review``. This shim
replaces itself in ``sys.modules`` with that module, so the flat surface
(``import review``, used by the test suite), the qualified surface
(``from scripts.review import ...``) and direct execution
(``python3 scripts/review.py``, the llm-pr-review invocation) all resolve
every name — including private helpers used as patch targets — to the moved
module. See tests/test_judge_package.py, which pins the alias contract.
"""

import os
import sys

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import quality_gates_toolkit.review as _impl  # noqa: E402

if __name__ == "__main__":
    _impl.main()

sys.modules[__name__] = _impl
