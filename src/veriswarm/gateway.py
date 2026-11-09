"""Trusted gateway: identity from host-side handles, validation, audit.

Capability-limited simulation, not OS sandboxing. Agents are ordinary Python
objects that can only return a ToolRequest and only receive their own view. A
worker process or a Python object boundary would not by itself establish
containment against a real adversary; this models the interface, nothing more.
"""

from __future__ import annotations

import random
import secrets as _secrets
from dataclasses import dataclass

from .config import ALL_ACTIONS, MAINT, ModelConfig
from .observations import ObsEvent
from .semantics import (
    AuditEvent,
    State,
    coins,
    current_slot,
    initial_state,
    is_terminal,
    last_response,
    step,
    step_maintenance,
)


class ScheduleViolation(RuntimeError):
    """A principal tried to act outside its public slot."""


@dataclass(frozen=True)
class ToolRequest:
    """What an agent may submit. `claimed_actor` is untrusted and never used for access."""

    action: str
    claimed_actor: str | None = None


@dataclass(frozen=True)
class Handle:
    """Opaque host-side identity. The driver holds these; agents never do."""

    principal: str
    token: str


@dataclass(frozen=True)
class SenderReply:
    resp: str


class AuditSink:
    """Append-only, host-side audit store. Nothing passes it to an agent."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    def records(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def __len__(self) -> int:
        return len(self._events)


class Gateway:
    """Wraps the pure transition function with trusted identity and audit."""

    def __init__(
        self,
        cfg: ModelConfig,
        secret: tuple[int, ...] | None = None,
        noise_p: float = 0.0,
        rng: random.Random | None = None,
        sink: AuditSink | None = None,
    ) -> None:
        self._cfg = cfg
        self._state: State = initial_state(cfg, secret)
        self._noise_p = noise_p
        self._rng = rng or random.Random(0)
        self._sink = sink if sink is not None else AuditSink()
        self._handles: dict[str, Handle] = {}

    def enroll(self, principal: str) -> Handle:
        if principal not in self._cfg.principals:
            raise ValueError(f"unknown principal {principal!r}")
        handle = Handle(principal, _secrets.token_hex(8))
        self._handles[principal] = handle
        return handle

    # -- trusted-side accessors. The runner uses these; agents do not get them. --
    @property
    def state(self) -> State:
        return self._state

    @property
    def sink(self) -> AuditSink:
        return self._sink

    def terminal(self) -> bool:
        return is_terminal(self._cfg, self._state)

    def current_slot(self) -> str:
        return current_slot(self._cfg, self._state)

    def maintenance(self) -> None:
        if self.current_slot() != MAINT:
            raise ScheduleViolation("maintenance outside its slot")
        self._state = step_maintenance(self._cfg, self._state)

    # -- the only call that can affect the resource. --
    def _validate(self, principal: str, request: ToolRequest) -> str:
        """Maps a raw request to a model action. Unknown input can never mutate state."""
        action = request.action
        forged = request.claimed_actor is not None and request.claimed_actor != principal
        if action not in ALL_ACTIONS or action == "spoof_release":
            return "invalid"
        if forged:
            return "spoof_release" if action == "release" else "invalid"
        return action

    def execute(self, handle: Handle, request: ToolRequest) -> ObsEvent | SenderReply:
        principal = handle.principal
        if self._handles.get(principal) != handle:
            raise ScheduleViolation("unrecognised handle")
        if self.terminal() or self.current_slot() != principal:
            raise ScheduleViolation(f"{principal} acted outside its slot")

        action = self._validate(principal, request)
        coin = False
        if True in coins(self._cfg, principal, action):
            coin = self._rng.random() < self._noise_p

        before = len(self._state.audit)
        self._state = step(self._cfg, self._state, action, coin)
        if len(self._state.audit) > before:
            self._sink.append(self._state.audit[-1])

        if principal in self._cfg.receivers:
            return self._state.lastObs
        return SenderReply(last_response(self._state, principal))
