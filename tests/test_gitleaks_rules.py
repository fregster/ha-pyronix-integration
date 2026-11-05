# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The project's gitleaks rules flag full local paths and nothing else.

Samples are assembled from parts at run time so that this file never contains a
path the rules would flag (it is scanned too).
"""

import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
from typing import Any

import pytest

CONFIG = Path(__file__).parents[1] / ".gitleaks.toml"
HOME = "Us" + "ers"
WIN = "\\"

SHOULD_FLAG = [
    "cd /" + HOME + "/alice/git/repo",
    'path = "/' + "home" + '/bob/.config/x"',
    "C:" + WIN + HOME + WIN + "Carol" + WIN + "repo",
    "tmp at /var/" + "folders/xy/abc/T/",
    "see /private/" + "tmp/scratch/x",
]
SHOULD_PASS = [
    "/" + "home" + "/runner/work/repo",
    "/" + HOME + "/you/repo",
    "/" + "home" + "/<you>/repo",
    "~/git/repo",
    "/config/custom_components/pyronix_cloud",
    "/usr/local/bin/python3",
    "cd ha-pyronix-integration",
]


def _rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = tomllib.loads(CONFIG.read_text())["rules"]
    return rules


def _allowed(rule: dict[str, Any], match: re.Match[str]) -> bool:
    for allow in rule.get("allowlists", []):
        target = match.group(0) if allow["regexTarget"] == "match" else _secret(match)
        if any(re.search(pattern, target) for pattern in allow["regexes"]):
            return True
    return False


def _secret(match: re.Match[str]) -> str:
    return next((group for group in match.groups() if group), match.group(0))


def _flagged(line: str) -> list[str]:
    hits = []
    for rule in _rules():
        match = re.search(rule["regex"], line)
        if match and not _allowed(rule, match):
            hits.append(rule["id"])
    return hits


def test_extends_the_default_rules() -> None:
    assert tomllib.loads(CONFIG.read_text())["extend"]["useDefault"] is True


@pytest.mark.parametrize("line", SHOULD_FLAG)
def test_local_paths_are_flagged(line: str) -> None:
    assert _flagged(line)


@pytest.mark.parametrize("line", SHOULD_PASS)
def test_placeholders_and_system_paths_pass(line: str) -> None:
    assert _flagged(line) == []


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_real_gitleaks_agrees(tmp_path: Path) -> None:
    # Python's re and Go's RE2 agree on these patterns, but the default global
    # allowlist only exists in the real binary, so check end to end as well.
    for index, line in enumerate(SHOULD_FLAG + SHOULD_PASS):
        (tmp_path / f"sample{index}.txt").write_text(line + "\n")
    report = tmp_path / "report.json"
    subprocess.run(  # noqa: S603 - fixed argv; gitleaks resolved by shutil.which
        [
            str(shutil.which("gitleaks")),
            *("dir", "--no-banner", "--exit-code", "0", "--redact"),
            *("-c", str(CONFIG), "-f", "json", "-r", str(report), str(tmp_path)),
        ],
        check=True,
        capture_output=True,
    )
    findings: list[dict[str, Any]] = json.loads(report.read_text())
    flagged = {Path(finding["File"]).name for finding in findings}
    assert flagged == {f"sample{index}.txt" for index in range(len(SHOULD_FLAG))}
