"""Company-authorised file imports (CSV or JSON).

The permission record lives on the source, so a file is only accepted once someone
has stated who owns the data and what the project may do with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..services.imports import ValidationReport, read_rows, validate_batch


@dataclass
class FilePreview:
    """What the user sees before confirming an import."""

    kind: str
    path: Path
    rows: list[dict[str, Any]]
    report: ValidationReport

    @property
    def ok(self) -> bool:
        return self.report.ok

    def head(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.rows[:limit]


def preview_file(path: Path | str, *, kind: str | None = None) -> FilePreview:
    """Read and validate a file without writing anything."""
    resolved_kind, rows = read_rows(path, kind=kind)
    report = validate_batch(resolved_kind, rows)
    return FilePreview(kind=resolved_kind, path=Path(path), rows=rows, report=report)
