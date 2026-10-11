"""Path-safety helpers shared by the corpus, audit and report tools.

Each tool used to carry its own copy of these checks and the copies had drifted
apart: one rejected only a path component named exactly ``holdout`` (so
``private-holdout/records.jsonl`` passed), one compared only the supplied path
and not the resolved one, and nine copies of the alias test differed in nothing
but their position in the file. The strongest behaviour is kept here:

* ``reject_holdout_path`` refuses a path when any component of the supplied
  path or of the resolved path (symlinks followed) contains ``holdout`` in any
  letter case;
* ``paths_alias`` tells whether an output path would overwrite an input: the
  same resolved path, the same path up to case, or the same existing file
  (hard link, symlink, another spelling);
* ``atomic_write_text`` replaces a file in one step, so a failed write never
  leaves a half-written report behind.

Tools keep their own, usually Korean, error messages by passing them in.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

__all__ = ["atomic_write_text", "holdout_components", "paths_alias", "reject_holdout_path"]

HOLDOUT_MARKER = "holdout"
DEFAULT_HOLDOUT_MESSAGE = "봉인 홀드아웃 경로는 사용할 수 없습니다"


def _resolved(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):  # no home directory, unreadable component, symlink loop
        return None


def holdout_components(path: Path) -> list[str]:
    """Components of ``path`` and of its resolved form that contain ``holdout``.

    The match is on the case-folded text of each component, so ``Holdout``,
    ``private-holdout`` and ``holdout.jsonl`` all count. The path does not have
    to exist.
    """
    path = Path(path)
    candidates = [path]
    resolved = _resolved(path)
    if resolved is not None:
        candidates.append(resolved)
    found: list[str] = []
    for candidate in candidates:
        for part in candidate.parts:
            if HOLDOUT_MARKER in part.casefold() and part not in found:
                found.append(part)
    return found


def reject_holdout_path(path: Path, message: str = DEFAULT_HOLDOUT_MESSAGE) -> None:
    """Raise ``ValueError(message)`` if the path names or leads into a holdout.

    Call it before opening the path. A symlink whose target sits in a holdout
    directory is refused although its own name looks harmless.
    """
    if holdout_components(path):
        raise ValueError(message)


def _case_key(path: Path) -> str:
    return os.path.normcase(str(path)).casefold()


def paths_alias(left: Path, right: Path) -> bool:
    """Whether ``left`` and ``right`` name the same entry or the same existing file.

    True for equal resolved paths, for paths that differ only in letter case,
    and for two existing paths that are the same file (a hard link, a symlink,
    another spelling). Neither path has to exist.
    """
    left, right = Path(left), Path(right)
    left_resolved, right_resolved = _resolved(left), _resolved(right)
    if left_resolved is not None and right_resolved is not None:
        if left_resolved == right_resolved:
            return True
        if _case_key(left_resolved) == _case_key(right_resolved):
            return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def atomic_write_text(path: Path, payload: str) -> None:
    """Write ``payload`` to ``path`` through a flushed temporary file and a rename.

    The directory is created first. The text is written as UTF-8 without
    newline translation. If anything fails, the previous content of ``path`` is
    untouched and the temporary file is removed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    except BaseException:
        try:
            staged.unlink(missing_ok=True)
        finally:
            raise
