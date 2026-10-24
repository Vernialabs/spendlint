#!/usr/bin/env bash
# Replay action.yml's steps locally the way a GitHub runner would, against a PR-shaped checkout
# (no Docker, so no `act`).
# Usage: replay_action.sh <action checkout> <workspace: a clone with a PR branch checked out> <empty run dir>
# Env: BASE (default main), HEAD_BRANCH (default spendlint-e2e), REPO_NAME (only fills GITHUB_REPOSITORY).
# Written for Git Bash on Windows (venv Scripts/ layout); on Linux/macOS change Lib/site-packages below.
set -u
ACTION=$1; WS=$2; RUN=$3
BASE=${BASE:-main}; HEAD_BRANCH=${HEAD_BRANCH:-spendlint-e2e}
HARNESS=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$RUN/temp"

export GITHUB_ACTIONS=true CI=true
export GITHUB_EVENT_NAME=pull_request GITHUB_BASE_REF="$BASE" GITHUB_HEAD_REF="$HEAD_BRANCH"
export GITHUB_REPOSITORY="${REPO_NAME:-owner/repo}" GITHUB_WORKSPACE="$WS" GITHUB_ACTION_PATH="$ACTION"
export RUNNER_TEMP="$RUN/temp" GITHUB_STEP_SUMMARY="$RUN/step_summary.md"
export SPENDLINT_DATA_DIR="$RUN/spendlint-data"          # telemetry state + default log location for this run

# --- harness only: an offline venv. On GitHub, pip downloads setuptools into an isolated build env;
# here no network is allowed, so the venv can see the base interpreter's setuptools+wheel and pip
# is told not to use an index. install.sh reuses an existing venv, so the step itself is unchanged.
python -m venv --system-site-packages "$RUNNER_TEMP/spendlint-venv"
cp "$HARNESS/audit_sitecustomize.py" "$RUNNER_TEMP/spendlint-venv/Lib/site-packages/sitecustomize.py"  # Windows venv layout
export PIP_NO_INDEX=1 PIP_NO_BUILD_ISOLATION=0

cd "$WS"   # GitHub runs every step with the workspace as the working directory

echo "=== step 1: install (action/install.sh)"
GITHUB_OUTPUT="$RUN/out_install" SPENDLINT_AUDIT_LOG="$RUN/audit_install.log" bash "$ACTION/action/install.sh"
echo "install.sh exit: $?"
echo "outputs: $(cat "$RUN/out_install")"
PY=$(sed -n 's/^python=//p' "$RUN/out_install")

# step 2 + step 4 of action.yml, with action.yml's input defaults; extra NAME=value args override them
scan() {
  local name=$1; shift
  env GITHUB_OUTPUT="$RUN/out_scan_$name" SPENDLINT_PYTHON="$PY" SPENDLINT_AUDIT_LOG="$RUN/audit_scan_$name.log" \
      INPUT_PATH=. INPUT_RULES= INPUT_MIN_CONFIDENCE= INPUT_FAIL_ON=none INPUT_DIFF_MODE=auto INPUT_DIFF_BASE= \
      INPUT_CONFIG= INPUT_SARIF_OUTPUT= INPUT_TELEMETRY=auto SPENDLINT_TELEMETRY_URL=none "$@" \
      bash "$ACTION/action/scan.sh" > "$RUN/log_scan_$name.txt" 2>&1
  local step=$?
  local ec; ec=$(sed -n 's/^exit-code=//p' "$RUN/out_scan_$name")
  EXIT_CODE=$ec bash -c 'exit "${EXIT_CODE:-2}"'
  local final=$?
  echo "[$name] scan.sh exit $step | final step (job result) exit $final | outputs: $(grep -v '^sarif-path' "$RUN/out_scan_$name" | tr '\n' ' ')"
}

echo "=== step 2+4 scenarios"
scan default
scan fail_on_info INPUT_FAIL_ON=info
scan fail_on_warning INPUT_FAIL_ON=warning
scan fail_on_warning_info_rules_only INPUT_FAIL_ON=warning INPUT_RULES=R-CTX-008
scan diff_off INPUT_DIFF_MODE=off
scan telemetry_on INPUT_TELEMETRY=on
git checkout -q --detach "$BASE"
git -c user.name=e2e -c user.email=e2e@example.invalid merge -q --no-ff -m "Merge $HEAD_BRANCH into $BASE" "$HEAD_BRANCH"
echo "HEAD is now a merge commit like refs/pull/N/merge: $(git log --format='%h parents=%p' -1)"
scan merge_commit
git checkout -q "$HEAD_BRANCH"
