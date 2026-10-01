"""Streamlit screens.

Each module exposes ``render()``. No screen contains business rules: they read from
``desk.services`` so the same logic backs the UI and the CLI.
"""

from . import (  # noqa: F401
    businesses,
    callprep,
    capture,
    components,
    matches,
    source_admin,
    sources,
    templates_admin,
    today,
)

__all__ = [
    "businesses",
    "callprep",
    "capture",
    "components",
    "matches",
    "source_admin",
    "sources",
    "templates_admin",
    "today",
]
