"""TLC runner and result classifier.

A violation only counts as vulnerability detection when TLC itself reports the
intended property and emits a parseable witness. A syntax failure, timeout, JVM
crash or missing dependency is never an expected violation; each is classified
separately and fails the harness.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .tla_values import TLAParseError, parse_state

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "specs"
JAR = ROOT / "tools" / "tla2tools.jar"
TOOL_LOCK = ROOT / "tools" / "tla2tools.lock.json"

# Exit codes from tlc2.output.EC.ExitStatus, confirmed against actual runs in
# tests/test_tlc_classifier.py.
EXIT_SUCCESS = 0
EXIT_DEADLOCK = 11
EXIT_SAFETY = 12
EXIT_LIVENESS = 13
EXIT_ASSUMPTION = 10
EXIT_SPEC_PARSE = 150
EXIT_CONFIG_PARSE = 151

STATUS_CHECKED = "checked"
STATUS_VIOLATED = "violated"
STATUS_INCOMPLETE = "incomplete"
STATUS_TOOL_ERROR = "tool_error"
STATUS_NOT_RUN = "not_run"

_STATES_RE = re.compile(
    r"([\d.,]+) states? generated, ([\d.,]+) distinct states? found, ([\d.,]+) states? left on queue"
)
_DEPTH_RE = re.compile(r"The depth of the complete state graph search is (\d+)")
_INV_RE = re.compile(r"Error: Invariant (\w+) is violated")
_ACTION_PROP_RE = re.compile(r"Error: Action property (\w+) is violated")
_TEMPORAL_RE = re.compile(r"Error: Temporal properties were violated")
_DEADLOCK_RE = re.compile(r"Error: Deadlock reached")
_DONE_RE = re.compile(r"Model checking completed\. No error has been found\.")
_FP_RE = re.compile(r"calculated \(optimistic\):\s+val = ([0-9.E+-]+)")
_VERSION_RE = re.compile(r"TLC2 Version (\S+) of (\d+ \w+ \d+) \(rev: (\w+)\)")
_STATE_HDR = re.compile(r"^State (\d+): <(.*)>\s*$", re.MULTILINE)
_BACK_TO = re.compile(r"^Back to state", re.MULTILINE)
_STUTTER = re.compile(r"^State \d+: Stuttering", re.MULTILINE)


def _plain_int(text: str) -> int:
    return int(re.sub(r"[.,]", "", text))


@dataclass
class Classified:
    status: str
    violated_property: str | None = None
    kind: str | None = None
    states_generated: int | None = None
    distinct_states: int | None = None
    queue_left: int | None = None
    depth: int | None = None
    fingerprint_collision_estimate: str | None = None
    tlc_version: str | None = None
    trace: list[dict] = field(default_factory=list)
    detail: str = ""


def parse_trace(output: str) -> list[dict]:
    """Extracts the error trace from TLC's console output."""
    headers = list(_STATE_HDR.finditer(output))
    trace = []
    for i, h in enumerate(headers):
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(output)
        body = output[start:end]
        # Stop at the first line that is not part of the conjunction list.
        stop = re.search(
            r"^(?:\d[\d.,]* states? generated|State \d+: Stuttering|Back to state|The |Finished )",
            body,
            re.MULTILINE,
        )
        if stop:
            body = body[: stop.start()]
        action = h.group(2).split(" line ")[0].strip()
        trace.append(
            {"index": int(h.group(1)), "action": action, "vars": parse_state(body.strip())}
        )
    return trace


