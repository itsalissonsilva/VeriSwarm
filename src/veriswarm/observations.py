"""The single definition of what a receiver may observe (ObsB).

Mirrored by MakeObs and ObsFields in specs/Semantics.tla. Every path from the
gateway back to a receiver goes through ObsEvent, so a field absent here cannot
reach an agent through the adapter. The verdict for each variant is a statement
about this boundary and nothing wider.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

NO_OBS_WHO = "none"


@dataclass(frozen=True)
class ObsEvent:
    who: str
    round: int
    slot: int
    clock: int
    act: str
    resp: str
    job: int
    seq: int


NO_OBS = ObsEvent(NO_OBS_WHO, 0, 0, 0, "none", "none", 0, 0)
OBS_FIELDS = tuple(f.name for f in fields(ObsEvent))

# Channels that are deliberately outside ObsB. These are listed so nobody reads
# a "checked" result as covering them. They are not solved here.
EXCLUDED_CHANNELS = (
    "sender actions and sender identity metadata",
    "gateway global event sequence numbers (except variant V6, as a defect)",
    "other principals' logs",
    "wall-clock duration of tool calls",
    "CPU, cache and memory-pressure behaviour",
    "network latency",
    "host process state",
    "content-level steganography in legitimate outputs",
    "any channel outside the gateway (shared humans, external services)",
)

# What ObsB contains, for the README and report tables.
INCLUDED_FIELDS = {
    "who": "the observing receiver",
    "round": "public logical round",
    "slot": "public position in the schedule",
    "clock": "logical completion time",
    "act": "the receiver's own action",
    "resp": "the receiver's own return code; busy counts even with no shared text",
    "job": "the receiver's own job output, or 0",
    "seq": "always 0, except V6 where exposing it is the injected defect",
}


def event_to_dict(e: ObsEvent) -> dict:
    return {name: getattr(e, name) for name in OBS_FIELDS}


def weaken(e: ObsEvent) -> ObsEvent:
    """The resp_only view: used only to show the verdict depends on the boundary."""
    return ObsEvent(e.who, 0, 0, 0, "none", e.resp, 0, 0)


def decode_feature(prev: ObsEvent | None, e: ObsEvent, period: int) -> tuple:
    """What a receiver can compute from its own two latest events and the public schedule.

    Uses differences rather than absolute counters so a decoder trained on one run
    applies to another. The first event has no predecessor, so its clock is measured
    against where the same slot would have fallen one period earlier. Without that, the
    first round would get a feature of its own and a decoder entry from a single sample.
    Only receiver-visible fields and the public period are read.
    """
    prev_clock = prev.clock if prev else e.slot - period
    prev_seq = prev.seq if prev else 0
    return (e.resp, e.clock - prev_clock, e.seq - prev_seq)
