# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Fail when a Python module exceeds its line budget from pyproject.toml.

Keeps every file single-focused: a module over budget must be split by
responsibility. Limits live in ``[tool.module-size]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import sys
import tomllib
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class Budget:
    """Line limits for modules, resolved per path."""

    default: int
    rules: tuple[tuple[str, int], ...]
    exclude: tuple[str, ...]

    def limit_for(self, path: PurePosixPath) -> int | None:
        """Return the line limit for ``path``, or None if it is excluded."""
        if any(path.full_match(pattern) for pattern in self.exclude):
            return None
        for pattern, limit in self.rules:
            if path.full_match(pattern):
                return limit
        return self.default


def load_budget(pyproject: Path) -> Budget:
    """Read ``[tool.module-size]`` from ``pyproject``."""
    with pyproject.open("rb") as handle:
        config = cast("dict[str, object]", tomllib.load(handle))
    tool = cast("dict[str, object]", config["tool"])
    section = cast("dict[str, object]", tool["module-size"])
    rules = cast("list[dict[str, object]]", section.get("rules", []))
    return Budget(
        default=cast("int", section["default"]),
        rules=tuple((cast("str", r["glob"]), cast("int", r["max"])) for r in rules),
        exclude=tuple(cast("list[str]", section.get("exclude", []))),
    )


def violations(files: Sequence[Path], budget: Budget) -> list[str]:
    """Return one message per file that is over its limit."""
    found: list[str] = []
    for file in files:
        limit = budget.limit_for(PurePosixPath(file.as_posix()))
        if limit is None:
            continue
        count = len(file.read_text(encoding="utf-8").splitlines())
        if count > limit:
            found.append(
                f"{file}: {count} lines > {limit}. Split it by responsibility."
            )
    return found


def main(argv: Sequence[str] | None = None) -> int:
    """Check the given files (pre-commit passes them); return a process exit code."""
    args = sys.argv[1:] if argv is None else list(argv)
    budget = load_budget(Path("pyproject.toml"))
    problems = violations([Path(a) for a in args], budget)
    for problem in problems:
        print(problem, file=sys.stderr)  # noqa: T201 - CLI output
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
