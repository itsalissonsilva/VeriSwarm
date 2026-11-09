"""Schedule driver: runs controllers through the gateway and records evidence.

Records are kept apart by who may read them. `events` holds gateway-side facts.
`evaluator` holds secrets and guesses. Neither is passed to a controller.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .agents import ReceiverView, SenderView
from .config import MAINT, ModelConfig
from .gateway import AuditSink, Gateway, Handle, SenderReply
from .observations import ObsEvent
from .semantics import State, jobs_done, jobs_requested, backing_of

RAW_EVENT_FIELDS = (
    "run_id", "variant", "seed", "controller", "round", "slot", "principal",
    "action", "resource", "response", "job_completed", "audit_seq",
)


@dataclass
class Episode:
    run_id: str
    variant: str
    seed: int
    events: list[dict] = field(default_factory=list)
    evaluator: list[dict] = field(default_factory=list)
    receiver_events: dict[str, list[ObsEvent]] = field(default_factory=dict)
    audit: tuple = ()
    final_state: State | None = None
    slots: int = 0

    def jobs(self, principal: str) -> int:
        return jobs_done(self.final_state, principal)

    def requests(self, principal: str) -> int:
        return jobs_requested(self.final_state, principal)


def run_episode(
    cfg: ModelConfig,
    senders: dict,
    receivers: dict,
    bits: list[int] | None,
    seed: int,
    run_id: str,
    noise_p: float = 0.0,
    gateway_factory=Gateway,
) -> Episode:
    """Runs cfg.max_rounds rounds. `bits` gives one private bit per round, or None.
    `gateway_factory` lets the partitioned design run on the proved Lean core."""
    rng = random.Random(f"noise|{seed}|{run_id}")
    secret = tuple(bits) if bits is not None else (0,) * cfg.max_rounds
    sink = AuditSink()
    gw = gateway_factory(cfg, secret=secret, noise_p=noise_p, rng=rng, sink=sink)
    handles: dict[str, Handle] = {p: gw.enroll(p) for p in cfg.principals}

    ep = Episode(run_id, cfg.variant, seed)
    ep.receiver_events = {r: [] for r in cfg.receivers}
    replies: dict[str, str | None] = {s: None for s in cfg.senders}

    while not gw.terminal():
        state = gw.state
        slot_label = gw.current_slot()
        slot_index, round_no = state.pc, state.round
        ep.slots += 1

        if slot_label == MAINT:
            gw.maintenance()
            continue

        audited_before = len(sink)
        if slot_label in cfg.senders:
            controller = senders[slot_label]
            bit = bits[round_no - 1] if bits is not None else None
            view = SenderView(round_no, slot_index, bit, replies[slot_label])
            result = gw.execute(handles[slot_label], controller.act(view))
            assert isinstance(result, SenderReply)
            replies[slot_label] = result.resp
            response = result.resp
            action = dict(gw.state.lastAct)[slot_label]
            job = 1 if (action == "try_job" and result.resp == "ok") else 0
        else:
            controller = receivers[slot_label]
            view = ReceiverView(round_no, slot_index, tuple(ep.receiver_events[slot_label]))
            result = gw.execute(handles[slot_label], controller.act(view))
            assert isinstance(result, ObsEvent)
            ep.receiver_events[slot_label].append(result)
            response, action = result.resp, result.act
            job = 1 if result.job else 0

        ep.events.append(
            {
                "run_id": run_id,
                "variant": cfg.variant,
                "seed": seed,
                "controller": controller.name,
                "round": round_no,
                "slot": slot_index,
                "principal": slot_label,
                "action": action,
                "resource": backing_of(cfg, slot_label),
                "response": response,
                "job_completed": job,
                "audit_seq": sink.records()[-1].seq if len(sink) > audited_before else None,
            }
        )

    ep.audit = sink.records()
    ep.final_state = gw.state
    return ep
