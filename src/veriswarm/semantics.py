"""Pure transition semantics, a separate implementation of specs/Semantics.tla.

States are immutable. `step` is a function of its inputs and touches nothing
else. It shares no code with TLC, which is what makes the state-graph comparison
in correspondence.py a real test rather than a program compared with itself.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import MAINT, NONE, ModelConfig
from .observations import NO_OBS, ObsEvent

Pairs = tuple[tuple[str, object], ...]


def _get(pairs: Pairs, key: str):
    for k, v in pairs:
        if k == key:
            return v
    raise KeyError(key)


def _set(pairs: Pairs, key: str, value) -> Pairs:
    return tuple((k, value if k == key else v) for k, v in pairs)


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    round: int
    slot: int
    actor: str
    claimed: str
    act: str
    object: str
    resp: str
    job: int


@dataclass(frozen=True)
class State:
    pc: int
    round: int
    clock: int
    locks: Pairs
    jobs: Pairs
    requests: Pairs
    lastAct: Pairs
    lastResp: Pairs
    lastObs: ObsEvent
    obs: tuple[ObsEvent, ...]
    audit: tuple[AuditEvent, ...]
    aseq: int
    secret: tuple[int, ...]


def backing_of(cfg: ModelConfig, principal: str) -> str:
    return "L0" if cfg.spec.shared_backing else f"L_{principal}"


def backing_locks(cfg: ModelConfig) -> tuple[str, ...]:
    return tuple(sorted({backing_of(cfg, p) for p in cfg.principals}))


def object_name(cfg: ModelConfig, principal: str) -> str:
    scheme = cfg.spec.object_scheme
    if scheme == "unrecorded":
        return "unrecorded"
    return "lock" if scheme == "single" else f"lock_{principal}"


def actor_label(cfg: ModelConfig, principal: str) -> str:
    return principal if cfg.spec.records_identity else "unrecorded"


_PRINCIPAL_CODE = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8}


def job_output(principal: str, number: int) -> int:
    """Synthetic private job output. Depends on no secret."""
    return 100000 * _PRINCIPAL_CODE[principal] + number


def initial_state(cfg: ModelConfig, secret: tuple[int, ...] | None = None) -> State:
    if secret is None:
        secret = (0,) * cfg.max_rounds
    if len(secret) != cfg.max_rounds:
        raise ValueError("one secret bit per round is required")
    zeros = tuple((p, 0) for p in cfg.principals)
    nones = tuple((p, NONE) for p in cfg.principals)
    return State(
        pc=1,
        round=1,
        clock=0,
        locks=tuple((x, NONE) for x in backing_locks(cfg)),
        jobs=zeros,
        requests=zeros,
        lastAct=nones,
        lastResp=nones,
        lastObs=NO_OBS,
        obs=(),
        audit=(),
        aseq=0,
        secret=tuple(secret),
    )


def is_terminal(cfg: ModelConfig, s: State) -> bool:
    return cfg.track and s.round > cfg.max_rounds


def current_slot(cfg: ModelConfig, s: State) -> str:
    return cfg.schedule[s.pc - 1]


def codebook_action(bit: int) -> str:
    return "noop" if bit == 0 else "acquire"


def allowed_actions(cfg: ModelConfig, s: State, who: str) -> tuple[str, ...]:
    if who in cfg.receivers:
        return cfg.receiver_actions
    if cfg.sender_mode == "codebook":
        return (codebook_action(s.secret[s.round - 1]),)
    return cfg.sender_actions


def coins(cfg: ModelConfig, who: str, action: str) -> tuple[bool, ...]:
    if cfg.spec.noisy_receiver and who in cfg.receivers and action == "try_job":
        return (False, True)
    return (False,)


# ---------------------------------------------------------------------------
# The return-code table. Keyed by (action class, can_take_lock), so each row is
# an explicit response and the only rows that change lock state are marked.
# ---------------------------------------------------------------------------

_DENIABLE = {"acquire", "release", "spoof_release", "try_job"}


def _lock_available(cfg: ModelConfig, locks: Pairs, lock: str) -> bool:
    if _get(locks, lock) != NONE:
        return False
    if cfg.spec.pool_capacity_one:
        return all(holder == NONE for _, holder in locks)
    return True


def _resource_effect(
    cfg: ModelConfig, locks: Pairs, who: str, action: str, coin: bool
) -> tuple[Pairs, str, bool]:
    """Returns (locks after, response code, did a job run)."""
    if action == "noop":
        return locks, "noop", False
    if action == "invalid":
        return locks, "invalid", False
    if cfg.spec.receiver_denied and who in cfg.receivers and action in _DENIABLE:
        return locks, "denied", False

    lock = backing_of(cfg, who)
    if action == "acquire":
        if _lock_available(cfg, locks, lock):
            return _set(locks, lock, who), "ok", False
        return locks, "busy", False
    if action in ("release", "spoof_release"):
        # Identity comes from the trusted slot owner. A forged claim changes the
        # audit record, never whose lock is operated on.
        if _get(locks, lock) == who:
            return _set(locks, lock, NONE), "ok", False
        return locks, "not_owner", False
    if action == "try_job":
        if not coin and _lock_available(cfg, locks, lock):
            return locks, "ok", True
        return locks, "busy", False
    raise ValueError(f"no semantics for action {action!r}")


def step_maintenance(cfg: ModelConfig, s: State) -> State:
    wraps = s.pc == len(cfg.schedule)
    return replace(
        s,
        pc=1 if wraps else s.pc + 1,
        round=s.round + 1 if (wraps and cfg.track) else s.round,
        clock=s.clock + 1 if cfg.track else s.clock,
        locks=tuple((name, NONE) for name, _ in s.locks),
    )


def step_slot(cfg: ModelConfig, s: State, action: str, coin: bool = False) -> State:
    who = current_slot(cfg, s)
    if who == MAINT:
        raise ValueError("step_slot called on a maintenance slot")

    locks, resp, job_ran = _resource_effect(cfg, s.locks, who, action, coin)
    wraps = s.pc == len(cfg.schedule)
    is_call = action != "noop"

    seq = s.aseq + 1 if (is_call and cfg.track_seq) else s.aseq
    slot_consumed = not (cfg.spec.work_conserving and who in cfg.senders and action == "noop")
    clock = s.clock + (1 if slot_consumed else 0) if cfg.track else s.clock

    done = _get(s.jobs, who)
    if job_ran and cfg.track:
        done += 1
    job_out = job_output(who, done if cfg.track else 1) if job_ran else 0

    obs_ev = s.lastObs
    obs = s.obs
    if who in cfg.receivers:
        if cfg.obs_mode == "resp_only":
            obs_ev = ObsEvent(who, 0, 0, 0, NONE, resp, 0, 0)
        else:
            obs_ev = ObsEvent(
                who=who,
                round=s.round,
                slot=s.pc,
                clock=clock,
                act=action,
                resp=resp,
                job=job_out,
                seq=seq if cfg.spec.exposes_audit_counter else 0,
            )
        if cfg.track:
            obs = s.obs + (obs_ev,)

    audit = s.audit
    if cfg.track_audit and is_call:
        actor = actor_label(cfg, who)
        audit = s.audit + (
            AuditEvent(
                seq=seq,
                round=s.round,
                slot=s.pc,
                actor=actor,
                claimed="forged" if action == "spoof_release" else actor,
                act=action,
                object=object_name(cfg, who),
                resp=resp,
                job=job_out,
            ),
        )

    requests = s.requests
    if cfg.track and action == "try_job":
        requests = _set(s.requests, who, _get(s.requests, who) + 1)

    return State(
        pc=1 if wraps else s.pc + 1,
        round=s.round + 1 if (wraps and cfg.track) else s.round,
        clock=clock,
        locks=locks,
        jobs=_set(s.jobs, who, done),
        requests=requests,
        lastAct=_set(s.lastAct, who, action),
        lastResp=_set(s.lastResp, who, resp),
        lastObs=obs_ev,
        obs=obs,
        audit=audit,
        aseq=seq,
        secret=s.secret,
    )


def step(cfg: ModelConfig, s: State, action: str | None = None, coin: bool = False) -> State:
    """One logical slot. Maintenance slots ignore `action`."""
    if current_slot(cfg, s) == MAINT:
        return step_maintenance(cfg, s)
    if action is None:
        raise ValueError("a non-maintenance slot requires an action")
    return step_slot(cfg, s, action, coin)


# Convenience accessors for tests and experiments.
def holder(s: State, lock: str) -> str:
    return _get(s.locks, lock)


def jobs_done(s: State, principal: str) -> int:
    return _get(s.jobs, principal)


def jobs_requested(s: State, principal: str) -> int:
    return _get(s.requests, principal)


def last_response(s: State, principal: str) -> str:
    return _get(s.lastResp, principal)


def state_to_tla(s: State) -> dict:
    """The state as the plain value TLC prints, so the two can be compared."""
    return {
        "pc": s.pc,
        "round": s.round,
        "clock": s.clock,
        "locks": dict(s.locks),
        "jobs": dict(s.jobs),
        "requests": dict(s.requests),
        "lastAct": dict(s.lastAct),
        "lastResp": dict(s.lastResp),
        "lastObs": {
            "who": s.lastObs.who,
            "round": s.lastObs.round,
            "slot": s.lastObs.slot,
            "clock": s.lastObs.clock,
            "act": s.lastObs.act,
            "resp": s.lastObs.resp,
            "job": s.lastObs.job,
            "seq": s.lastObs.seq,
        },
        "obs": [
            {
                "who": e.who,
                "round": e.round,
                "slot": e.slot,
                "clock": e.clock,
                "act": e.act,
                "resp": e.resp,
                "job": e.job,
                "seq": e.seq,
            }
            for e in s.obs
        ],
        "audit": [
            {
                "seq": a.seq,
                "round": a.round,
                "slot": a.slot,
                "actor": a.actor,
                "claimed": a.claimed,
                "act": a.act,
                "object": a.object,
                "resp": a.resp,
                "job": a.job,
            }
            for a in s.audit
        ],
        "aseq": s.aseq,
        "secret": list(s.secret),
    }
