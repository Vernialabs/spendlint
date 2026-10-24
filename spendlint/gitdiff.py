"""Lines added by a change, from git. Used by `--diff-base` and by the GitHub Action's PR mode.

Two checkout shapes matter on GitHub:
- `actions/checkout` on a pull_request event checks out the PR *merge commit* (refs/pull/N/merge) at depth 1.
  Its first parent is the base branch tip, so `git diff HEAD^1 HEAD` is exactly what the PR changes.
- A checkout of the PR head (or a local branch) is compared with the base using the merge base (`base...HEAD`),
  falling back to a direct two-point diff when the history is too shallow to find a merge base.
"""
import subprocess

from .scan import parse_diff

GIT_TIMEOUT = 120


class GitError(RuntimeError):
    pass


def git(root, *args, check=True):
    try:
        p = subprocess.run(["git", "-C", str(root), "-c", "core.quotePath=false", *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT)
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out") from None
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])} failed: {p.stderr.strip()[:300]}")
    return p


def repo_prefix(root):
    """Path of `root` inside its repository, with a trailing slash ('' at the top level)."""
    return git(root, "rev-parse", "--show-prefix").stdout.strip()


def _has_commit(root, rev):
    return git(root, "rev-parse", "--verify", "--quiet", rev + "^{commit}", check=False).returncode == 0


def _parents(root):
    out = git(root, "cat-file", "-p", "HEAD").stdout
    return [line.split()[1] for line in out.splitlines() if line.startswith("parent ")]


def _diff(root, *revs):
    # --relative: paths relative to the scanned directory, and only changes under it
    return git(root, "diff", "--no-color", "--no-ext-diff", "--unified=0", "--relative", *revs).stdout


def added_lines(root, base, merge_commit=False, fetch=False):
    """Return ({path: set(added line numbers)}, note). `base` is a revision such as `origin/main`.
    `merge_commit=True` (GitHub pull_request checkouts) prefers HEAD^1 when HEAD is a merge commit.
    `fetch=True` lets it fetch the one missing commit or branch from `origin` (needs network and credentials)."""
    if merge_commit:
        parents = _parents(root)
        if len(parents) == 2:
            first = parents[0]
            if not _has_commit(root, first) and fetch:
                git(root, "fetch", "--no-tags", "--quiet", "--depth=1", "origin", first, check=False)
            if _has_commit(root, first):
                return parse_diff(_diff(root, first, "HEAD")), "lines added by the pull request merge commit"
    if not _has_commit(root, base) and fetch and base.startswith("origin/"):
        branch = base[len("origin/"):]
        git(root, "fetch", "--no-tags", "--quiet", "--depth=100", "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}", check=False)
    if not _has_commit(root, base):
        raise GitError(f"base revision {base!r} not found (fetch it, or check out with fetch-depth: 0)")
    if git(root, "merge-base", base, "HEAD", check=False).returncode == 0:
        return parse_diff(_diff(root, f"{base}...HEAD")), f"lines added since the merge base with {base}"
    return parse_diff(_diff(root, base, "HEAD")), f"lines that differ from {base} (no merge base in a shallow history)"
