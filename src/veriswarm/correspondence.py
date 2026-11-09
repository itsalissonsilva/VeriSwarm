"""Tested correspondence between the TLA+ model and the Python simulator.

For each small configuration TLC dumps its whole reachable state graph. The
Python semantics explore the same configuration independently. The two node sets
and edge sets are compared in both directions, so a transition present in only one
is reported either way.

This is evidence of alignment on the listed finite configurations. It is not a
machine-checked refinement proof, and says nothing about configurations not listed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import ModelConfig
from .graph import Graph, canonical, explore_pair, explore_single
from .tla_values import parse_state
from .tlc import STATUS_CHECKED, TLCJob, run_job

_NODE_RE = re.compile(r'^(-?\d+) \[label="(.*)"(?:,[^\]]*)?\];?\s*$', re.MULTILINE)
_EDGE_RE = re.compile(r'^(-?\d+) -> (-?\d+)(?: \[[^\]]*\])?;?\s*$', re.MULTILINE)


def parse_dot(text: str) -> Graph:
    """Reads a TLC `-dump dot` file into canonical nodes and non-stutter edges."""
    ids: dict[str, str] = {}
    for m in _NODE_RE.finditer(text):
        label = m.group(2).replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        ids[m.group(1)] = canonical(parse_state(label))
    edges = set()
    for m in _EDGE_RE.finditer(text):
        a, b = m.group(1), m.group(2)
        # A self-edge is the explicit terminal stutter. The Python graph has none,
        # since a terminal state has no successors.
        if a != b:
            edges.add((ids[a], ids[b]))
    return Graph(set(ids.values()), edges)


@dataclass
class CorrespondenceResult:
    case_id: str
    mode: str
    variant: str
    schedule: str
    rounds: int
    tlc_status: str
    tlc_nodes: int
    tlc_edges: int
    python_nodes: int
    python_edges: int
    nodes_only_in_tlc: int
    nodes_only_in_python: int
    edges_only_in_tlc: int
    edges_only_in_python: int
    match: bool
    graph_sha256: str
    example_difference: str

    def to_dict(self) -> dict:
        return asdict(self)


def digest(graph: Graph) -> str:
    h = hashlib.sha256()
    for node in sorted(graph.nodes):
        h.update(node.encode())
    for a, b in sorted(graph.edges):
        h.update(a.encode())
        h.update(b.encode())
    return h.hexdigest()


def compare(tlc: Graph, py: Graph) -> dict:
    only_t = tlc.nodes - py.nodes
    only_p = py.nodes - tlc.nodes
    e_t = tlc.edges - py.edges
    e_p = py.edges - tlc.edges
    example = ""
    if only_t:
        example = "node only in TLC: " + min(only_t)[:300]
    elif only_p:
        example = "node only in Python: " + min(only_p)[:300]
    elif e_t:
        example = "edge only in TLC from: " + min(e_t)[0][:300]
    elif e_p:
        example = "edge only in Python from: " + min(e_p)[0][:300]
    return {
        "nodes_only_in_tlc": len(only_t),
        "nodes_only_in_python": len(only_p),
        "edges_only_in_tlc": len(e_t),
        "edges_only_in_python": len(e_p),
        "match": not (only_t or only_p or e_t or e_p),
        "example_difference": example,
    }


def _tlc_constants(cfg: ModelConfig) -> dict:
    return {
        "Variant": cfg.variant,
        "Senders": set(cfg.senders),
        "Receivers": set(cfg.receivers),
        "SenderActions": set(cfg.sender_actions),
        "ReceiverActions": set(cfg.receiver_actions),
        "MaxRounds": cfg.max_rounds,
        "ObsMode": cfg.obs_mode,
        "SenderMode": cfg.sender_mode,
        "TrackAudit": cfg.track_audit,
        "ScheduleStrs": {"".join(cfg.schedule)},
        "AllSchedulesUpTo": 0,
    }


def run_case(case_id: str, mode: str, cfg: ModelConfig, work: Path, explore=None) -> CorrespondenceResult:
    """Dumps the TLC graph for one configuration and compares it with Python's."""
    module, spec = ("Workspace", "Spec") if mode == "single" else ("WorkspacePair", "SpecPair")
    job = TLCJob(
        id=f"corr_{case_id}",
        module=module,
        spec=spec,
        constants=_tlc_constants(cfg),
        group="correspondence",
    )
    dot_path = work / f"{job.id}.dot"
    result, _ = run_job(
        job, log_dir=work / "logs", cfg_dir=work / "cfg", trace_dir=work / "traces",
        meta_root=work / "meta", dump_dot=dot_path,
    )
    if result.status != STATUS_CHECKED or not dot_path.exists():
        raise RuntimeError(f"{case_id}: TLC did not finish the graph dump: {result.status} {result.detail}")
    tlc_graph = parse_dot(dot_path.read_text(encoding="utf-8"))
    dot_path.unlink()

    if explore is not None:
        py_graph = explore(cfg)
    else:
        py_graph = explore_single(cfg) if mode == "single" else explore_pair(cfg)

    if result.distinct_states != len(tlc_graph.nodes):
        raise RuntimeError(
            f"{case_id}: TLC reports {result.distinct_states} states but the dump has "
            f"{len(tlc_graph.nodes)}"
        )

    delta = compare(tlc_graph, py_graph)
    return CorrespondenceResult(
        case_id=case_id,
        mode=mode,
        variant=cfg.variant,
        schedule="".join(cfg.schedule),
        rounds=cfg.max_rounds,
        tlc_status=result.status,
        tlc_nodes=len(tlc_graph.nodes),
        tlc_edges=len(tlc_graph.edges),
        python_nodes=len(py_graph.nodes),
        python_edges=len(py_graph.edges),
        graph_sha256=digest(tlc_graph) if delta["match"] else "",
        **delta,
    )


