"""What is allowed to be true when the app is reachable from the internet.

The prototype was written for one person on one machine, and almost every
guarantee in it is enforced against that assumption. A hosted copy breaks the
assumption: anyone with the link is a user, there is no authentication, and there
is no access control to add one to.

So the hosted copy is allowed to be exactly one thing — a demonstration on
representative sample stock — and this module makes that a guard rather than a
note in a README. ``DESK_PUBLIC=1`` is set by the hosting configuration. With it
set, live mode, a live database and any network-facing connector are refused at
startup, loudly, before a screen renders.

The point is narrow and worth stating plainly: hosting is safe here *because the
hosted instance cannot hold real data*, not because the app became secure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..config import Config, Mode

PUBLIC_FLAG = "DESK_PUBLIC"


class DeploymentRefused(RuntimeError):
    """Raised when a hosted instance has been pointed at something it must not hold."""


def is_public_deployment(environ: dict[str, str] | None = None) -> bool:
    """Whether this process is serving a publicly reachable copy."""
    env = environ if environ is not None else os.environ
    raw = (env.get(PUBLIC_FLAG) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DeploymentPosture:
    """What this instance is, and what it is therefore permitted to do."""

    public: bool
    mode: Mode
    network_allowed: bool

    @property
    def label(self) -> str:
        if not self.public:
            return "Local instance"
        return "Public demonstration · sample stock only"

    @property
    def caveat(self) -> str:
        if not self.public:
            return (
                "Running locally. Live mode is available here; it is not available on the "
                "hosted copy."
            )
        return (
            "This is a hosted demonstration. It runs on representative sample stock, it "
            "cannot be switched to live data, and it holds no real customer record."
        )


def describe(config: Config, environ: dict[str, str] | None = None) -> DeploymentPosture:
    return DeploymentPosture(
        public=is_public_deployment(environ),
        mode=config.mode,
        network_allowed=config.allow_network,
    )


def guard(config: Config, environ: dict[str, str] | None = None) -> DeploymentPosture:
    """Refuse to start a public instance that could reach real data.

    Called once at startup. It fails rather than degrading, because a hosted
    instance that quietly dropped to demo mode after being pointed at a live
    database would be worse than one that refuses: somebody would believe the
    live data was being served and it would not be.
    """
    posture = describe(config, environ)
    if not posture.public:
        return posture

    problems: list[str] = []
    if config.mode is not Mode.DEMO:
        problems.append(
            f"mode is {config.mode.value!r}; a public instance may only run in demo mode"
        )
    if config.allow_network:
        problems.append(
            "network access is enabled; a public instance has no approved source to reach "
            "and must not make outbound requests"
        )
    if config.ai_enabled:
        problems.append(
            "runtime AI is enabled; the hosted demonstration states that every figure comes "
            "from a readable rule, and that has to stay true"
        )

    if problems:
        raise DeploymentRefused(
            "Refusing to serve a public instance: "
            + "; ".join(problems)
            + ". Unset DESK_PUBLIC to run this configuration locally instead."
        )
    return posture


def ensure_demo_data(db, config: Config, clock) -> str | None:
    """Seed the sample stock on a fresh hosted instance.

    A hosting platform starts from a clean checkout with no database, and the
    seeding step is a command somebody runs locally. Without this, the first
    visitor to a newly deployed copy sees an empty app and no explanation.

    It only ever runs in demo mode, and only when there is nothing there: a
    database that already holds offers is left exactly as it is, so a redeploy
    never overwrites what somebody has entered. Returns a short note when it
    seeded, and nothing when it did not need to.
    """
    from ..config import Mode

    if config.mode is not Mode.DEMO:
        return None

    with db.open() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    if existing:
        return None

    from . import demo as demo_service
    from .workflow import Workflow

    report = demo_service.seed(db, config, clock)
    Workflow(db, config, clock).recompute()
    return f"Sample stock loaded: {report.summary()}"


__all__ = [
    "DeploymentPosture",
    "DeploymentRefused",
    "PUBLIC_FLAG",
    "describe",
    "ensure_demo_data",
    "guard",
    "is_public_deployment",
]
