"""The model-checking matrix: every TLC run, with its prediction stated up front.

Predictions are recorded before the run. A mismatch is a finding to investigate,
never something to tune away. In particular ObsB is not weakened to make a check
pass; the two resp_only jobs below exist to show what weakening it would hide.
"""

from __future__ import annotations

from .config import (
    RECEIVER_ACTIONS,
    SENDER_ACTIONS,
    STRONG_RECEIVER_ACTIONS,
    VARIANTS,
)
from .tlc import STATUS_CHECKED, STATUS_VIOLATED, TLCJob

ALL_VARIANTS = tuple(VARIANTS)
LEAKS = {"V0", "V1", "V4", "V5", "V6", "V7", "V8"}
ISOLATED = {"V2", "V3", "V9"}
EQ = "ReceiverEquivalent"
HEAVY_WORKERS = 4

# Configurations reduced after they exceeded the 500-second budget and were
# classified `incomplete`. The first run of each is kept here so the report can
# state what was attempted. Stronger or smaller checks replaced them below.
TIMED_OUT_AND_REPLACED = {
    "schedules_V2_all5_R2": "363 schedules with full histories at R=2; replaced by schedules_V2_all5_unbounded (all horizons) and schedules_V2_all3_R2",
    "schedules_V3_all5_R2": "363 schedules with full histories at R=2; replaced by schedules_V3_all5_unbounded and schedules_V3_all3_R2",
    "strongreceiver_V2_all5_unbounded": "strong receiver on 363 schedules; replaced by strongreceiver_V2_all4_unbounded (120 schedules)",
    "three_senders_V2_R2": "two senders with 6 actions each at R=2; replaced by three_senders_V2_R1 and three_senders_V2_unbounded",
}

SINGLE_INVARIANTS = (
    "TypeOK", "LockOwnership", "PrivateDataAccess", "GatewayIdentity",
    "JobIntegrity", "AuditComplete", "MonitorSound",
)

# Principals whose backing lock is separate, so one's step cannot touch another's.
PARTITIONED = {"V2", "V5", "V6", "V7"}
# Variants where no sender behaviour can make a receiver's job fail.
NEVER_BLOCKED = {"V2", "V6", "V7", "V9"}


def constants(
    variant: str,
    rounds: int,
    schedule: str | None = None,
    *,
    senders=("A",),
    receivers=("B",),
    sender_actions=SENDER_ACTIONS,
    receiver_actions=RECEIVER_ACTIONS,
    obs_mode="full",
    sender_mode="free",
    track_audit=False,
    all_schedules_up_to=0,
) -> dict:
    return {
        "Variant": variant,
        "Senders": set(senders),
        "Receivers": set(receivers),
        "SenderActions": set(sender_actions),
        "ReceiverActions": set(receiver_actions),
        "MaxRounds": rounds,
        "ObsMode": obs_mode,
        "SenderMode": sender_mode,
        "TrackAudit": track_audit,
        "ScheduleStrs": {schedule or VARIANTS[variant].default_schedule},
        "AllSchedulesUpTo": all_schedules_up_to,
    }


def _pair(job_id, variant, rounds, *, group, expect=None, invariants=(EQ,), note="", workers=1, **kw) -> TLCJob:
    leaks = variant in LEAKS
    if expect is None:
        expect = (STATUS_VIOLATED, EQ) if leaks else (STATUS_CHECKED, None)
    return TLCJob(
        id=job_id,
        module="WorkspacePair",
        spec="SpecPair",
        constants=constants(variant, rounds, **kw),
        invariants=tuple(invariants),
        expect_status=expect[0],
        expect_property=expect[1],
        group=group,
        note=note,
        workers=workers,
    )


