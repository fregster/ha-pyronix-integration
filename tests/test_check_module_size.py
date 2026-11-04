# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from pathlib import Path, PurePosixPath
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import check_module_size as cms

BUDGET = cms.Budget(default=3, rules=(("tests/**", 5),), exclude=("probe/**",))


@pytest.mark.parametrize(
    ("path", "expected"),
    [("src/x.py", 3), ("tests/a/b.py", 5), ("probe/p.py", None)],
)
def test_limit_for(path: str, expected: int | None) -> None:
    assert BUDGET.limit_for(PurePosixPath(path)) == expected


def test_violations_reports_only_files_over_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.py").write_text("a\nb\nc\n")
    (tmp_path / "src" / "big.py").write_text("a\nb\nc\nd\n")
    found = cms.violations([Path("src/ok.py"), Path("src/big.py")], BUDGET)
    assert found == ["src/big.py: 4 lines > 3. Split it by responsibility."]


def test_repo_config_loads_and_main_passes_on_this_file() -> None:
    root = Path(__file__).parents[1]
    budget = cms.load_budget(root / "pyproject.toml")
    assert budget.default == 300
    assert cms.main([str(Path(__file__).relative_to(root))]) == 0
