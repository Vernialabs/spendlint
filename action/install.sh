#!/usr/bin/env bash
# Step 1 of action.yml: create (or reuse) a virtualenv under $RUNNER_TEMP and install spendlint from this
# action's own checkout ($GITHUB_ACTION_PATH). spendlint has no runtime dependencies; pip only needs its
# build backend (setuptools). Writes `python=<venv interpreter>` to $GITHUB_OUTPUT.
set -euo pipefail

action_path="${GITHUB_ACTION_PATH:?GITHUB_ACTION_PATH is not set}"
venv="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/spendlint-venv"

py=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -I -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    py="$candidate"
    break
  fi
done
if [ -z "$py" ]; then
  echo "::error title=spendlint::Python 3.9 or newer is required on the runner; add actions/setup-python before this step."
  exit 2
fi

# Every python call uses -I: the working directory is the scanned repository and must never be on sys.path.
# A venv keeps the install away from the runner's system Python (PEP 668 blocks pip there on Ubuntu 24.04).
[ -d "$venv" ] || "$py" -I -m venv "$venv"
if [ -x "$venv/bin/python" ]; then vpy="$venv/bin/python"; else vpy="$venv/Scripts/python.exe"; fi

"$vpy" -I -m pip install --disable-pip-version-check --quiet "$action_path"
"$vpy" -I -m spendlint --version
echo "python=$vpy" >> "${GITHUB_OUTPUT:?GITHUB_OUTPUT is not set}"
