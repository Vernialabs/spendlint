#!/usr/bin/env bash
# Step 2 of action.yml: run `spendlint action` (inputs arrive as INPUT_* variables) and record its exit code
# as the `exit-code` output instead of failing here, so the optional SARIF upload still runs.
# The last step of action.yml turns that exit code into the job result.
set -uo pipefail

# -I (isolated mode): the working directory is the scanned repository, so it must never be on sys.path;
# otherwise a file in that repository named like a stdlib module would be imported and run.
"${SPENDLINT_PYTHON:?SPENDLINT_PYTHON is not set}" -I -m spendlint action
code=$?
echo "exit-code=$code" >> "${GITHUB_OUTPUT:?GITHUB_OUTPUT is not set}"
exit 0
