#!/bin/sh
# Wrapper around scripts/review.py (keeps OpenTelemetry tracing wiring in one
# place). Resolves its own location so it works regardless of the current
# working directory — inside the toolkit's reusable workflows the caller
# checkout (repo/) and the toolkit checkout (toolkit/) are siblings.
set -u
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/review.py" "$@"
