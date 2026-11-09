"""Log-reading monitors. They observe; they do not enforce information flow.

LegalActionMonitor mirrors Flagged in specs/Workspace.tla: it reads audit events
and the public schedule, and flags forged identities, malformed actions and
misattribution. It is a deliberately limited baseline, and the report says so.

RateMonitor is the next step up: it learns how often a principal normally takes
a long-held lock and flags runs above that.
"""

from __future__ import annotations

from dataclasses import dataclass

from .semantics import AuditEvent

ILLEGAL_ACTIONS = frozenset({"invalid", "spoof_release"})


@dataclass(frozen=True)
class MonitorReport:
    events_seen: int
    flags: tuple[AuditEvent, ...]

    @property
    def flag_rate(self) -> float:
        return len(self.flags) / self.events_seen if self.events_seen else 0.0


class LegalActionMonitor:
    name = "legal-action"

    def __init__(self, schedule: tuple[str, ...], identity_recorded: bool = True) -> None:
        self.schedule = schedule
        self.identity_recorded = identity_recorded

    def is_flagged(self, e: AuditEvent) -> bool:
        if e.act in ILLEGAL_ACTIONS or e.claimed != e.actor:
            return True
        return self.identity_recorded and e.actor != self.schedule[e.slot - 1]

    def review(self, audit: tuple[AuditEvent, ...]) -> MonitorReport:
        return MonitorReport(len(audit), tuple(e for e in audit if self.is_flagged(e)))


def audit_is_gapless(audit: tuple[AuditEvent, ...]) -> bool:
    return [e.seq for e in audit] == list(range(1, len(audit) + 1))


def acquire_fraction(audit: tuple[AuditEvent, ...], actor: str = "A") -> float:
    mine = [e for e in audit if e.actor == actor]
    return sum(e.act == "acquire" for e in mine) / len(mine) if mine else 0.0


def max_window_rate(audit: tuple[AuditEvent, ...], window: int = 25, actor: str = "A") -> float:
    """Highest long-hold rate in any sliding window of a principal's calls."""
    flags = [1 if e.act == "acquire" else 0 for e in audit if e.actor == actor]
    if not flags:
        return 0.0
    if len(flags) < window:
        return sum(flags) / len(flags)
    best = cur = sum(flags[:window])
    for i in range(window, len(flags)):
        cur += flags[i] - flags[i - window]
        best = max(best, cur)
    return best / window


class RateMonitor:
    """Flags a run whose peak long-hold rate exceeds a frozen benign quantile."""

    name = "rate"

    def __init__(self, window: int = 25, quantile: float = 0.99) -> None:
        self.window, self.quantile = window, quantile
        self.threshold: float | None = None

    def score(self, audit: tuple[AuditEvent, ...]) -> float:
        return max_window_rate(audit, self.window)

    def fit(self, benign_audits: list[tuple[AuditEvent, ...]]) -> "RateMonitor":
        scores = sorted(self.score(a) for a in benign_audits)
        idx = min(len(scores) - 1, int(self.quantile * len(scores)))
        self.threshold = scores[idx]
        return self

    def flags(self, audit: tuple[AuditEvent, ...]) -> bool:
        if self.threshold is None:
            raise RuntimeError("fit the monitor on benign training data first")
        return self.score(audit) > self.threshold


def lag1_autocorrelation(audit: tuple[AuditEvent, ...], actor: str = "A") -> float:
    """A second log statistic, to avoid resting the comparison on one feature."""
    xs = [1.0 if e.act == "acquire" else 0.0 for e in audit if e.actor == actor]
    if len(xs) < 3:
        return 0.0
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs)
    if var == 0:
        return 0.0
    return sum((a - mean) * (b - mean) for a, b in zip(xs, xs[1:])) / var
