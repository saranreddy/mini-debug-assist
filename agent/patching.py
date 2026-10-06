"""
Apply LLM-generated unified diffs with ``git apply``.

``git`` is installed in the agent image (agent/Dockerfile); ``patch`` is not, so
nothing here shells out to it. ``git apply`` works outside a repository.

LLM diffs vary: headers with or without the ``a/``/``b/`` prefixes, hunk line
counts that don't add up, and hunks with no context lines. So each diff is tried
with ``-p1`` then ``-p0`` (``--recount`` recomputes the hunk counts), and only
if those fail, again with ``--unidiff-zero`` for zero-context hunks.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

GIT_APPLY_FLAGS = ["--recount", "--whitespace=nowarn"]


class PatchError(Exception):
    """A diff did not apply."""


def apply_unified_diff(diff: str, workdir: str | Path) -> tuple[bool, str]:
    """
    Apply ``diff`` to the files under ``workdir``.

    Returns (success, error message). The tree is unchanged on failure because
    each strip level is dry-run with ``--check`` first.
    """
    if not diff.endswith("\n"):
        diff += "\n"

    workdir = Path(workdir).resolve()
    # Don't let git treat a parent directory's repository as the work tree.
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(workdir.parent)}
    errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="debug-assist-patch-") as patch_dir:
        patch_file = Path(patch_dir) / "fix.patch"
        patch_file.write_text(diff)

        attempts = [["-p1"], ["-p0"], ["-p1", "--unidiff-zero"], ["-p0", "--unidiff-zero"]]
        for extra in attempts:
            label = " ".join(extra)
            base = ["git", "apply", *GIT_APPLY_FLAGS, *extra]
            check = subprocess.run(
                [*base, "--check", str(patch_file)],
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
            )
            if check.returncode != 0:
                errors.append(f"git apply {label}: {check.stderr.strip()}")
                continue

            result = subprocess.run(
                [*base, str(patch_file)],
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True, ""
            errors.append(f"git apply {label}: {result.stderr.strip()}")

    return False, "; ".join(errors)


def patch_file_content(current: str | None, diff: str, file_path: str) -> str:
    """
    Return the content of ``file_path`` after applying ``diff`` to ``current``.

    ``current`` is None for a file that doesn't exist yet (diff from /dev/null).
    Raises PatchError if the diff doesn't apply.
    """
    with tempfile.TemporaryDirectory(prefix="debug-assist-file-") as workdir:
        target = Path(workdir) / file_path
        if current is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(current)

        ok, error = apply_unified_diff(diff, workdir)
        if not ok:
            raise PatchError(f"Diff for {file_path} did not apply: {error}")
        if not target.exists():
            raise PatchError(f"Diff did not produce {file_path}")
        return target.read_text()
