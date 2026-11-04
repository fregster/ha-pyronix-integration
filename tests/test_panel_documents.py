# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from __future__ import annotations

import json

from pyronix_cloud.testing import dump
from pyronix_cloud.testing.panel_documents import SET, SETTING, PanelDocuments


def test_untouched_dump_is_replayed_verbatim() -> None:
    assert PanelDocuments(dump.login_dump(), {}).login_dump() == dump.login_dump()


def test_overrides_apply_to_dump_pages() -> None:
    docs = PanelDocuments(dump.login_dump(), {3: 2})
    page = json.loads(docs.login_dump()[3])
    assert [(d["R"], d["V"], d["S"]) for d in page["Detail"]] == [
        (2, 0, "Unset"),
        (3, 2, "2"),
    ]


def test_change_areas_skips_unknown_areas() -> None:
    areas: dict[int, int] = {}
    docs = PanelDocuments(dump.login_dump(), areas)
    updates = docs.change_areas(bytes([5, 200]), [SETTING, SET])
    assert [json.loads(u)["Detail"][0]["S"] for u in updates] == ["Setting", "Set"]
    assert areas == {5: SET}


def test_log_defaults_to_ten_entries() -> None:
    log = json.loads(PanelDocuments([], {}).log(0))
    assert log["Entries"] == len(log["Detail"]) == 10
