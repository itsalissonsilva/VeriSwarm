"""The same logic on real lock primitives, for differential testing.

`semantics.py` models a lock as a value. To check that the model's return codes are
not artefacts of that choice, this module runs the same actions against
`threading.Lock` and against `O_CREAT | O_EXCL` lock files, the pattern behind
`.git/index.lock`. tests/test_backends.py requires identical responses on every
enumerated action sequence.

It is only a differential test of return codes under the logical schedule. It adds no
sandboxing and measures no real timing.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from .config import MAINT, ModelConfig
from .semantics import backing_of


class PrimitiveBackend:
    name = "abstract"

    def create(self, lock: str) -> None:
        raise NotImplementedError

    def try_take(self, lock: str) -> bool:
        raise NotImplementedError

    def give_back(self, lock: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class ThreadingBackend(PrimitiveBackend):
    name = "threading.Lock"

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}

    def create(self, lock: str) -> None:
        self._locks.setdefault(lock, threading.Lock())

    def try_take(self, lock: str) -> bool:
        return self._locks[lock].acquire(blocking=False)

    def give_back(self, lock: str) -> None:
        self._locks[lock].release()


class LockfileBackend(PrimitiveBackend):
    name = "O_EXCL lockfile"

    def __init__(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="veriswarm_locks_")
        self._paths: dict[str, Path] = {}

    def create(self, lock: str) -> None:
        self._paths[lock] = Path(self._dir.name) / f"{lock}.lock"

    def try_take(self, lock: str) -> bool:
        try:
            os.close(os.open(self._paths[lock], os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            return False

    def give_back(self, lock: str) -> None:
        os.unlink(self._paths[lock])

    def close(self) -> None:
        self._dir.cleanup()


class ConcreteGateway:
    """Actions over real primitives. Owner tracking lives here, as a gateway would hold it."""

    def __init__(self, cfg: ModelConfig, backend: PrimitiveBackend) -> None:
        self.cfg = cfg
        self.backend = backend
        self.owner: dict[str, str | None] = {}
        for principal in cfg.principals:
            lock = backing_of(cfg, principal)
            backend.create(lock)
            self.owner[lock] = None

    def _pool_ok(self) -> bool:
        if not self.cfg.spec.pool_capacity_one:
            return True
        return all(holder is None for holder in self.owner.values())

    def call(self, principal: str, action: str, coin: bool = False) -> str:
        if action == "noop":
            return "noop"
        if action == "invalid":
            return "invalid"
        if self.cfg.spec.receiver_denied and principal in self.cfg.receivers:
            return "denied"
        lock = backing_of(self.cfg, principal)
        if action == "acquire":
            if self._pool_ok() and self.backend.try_take(lock):
                self.owner[lock] = principal
                return "ok"
            return "busy"
        if action in ("release", "spoof_release"):
            if self.owner[lock] == principal:
                self.backend.give_back(lock)
                self.owner[lock] = None
                return "ok"
            return "not_owner"
        if action == "try_job":
            if coin or not self._pool_ok():
                return "busy"
            if self.backend.try_take(lock):
                self.backend.give_back(lock)
                return "ok"
            return "busy"
        raise ValueError(action)

    def flush(self) -> None:
        for lock, holder in self.owner.items():
            if holder is not None:
                self.backend.give_back(lock)
                self.owner[lock] = None


def run_responses(cfg: ModelConfig, backend: PrimitiveBackend, slots: list[tuple[str, str]]) -> list[str]:
    """Runs (label, action) pairs and returns the response of every non-maintenance slot."""
    gateway = ConcreteGateway(cfg, backend)
    out = []
    try:
        for label, action in slots:
            if label == MAINT:
                gateway.flush()
            else:
                out.append(gateway.call(label, action))
    finally:
        gateway.flush()
        backend.close()
    return out
