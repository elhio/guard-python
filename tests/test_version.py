"""
The version is written in two places; this is what keeps them equal.
"""

from __future__ import annotations

from importlib.metadata import version

import guard_client


def test_dunder_version_matches_distribution_metadata():
    """
    Verify that guard_client.__version__ matches the installed package distribution
    metadata version.
    """
    assert guard_client.__version__ == version("guard-client")
