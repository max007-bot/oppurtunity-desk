"""Connectors.

Only the fixture and file connectors are enabled by default. Every live adapter is
disabled until its source-policy record is approved and its scope recorded, and an
unavailable route is reported rather than worked around.
"""

from . import base, fixtures  # noqa: F401

__all__ = ["base", "fixtures"]