def default_cases() -> list[tuple[str, str, ModelConfig]]:
    """Every variant single-run and product; deeper or wider for the important ones."""
    from .config import VARIANTS, default_config

    cases: list[tuple[str, str, ModelConfig]] = []
    for v in VARIANTS:
        cases.append((f"single_{v}_R2", "single", default_config(v, max_rounds=2, track_audit=True)))
        cases.append((f"pair_{v}_R1", "pair", default_config(v, max_rounds=1, track_audit=False)))
    # Mid-round release is reachable only under the longer schedule.
    cases.append(("single_V2_ABABM_R1", "single", default_config("V2", schedule=tuple("ABABM"), max_rounds=1, track_audit=True)))
    cases.append(("single_V1_ABABM_R1", "single", default_config("V1", schedule=tuple("ABABM"), max_rounds=1, track_audit=True)))
    # No maintenance slot: holds persist across rounds.
    cases.append(("single_V1_AB_R2", "single", default_config("V1", schedule=tuple("AB"), max_rounds=2, track_audit=True)))
    # The codebook sender and unbounded mode.
    cases.append(("pair_V1_codebook_R1", "pair", default_config("V1", max_rounds=1, sender_mode="codebook", track_audit=False)))
    cases.append(("pair_V2_codebook_R2", "pair", default_config("V2", max_rounds=2, sender_mode="codebook", track_audit=False)))
    cases.append(("pair_V2_unbounded", "pair", default_config("V2", max_rounds=0, track_audit=False)))
    cases.append(("pair_V9_unbounded", "pair", default_config("V9", max_rounds=0, track_audit=False)))
    # Weakened observation, three principals and a strong receiver.
    cases.append(("pair_V7_respOnly_R1", "pair", default_config("V7", max_rounds=1, obs_mode="resp_only", track_audit=False)))
    cases.append(("pair_V2_R2", "pair", default_config("V2", max_rounds=2, track_audit=False)))
    cases.append(("single_V2_three_R1", "single", default_config("V2", schedule=tuple("ACBM"), senders=("A", "C"), receivers=("B",), max_rounds=1, track_audit=True)))
    cases.append(("single_V1_threeRecv_R1", "single", default_config("V1", schedule=tuple("ABCM"), senders=("A",), receivers=("B", "C"), max_rounds=1, track_audit=True)))
    from .config import STRONG_RECEIVER_ACTIONS
    cases.append(("single_V2_strong_R1", "single", default_config("V2", receiver_actions=STRONG_RECEIVER_ACTIONS, max_rounds=1, track_audit=True)))
    return cases
