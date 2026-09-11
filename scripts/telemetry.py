"""Backward-compatibility shim for the moved module (D-0017).

The implementation lives in ``quality_gates_toolkit.telemetry``. This shim
replaces itself in ``sys.modules`` with that module, so both import surfaces
(``import telemetry`` and ``from scripts.telemetry import ...``) resolve
every name to the moved module. See tests/test_judge_package.py.
"""

import sys

import quality_gates_toolkit.telemetry as _impl

sys.modules[__name__] = _impl