def core_jobs() -> list[TLCJob]:
    """Relational check of every variant, plus the checks each variant passes alone."""
    jobs: list[TLCJob] = []

    # 1. Free sender: any allowed choice, independently in each copy.
    for v in ALL_VARIANTS:
        for rounds in (1, 2):
            jobs.append(_pair(f"pair_{v}_R{rounds}", v, rounds, group="relational",
                              invariants=("TypeOKPair", "PublicSynchrony", EQ)))
    # Three rounds for the variants that survive, as the state space allows.
    for v in sorted(ISOLATED):
        jobs.append(_pair(f"pair_{v}_R3", v, 3, group="relational", workers=HEAVY_WORKERS,
                          invariants=("TypeOKPair", "PublicSynchrony", EQ)))

    # 2. Codebook sender: the witnesses used for replay and the paired timeline.
    for v in ALL_VARIANTS:
        jobs.append(_pair(f"codebook_{v}_R1", v, 1, group="witness", sender_mode="codebook",
                          note="b=0 -> noop, b=1 -> acquire"))

    # 3. Single-execution checks. These pass for leaky variants too, which is the point.
    for v in ALL_VARIANTS:
        jobs.append(TLCJob(
            id=f"single_{v}_R2",
            module="Workspace",
            spec="FairSpec",
            constants=constants(v, 2, track_audit=True),
            invariants=SINGLE_INVARIANTS,
            properties=("Progress", "InvalidInert"),
            group="single",
        ))

    # 4. Local respect, the first unwinding condition.
    for v in ALL_VARIANTS:
        respected = v in PARTITIONED
        jobs.append(TLCJob(
            id=f"localrespect_{v}_R1",
            module="Workspace",
            spec="Spec",
            constants=constants(v, 1, track_audit=False),
            properties=("LocalRespect",),
            expect_status=STATUS_CHECKED if respected else STATUS_VIOLATED,
            expect_property=None if respected else "LocalRespect",
            group="unwinding",
        ))

    # 5. Utility under every sender behaviour: can any sender stop a receiver's job?
    for v in ALL_VARIANTS:
        safe = v in NEVER_BLOCKED
        jobs.append(TLCJob(
            id=f"utility_{v}_R2",
            module="Workspace",
            spec="Spec",
            constants=constants(v, 2, receiver_actions=("try_job",), track_audit=False),
            invariants=("NoBlockedReceiver",),
            expect_status=STATUS_CHECKED if safe else STATUS_VIOLATED,
            expect_property=None if safe else "NoBlockedReceiver",
            group="utility",
        ))
    return jobs