def classify(exit_code: int | None, output: str, timed_out: bool = False) -> Classified:
    """Maps a finished TLC process to a status. Pure, so it can be tested on fixtures."""
    result = Classified(status=STATUS_TOOL_ERROR)

    if (v := _VERSION_RE.search(output)):
        result.tlc_version = f"{v.group(1)} ({v.group(2)}, rev {v.group(3)})"
    if (m := _STATES_RE.search(output)):
        result.states_generated = _plain_int(m.group(1))
        result.distinct_states = _plain_int(m.group(2))
        result.queue_left = _plain_int(m.group(3))
    if (m := _DEPTH_RE.search(output)):
        result.depth = int(m.group(1))
    if (m := _FP_RE.search(output)):
        result.fingerprint_collision_estimate = m.group(1)

    if timed_out:
        result.status = STATUS_INCOMPLETE
        result.detail = "timed out before TLC finished"
        return result

    if exit_code == EXIT_SUCCESS:
        if _DONE_RE.search(output) and result.queue_left == 0:
            result.status = STATUS_CHECKED
        else:
            result.detail = "exit 0 without a completion message and an empty queue"
        return result

    named = None
    kind = None
    if exit_code == EXIT_SAFETY and (m := _INV_RE.search(output)):
        named, kind = m.group(1), "invariant"
    elif exit_code == EXIT_LIVENESS and (m := _ACTION_PROP_RE.search(output)):
        # TLC reports a violated [][A]_v property with the liveness exit code.
        named, kind = m.group(1), "action_property"
    elif exit_code == EXIT_LIVENESS and _TEMPORAL_RE.search(output):
        named, kind = "temporal", "temporal"
    elif exit_code == EXIT_DEADLOCK and _DEADLOCK_RE.search(output):
        named, kind = "deadlock", "deadlock"

    if named is None:
        result.detail = f"exit {exit_code} is not a recognised property violation"
        return result

    try:
        trace = parse_trace(output)
    except TLAParseError as exc:
        result.detail = f"violation reported but the witness did not parse: {exc}"
        return result
    if not trace:
        result.detail = "violation reported without a witness"
        return result

    result.status = STATUS_VIOLATED
    result.violated_property = named
    result.kind = kind
    result.trace = trace
    return result


@dataclass(frozen=True)
class TLCJob:
    """One TLC run: which module, which configuration, and what is predicted."""

    id: str
    module: str  # "Workspace" or "WorkspacePair"
    spec: str  # SPECIFICATION operator
    constants: dict
    invariants: tuple[str, ...] = ()
    properties: tuple[str, ...] = ()
    expect_status: str = STATUS_CHECKED
    expect_property: str | None = None
    group: str = "core"
    note: str = ""
    workers: int = 1  # >1 only for jobs predicted to pass; witnesses stay deterministic


def _cfg_value(value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (set, frozenset, list, tuple)):
        return "{" + ", ".join(_cfg_value(v) for v in sorted(value)) + "}"
    raise TypeError(f"no cfg form for {value!r}")


def render_cfg(job: TLCJob) -> str:
    lines = [f"\\* Generated by scripts/check_models.py. Job: {job.id}"]
    if job.note:
        lines.append(f"\\* {job.note}")
    lines.append(f"SPECIFICATION {job.spec}")
    for name in job.invariants:
        lines.append(f"INVARIANT {name}")
    for name in job.properties:
        lines.append(f"PROPERTY {name}")
    lines.append("CONSTANTS")
    for key, value in job.constants.items():
        lines.append(f"  {key} = {_cfg_value(value)}")
    return "\n".join(lines) + "\n"


@dataclass
class TLCResult:
    job_id: str
    module: str
    group: str
    constants: dict
    invariants: list
    properties: list
    expect_status: str
    expect_property: str | None
    status: str
    violated_property: str | None
    kind: str | None
    as_expected: bool
    exit_code: int | None
    runtime_seconds: float
    states_generated: int | None
    distinct_states: int | None
    depth: int | None
    fingerprint_collision_estimate: str | None
    tlc_version: str | None
    log_path: str
    log_sha256: str
    cfg_path: str
    trace_path: str | None
    detail: str
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def java_version() -> str:
    try:
        out = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {exc}"
    first = (out.stderr or out.stdout).splitlines()
    return first[0] if first else "unknown"


