"""Row mapping and persistence helpers.

Repository functions take an open ``sqlite3.Connection`` rather than opening their
own, so the caller controls the transaction boundary. That is what lets an import
batch be all-or-nothing.
"""

from . import analysis, base, companies, followup, supply  # noqa: F401

__all__ = ["analysis", "base", "companies", "followup", "supply"]