def strength_jobs() -> list[TLCJob]:
    """The stronger claims: all horizons, schedule families, richer attackers."""
    jobs: list[TLCJob] = []

    # All horizons: no history, no round bound. Per-step agreement implies history
    # agreement at every length (argument in WorkspacePair.tla; Lean proves it).
    for v in ("V2", "V3", "V9"):
        jobs.append(_pair(f"unbounded_{v}", v, 0, group="unbounded",
                          invariants=("TypeOKPair", "PublicSynchrony", EQ),
                          note="MaxRounds = 0: every horizon"))
    # Controls, so "checked" in this mode is known to be able to fail.
    for v in ("V0", "V1", "V4", "V5", "V8"):
        jobs.append(_pair(f"unbounded_{v}", v, 0, group="unbounded",
                          invariants=("TypeOKPair", "PublicSynchrony", EQ),
                          note="MaxRounds = 0: control"))

    # Every public schedule over {A, B, M} up to length 5: 363 schedules at once.
    for v in ("V2", "V3"):
        jobs.append(_pair(f"schedules_{v}_all5_unbounded", v, 0, group="schedules",
                          all_schedules_up_to=5, invariants=(EQ,), workers=HEAVY_WORKERS,
                          note="all 363 schedules, every horizon"))
        jobs.append(_pair(f"schedules_{v}_all3_R2", v, 2, group="schedules",
                          all_schedules_up_to=3, invariants=(EQ,), workers=HEAVY_WORKERS,
                          note="all 39 schedules of length <= 3, full histories"))

    # A receiver that may also take and release locks.
    for v, rounds in (("V2", 2), ("V9", 2)):
        jobs.append(_pair(f"strongreceiver_{v}_R{rounds}", v, rounds, group="strong",
                          receiver_actions=STRONG_RECEIVER_ACTIONS, invariants=(EQ,)))
    jobs.append(_pair("strongreceiver_V2_unbounded", "V2", 0, group="strong",
                      receiver_actions=STRONG_RECEIVER_ACTIONS, invariants=(EQ,)))
    jobs.append(_pair("strongreceiver_V2_all4_unbounded", "V2", 0, group="strong",
                      receiver_actions=STRONG_RECEIVER_ACTIONS, all_schedules_up_to=4,
                      invariants=(EQ,), workers=HEAVY_WORKERS,
                      note="strong receiver, 120 schedules, every horizon"))

    # A schedule in which a sender's release takes effect mid-round.
    jobs.append(_pair("midrelease_V2_ABABM_R1", "V2", 1, group="strong", schedule="ABABM",
                      invariants=(EQ,)))
    jobs.append(_pair("midrelease_V1_ABABM_R2", "V1", 2, group="strong", schedule="ABABM",
                      invariants=(EQ,)))

    # Three principals: two colluding senders, or one sender and two receivers.
    three_sender = dict(senders=("A", "C"), receivers=("B",))
    three_recv = dict(senders=("A",), receivers=("B", "C"))
    jobs.append(_pair("three_senders_V2_R1", "V2", 1, group="three", schedule="ACBM",
                      invariants=(EQ,), **three_sender))
    jobs.append(_pair("three_senders_V2_unbounded", "V2", 0, group="three", schedule="ACBM",
                      invariants=(EQ,), **three_sender))
    jobs.append(_pair("three_senders_V1_R1", "V1", 1, group="three", schedule="ACBM",
                      invariants=(EQ,), **three_sender))
    jobs.append(_pair("three_receivers_V2_R2", "V2", 2, group="three", schedule="ABCM",
                      invariants=(EQ,), **three_recv))
    jobs.append(_pair("three_receivers_V2_unbounded", "V2", 0, group="three", schedule="ABCM",
                      invariants=(EQ,), **three_recv))
    jobs.append(_pair("three_receivers_V1_R1", "V1", 1, group="three", schedule="ABCM",
                      invariants=(EQ,), **three_recv))

    # The verdict depends on the declared observation. Hide logical time and the
    # telemetry field, and the V6 and V7 mutants pass. These two "checked" results
    # are the warning, not a defence.
    for v in ("V6", "V7"):
        jobs.append(_pair(f"weakobs_{v}_R2", v, 2, group="boundary", obs_mode="resp_only",
                          invariants=(EQ,), expect=(STATUS_CHECKED, None),
                          note="weakened observation: shows boundary dependence"))

    # Non-vacuity probes: TLC must find each of these violations.
    for probe in ("ProbeSendersNeverDiffer", "ProbeBackingNeverDiffers", "ProbeNoReceiverJob"):
        jobs.append(_pair(f"probe_pair_{probe}_V2", "V2", 2, group="probe",
                          invariants=(probe,), expect=(STATUS_VIOLATED, probe),
                          note="non-vacuity: this behaviour must be reachable"))
    for probe, schedule in (("ProbeNoReceiverJob", "ABM"), ("ProbeNoSenderHold", "ABM"),
                            ("ProbeNoEffectiveRelease", "ABABM")):
        jobs.append(TLCJob(
            id=f"probe_single_{probe}_V2",
            module="Workspace",
            spec="Spec",
            constants=constants("V2", 2, schedule, track_audit=False),
            invariants=(probe,),
            expect_status=STATUS_VIOLATED,
            expect_property=probe,
            group="probe",
            note="non-vacuity: this behaviour must be reachable",
        ))
    return jobs


def all_jobs() -> list[TLCJob]:
    jobs = core_jobs() + strength_jobs()
    ids = [j.id for j in jobs]
    if len(ids) != len(set(ids)):
        raise AssertionError("duplicate job id")
    return jobs


def schedule_sweep_jobs(variant: str = "V0", max_len: int = 4, rounds: int = 2) -> list[TLCJob]:
    """One TLC run per schedule, to test the leak characterisation in leakage.py.

    Sender actions are the three lock actions the characterisation is about. With all
    six, the sender-only schedules (which have no receiver, so nothing to learn) hit
    the time budget without adding any information.
    """
    import itertools

    from .leakage import leak_predicted_by_schedule

    jobs = []
    for n in range(1, max_len + 1):
        for letters in itertools.product("ABM", repeat=n):
            schedule = "".join(letters)
            leaks = leak_predicted_by_schedule(tuple(letters), rounds)
            jobs.append(_pair(
                f"sweep_{variant}_{schedule}_R{rounds}", variant, rounds,
                group="sweep", schedule=schedule, invariants=(EQ,),
                sender_actions=("noop", "acquire", "release"),
                expect=(STATUS_VIOLATED, EQ) if leaks else (STATUS_CHECKED, None),
            ))
    return jobs