def run_job(
    job: TLCJob,
    log_dir: Path,
    cfg_dir: Path,
    trace_dir: Path,
    meta_root: Path,
    timeout_s: int = 600,
    dump_dot: Path | None = None,
) -> tuple[TLCResult, Classified]:
    """Runs TLC on one job and preserves stdout, stderr and the exit code."""
    log_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    meta = meta_root / job.id
    if meta.exists():
        shutil.rmtree(meta, ignore_errors=True)

    cfg_path = cfg_dir / f"{job.id}.cfg"
    cfg_path.write_text(render_cfg(job), encoding="utf-8")
    log_path = log_dir / f"{job.id}.log"

    # TLC unpacks its standard modules into java.io.tmpdir. Concurrent JVMs sharing
    # one directory race on those files and fail with a parse error, so each job
    # gets its own.
    private_tmp = meta_root / f"{job.id}.tmp"
    private_tmp.mkdir(parents=True, exist_ok=True)

    cmd = [
        "java", "-Duser.language=en", "-Duser.country=US", f"-Djava.io.tmpdir={private_tmp}",
        "-XX:+UseParallelGC", "-Xmx3g",
        "-cp", str(JAR), "tlc2.TLC",
        "-workers", str(job.workers), "-fp", "0", "-seed", "0",
        "-metadir", str(meta),
        "-config", str(cfg_path),
    ]
    if dump_dot is not None:
        cmd += ["-dump", "dot", str(dump_dot.with_suffix(""))]
    cmd.append(f"{job.module}.tla")

    started = time.perf_counter()
    timed_out = False
    exit_code: int | None = None
    try:
        proc = subprocess.run(
            cmd, cwd=SPECS, capture_output=True, text=True, timeout=timeout_s, encoding="utf-8",
            errors="replace",
        )
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    except OSError as exc:
        stdout, stderr = "", f"could not start java: {exc}"
    runtime = time.perf_counter() - started
    shutil.rmtree(meta, ignore_errors=True)
    shutil.rmtree(private_tmp, ignore_errors=True)

    log_text = (
        f"# command: {' '.join(cmd)}\n# exit_code: {exit_code}\n# timed_out: {timed_out}\n"
        f"# runtime_seconds: {runtime:.3f}\n# --- stdout ---\n{stdout}\n# --- stderr ---\n{stderr}\n"
    )
    log_path.write_text(log_text, encoding="utf-8")

    c = classify(exit_code, stdout, timed_out)
    if exit_code is None and not timed_out:
        c.detail = stderr.strip()

    as_expected = c.status == job.expect_status and (
        job.expect_property is None or c.violated_property == job.expect_property
    )

    trace_path = None
    if c.trace:
        trace_path = trace_dir / f"{job.id}.tlc.json"
        trace_path.write_text(
            json.dumps(
                {
                    "format": "veriswarm-tlc-trace/1",
                    "job_id": job.id,
                    "module": job.module,
                    "constants": _jsonable(job.constants),
                    "violated_property": c.violated_property,
                    "kind": c.kind,
                    "tlc_version": c.tlc_version,
                    "log_sha256": hashlib.sha256(log_text.encode()).hexdigest(),
                    "states": c.trace,
                },
                indent=1,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    result = TLCResult(
        job_id=job.id,
        module=job.module,
        group=job.group,
        constants=_jsonable(job.constants),
        invariants=list(job.invariants),
        properties=list(job.properties),
        expect_status=job.expect_status,
        expect_property=job.expect_property,
        status=c.status,
        violated_property=c.violated_property,
        kind=c.kind,
        as_expected=as_expected,
        exit_code=exit_code,
        runtime_seconds=round(runtime, 3),
        states_generated=c.states_generated,
        distinct_states=c.distinct_states,
        depth=c.depth,
        fingerprint_collision_estimate=c.fingerprint_collision_estimate,
        tlc_version=c.tlc_version,
        log_path=log_path.relative_to(ROOT).as_posix() if log_path.is_relative_to(ROOT) else str(log_path),
        log_sha256=hashlib.sha256(log_text.encode()).hexdigest(),
        cfg_path=cfg_path.relative_to(ROOT).as_posix() if cfg_path.is_relative_to(ROOT) else str(cfg_path),
        trace_path=(trace_path.relative_to(ROOT).as_posix() if trace_path and trace_path.is_relative_to(ROOT) else (str(trace_path) if trace_path else None)),
        detail=c.detail,
        note=job.note,
    )
    return result, c


def _jsonable(constants: dict) -> dict:
    out = {}
    for key, value in constants.items():
        out[key] = sorted(value) if isinstance(value, (set, frozenset)) else value
    return out
