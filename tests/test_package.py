# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
from pathlib import Path

import pyronix_cloud


def test_version_is_a_string() -> None:
    assert isinstance(pyronix_cloud.__version__, str)


def test_package_is_typed() -> None:
    assert (Path(pyronix_cloud.__file__).parent / "py.typed").is_file()


def test_app_profile_is_exported() -> None:
    assert "AppProfile" in pyronix_cloud.__all__
    assert pyronix_cloud.AppProfile().app_version == "6.03.001"
