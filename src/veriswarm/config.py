"""Variant table and model parameters, mirroring the CONSTANTS of Semantics.tla."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

SENDER_ACTIONS = ("noop", "acquire", "release", "try_job", "invalid", "spoof_release")
RECEIVER_ACTIONS = ("noop", "try_job")
STRONG_RECEIVER_ACTIONS = ("noop", "try_job", "acquire", "release")
ALL_ACTIONS = frozenset(SENDER_ACTIONS) | frozenset(STRONG_RECEIVER_ACTIONS)
MAINT = "M"
NONE = "none"


@dataclass(frozen=True)
class VariantSpec:
    """How one variant differs from the others. Read by semantics.py only."""

    key: str
    title: str
    purpose: str
    shared_backing: bool
    object_scheme: str  # "unrecorded" | "single" | "per_principal"
    records_identity: bool = True
    receiver_denied: bool = False
    pool_capacity_one: bool = False
    exposes_audit_counter: bool = False
    work_conserving: bool = False
    noisy_receiver: bool = False
    default_schedule: str = "ABM"


VARIANTS: dict[str, VariantSpec] = {
    v.key: v
    for v in [
        VariantSpec("V0", "shared", "positive control", True, "unrecorded", records_identity=False),
        VariantSpec("V1", "audited shared", "accountability alone", True, "single"),
        VariantSpec("V2", "partitioned", "structural defence (space)", False, "per_principal"),
        VariantSpec("V3", "disabled", "isolation without utility", True, "single", receiver_denied=True),
        VariantSpec("V4", "aliased backing", "mutation: partition in name only", True, "per_principal"),
        VariantSpec("V5", "shared quota", "mutation: global capacity pool", False, "per_principal", pool_capacity_one=True),
        VariantSpec("V6", "exposed counter", "mutation: audit sequence in reply", False, "per_principal", exposes_audit_counter=True),
        VariantSpec("V7", "work-conserving", "mutation: schedule depends on sender", False, "per_principal", work_conserving=True),
        VariantSpec("V8", "noisy shared", "probabilistic mitigation", True, "single", noisy_receiver=True),
        VariantSpec("V9", "flushed shared", "structural defence (time)", True, "single", default_schedule="AMBM"),
    ]
}

LEAKY_VARIANTS = ("V0", "V1", "V4", "V5", "V6", "V7", "V8")
ISOLATED_VARIANTS = ("V2", "V3", "V9")


@dataclass(frozen=True)
class ModelConfig:
    variant: str
    schedule: tuple[str, ...] = ("A", "B", "M")
    senders: tuple[str, ...] = ("A",)
    receivers: tuple[str, ...] = ("B",)
    sender_actions: tuple[str, ...] = SENDER_ACTIONS
    receiver_actions: tuple[str, ...] = RECEIVER_ACTIONS
    max_rounds: int = 1
    obs_mode: str = "full"
    sender_mode: str = "free"
    track_audit: bool = True
    spec: VariantSpec = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.variant not in VARIANTS:
            raise ValueError(f"unknown variant {self.variant!r}")
        if set(self.senders) & set(self.receivers):
            raise ValueError("a principal cannot be both sender and receiver")
        allowed = set(self.senders) | set(self.receivers) | {MAINT}
        if not set(self.schedule) <= allowed:
            raise ValueError(f"schedule {self.schedule} uses an unknown principal")
        if self.max_rounds == 0 and self.variant in ("V6", "V7"):
            raise ValueError("V6 and V7 expose counters; unbounded mode cannot track them")
        object.__setattr__(self, "spec", VARIANTS[self.variant])

    @property
    def principals(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.senders) | set(self.receivers)))

    @property
    def track(self) -> bool:
        return self.max_rounds > 0

    @property
    def track_seq(self) -> bool:
        return self.track and (self.track_audit or self.spec.exposes_audit_counter)

    def with_schedule(self, schedule: str | tuple[str, ...]) -> "ModelConfig":
        return replace(self, schedule=tuple(schedule))


def default_config(variant: str, **kw) -> ModelConfig:
    """A config using the variant's own default schedule unless overridden."""
    kw.setdefault("schedule", tuple(VARIANTS[variant].default_schedule))
    return ModelConfig(variant=variant, **kw)
