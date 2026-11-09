"""The VeriSwarm gateway with its decision core in proved Lean code.

`LeanCore` runs `lean/.lake/build/bin/veriswarm-core`, compiled from
`lean/GatewayCore.lean`. That program keeps the lock state and computes every reply with
`serveCall` and `serveMaint`, the functions `core_noninterference` in
`lean/VeriSwarm/Core.lean` is proved about.

`LeanCoreGateway` is the partitioned gateway (V2) built around that core. Python keeps
the parts the proof does not cover and that are trusted: identity from the caller's
handle, the public turn order, request validation and the audit log. Every reply an
agent receives comes from the Lean core. The pure Python semantics still runs alongside
as a check, and any disagreement stops the run.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import ModelConfig
from .gateway import Gateway, SenderReply
from .observations import ObsEvent
from .semantics import job_output

ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / "lean" / ".lake" / "build" / "bin" / ("veriswarm-core.exe" if os.name == "nt" else "veriswarm-core")

# Requests the core understands. Identity has already been fixed by the wrapper, so a
# release with a forged claim operates on the caller's own lock, as in the model.
CORE_ACTION = {"noop": "noop", "acquire": "acquire", "release": "release", "try_job": "try_job",
               "spoof_release": "release"}


class CoreDisagreement(AssertionError):
    """The Lean core and the Python semantics answered a request differently."""


class LeanCore:
    """A running instance of the compiled core, spoken to over its standard streams."""

    def __init__(self, exe: Path = EXE) -> None:
        if not exe.exists():
            raise FileNotFoundError(f"{exe} is missing: build it with `lake build veriswarm-core` in lean/")
        self.exe = exe
        self.proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, encoding="utf-8", bufsize=1)
        self.requests = 0

    def _ask(self, line: str) -> str:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        reply = self.proc.stdout.readline().strip()
        if not reply or reply.startswith("error"):
            raise RuntimeError(f"core refused {line!r}: {reply!r}")
        self.requests += 1
        return reply

    def call(self, principal: str, action: str) -> tuple[str, int]:
        resp, jobs = self._ask(f"call {principal} {action}").split()
        return resp, int(jobs)

    def maint(self) -> None:
        self._ask("maint")

    def reset(self) -> None:
        self._ask("reset")

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()

    def __enter__(self) -> "LeanCore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def core_applies(cfg: ModelConfig) -> bool:
    """The Lean core models one private lock per principal and nothing shared."""
    spec = cfg.spec
    return (not spec.shared_backing and not spec.pool_capacity_one and not spec.exposes_audit_counter
            and not spec.work_conserving and not spec.noisy_receiver and not spec.receiver_denied
            and cfg.track and cfg.obs_mode == "full")


class LeanCoreGateway(Gateway):
    """The partitioned gateway, answering every request from the proved Lean core."""

    def __init__(self, cfg: ModelConfig, *args, core: LeanCore | None = None, **kwargs) -> None:
        if not core_applies(cfg):
            raise ValueError(f"the Lean core models the partitioned design only, not {cfg.variant}")
        super().__init__(cfg, *args, **kwargs)
        self._owns_core = core is None
        self.core = core or LeanCore()
        self.core.reset()
        self.answered = 0

    def maintenance(self) -> None:
        super().maintenance()
        self.core.maint()

    def execute(self, handle, request):
        principal = handle.principal
        python_reply = super().execute(handle, request)  # identity, turn order, validation, audit
        action = dict(self.state.lastAct)[principal]  # the request as validated by the wrapper
        if action not in CORE_ACTION:
            return python_reply  # an invalid request: the wrapper refused it, no lock was touched
        resp, jobs = self.core.call(principal, CORE_ACTION[action])
        self.answered += 1
        job = job_output(principal, jobs) if (action == "try_job" and resp == "ok") else 0
        if isinstance(python_reply, ObsEvent):
            reply = ObsEvent(python_reply.who, python_reply.round, python_reply.slot, python_reply.clock,
                             python_reply.act, resp, job, python_reply.seq)
        else:
            reply = SenderReply(resp)
        if reply != python_reply:
            raise CoreDisagreement(f"{principal} {action}: core {reply!r}, Python {python_reply!r}")
        return reply

    def close(self) -> None:
        if self._owns_core:
            self.core.close()
