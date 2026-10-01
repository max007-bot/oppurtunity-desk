"""The offline fixture connector.

This is the only supply route enabled by default. It reads clearly marked
synthetic files from ``fixtures/`` and performs no network access at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT

FIXTURES_DIR = PROJECT_ROOT / "fixtures"

DEMO_SOURCE_ID = "fixture_demo"
DEMO_MARKET_SOURCE_ID = "fixture_demo_market"
DEMO_HUMAN_SOURCE_ID = "human_entry_demo"


@dataclass
class Snapshot:
    """One synthetic snapshot: sources plus entity batches."""

    name: str
    sources: list[dict[str, Any]]
    batches: dict[str, list[dict[str, Any]]]
    notes: list[str]
    cost_templates: list[dict[str, Any]] = field(default_factory=list)

    def kinds(self) -> list[str]:
        return [kind for kind, rows in self.batches.items() if rows]


def load_snapshot(path: Path | str) -> Snapshot:
    """Read a snapshot file. Raises if it is not marked as demo data."""
    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))

    if payload.get("data_class") != "synthetic_demo":
        raise ValueError(
            f"{target.name} is not marked as synthetic demo data; refusing to load it as a "
            f"fixture so real records can never be mistaken for demo ones"
        )

    batches = {
        key: list(payload.get(key, []))
        for key in (
            "companies",
            "offers",
            "buyer_briefs",
            "comparables",
            "interactions",
            "contact_policies",
        )
    }
    return Snapshot(
        name=payload.get("name", target.stem),
        sources=list(payload.get("sources", [])),
        batches=batches,
        notes=list(payload.get("notes", [])),
        cost_templates=list(payload.get("cost_templates", [])),
    )


def demo_seed() -> Snapshot:
    return load_snapshot(FIXTURES_DIR / "demo_seed.json")


def demo_updates() -> Snapshot:
    return load_snapshot(FIXTURES_DIR / "demo_updates.json")


def source_response(name: str) -> Any:
    """Load a saved synthetic provider response for adapter tests."""
    target = FIXTURES_DIR / "source_responses" / name
    return json.loads(target.read_text(encoding="utf-8"))
