----------------------------- MODULE Semantics -----------------------------
(***************************************************************************)
(* VeriSwarm: pure single-copy transition semantics of an agent tool       *)
(* gateway with lock-like resources.                                       *)
(*                                                                         *)
(* All state lives in one record value, and all transitions are pure       *)
(* operators from records to records.  Workspace.tla (one execution) and   *)
(* WorkspacePair.tla (two executions, self-composition) both reuse Step    *)
(* below, so the relational check runs on exactly the single-run           *)
(* semantics rather than on a parallel re-statement of it.                 *)
(*                                                                         *)
(* The Python simulator (src/veriswarm/semantics.py) is a separate         *)
(* implementation of the same table.  scripts/correspondence.py compares   *)
(* the two reachable state graphs edge by edge.                            *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS
    Variant,           \* "V0" .. "V9"; see the variant table below
    Senders,           \* principals whose choices may differ between copies
    Receivers,         \* principals whose observations are compared
    SenderActions,     \* actions a sender may choose at its slot
    ReceiverActions,   \* actions a receiver may choose at its slot
    MaxRounds,         \* >= 1: bounded run with full histories
                       \*    0: history-free, unbounded horizon (see below)
    ObsMode,           \* "full" (the declared ObsB) or "resp_only" (a
                       \* deliberately weakened view, used only to show that
                       \* the verdict depends on the observation boundary)
    SenderMode,        \* "free" (any allowed action) or "codebook"
    TrackAudit,        \* TRUE: keep the audit log in the state
    ScheduleStrs,      \* set of schedules, one letter per slot, e.g. {"ABM"}
    AllSchedulesUpTo   \* if > 0, ignore ScheduleStrs and use every schedule
                       \* over Principals \cup {"M"} up to this length

(***************************************************************************)
(* Variant table.                                                          *)
(*                                                                         *)
(*   V0  shared lock, minimal audit         positive control               *)
(*   V1  shared lock, full audit + monitor  accountability alone           *)
(*   V2  one private lock per principal     candidate structural defence   *)
(*   V3  shared lock, receiver ops denied   isolation without utility      *)
(*   V4  V2 names, aliased backing lock     mutation: partition in name    *)
(*   V5  V2 locks + shared capacity pool    mutation: global quota         *)
(*   V6  V2 locks + audit counter in reply  mutation: telemetry exposed    *)
(*   V7  V2 locks + work-conserving slots   mutation: schedule not fixed   *)
(*   V8  shared lock + random busy replies  probabilistic mitigation       *)
(*   V9  shared lock, flush between domains temporal defence (use a        *)
(*                                          schedule such as "AMBM")       *)
(***************************************************************************)
Variants == {"V0", "V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9"}

Principals == Senders \cup Receivers
Maint      == "M"
None       == "none"
AllActions == {"noop", "acquire", "release", "try_job", "invalid",
               "spoof_release"}

(* Unbounded mode keeps no counters or histories, so the state is finite   *)
(* without a round bound.  See WorkspacePair.tla for why per-step          *)
(* agreement there gives agreement of complete histories for all horizons. *)
Track == MaxRounds > 0

ASSUME
    /\ Variant \in Variants
    /\ Senders \cap Receivers = {}
    /\ SenderActions \subseteq AllActions
    /\ ReceiverActions \subseteq AllActions
    /\ "noop" \in SenderActions          \* no-op is always available, so the
    /\ ObsMode \in {"full", "resp_only"} \* pairing never discards a behaviour
    /\ SenderMode \in {"free", "codebook"}
    /\ TrackAudit \in BOOLEAN
    /\ MaxRounds \in Nat
    /\ AllSchedulesUpTo \in Nat
    \* V6 exposes a counter and V7 a clock; neither is tracked without bounds.
    /\ (MaxRounds = 0) => (Variant \notin {"V6", "V7"} /\ SenderMode = "free"
                           /\ ~TrackAudit)

(***************************************************************************)
(* Public schedules.  A schedule is a sequence of slot labels.  It is      *)
(* public and independent of secrets; both copies of a product run share   *)
(* it.  Family size is a finite parameter.                                 *)
(***************************************************************************)
ParseSchedule(str) == [i \in 1..Len(str) |-> SubSeq(str, i, i)]

ScheduleFamily ==
    IF AllSchedulesUpTo > 0
    THEN UNION {[1..k -> Principals \cup {Maint}] : k \in 1..AllSchedulesUpTo}
    ELSE {ParseSchedule(str) : str \in ScheduleStrs}

(***************************************************************************)
(* Resource mapping.  Backing is the physical resource; ObjectName is what *)
(* the audit log calls it.  V4 shows why the two must not be conflated.    *)
(***************************************************************************)
SharedBacking == Variant \in {"V0", "V1", "V3", "V4", "V8", "V9"}

Backing == IF SharedBacking THEN {"L0"} ELSE {"L_" \o p : p \in Principals}

BackingOf(p) == IF SharedBacking THEN "L0" ELSE "L_" \o p

ObjectName(p) ==
    CASE Variant = "V0"                       -> "unrecorded"
      [] Variant \in {"V1", "V3", "V8", "V9"} -> "lock"
      [] OTHER                                -> "lock_" \o p

ActorLabel(p) == IF Variant = "V0" THEN "unrecorded" ELSE p

Denied(p, act) ==
    /\ Variant = "V3"
    /\ p \in Receivers
    /\ act \in {"acquire", "release", "spoof_release", "try_job"}

(* V5 only: capacity 1 across all backing locks.                           *)
PoolFree(locks) == Variant /= "V5" \/ \A x \in Backing : locks[x] = None

(* V8 only: the gateway may answer a receiver's try_job with busy.  The    *)
(* coin is shared between copies in the product, which is the coupling     *)
(* under which a violation implies the two response laws differ.           *)
Coins(p, act) ==
    IF Variant = "V8" /\ p \in Receivers /\ act = "try_job"
    THEN {FALSE, TRUE} ELSE {FALSE}

(***************************************************************************)
(* Return-code table.  Every (action, lock state) pair has a stated        *)
(* response.  Only the listed cases mutate locks.                          *)
(***************************************************************************)
ApplyResource(locks, p, act, coin) ==
    LET l    == BackingOf(p)
        same(code) == [locks |-> locks, resp |-> code, job |-> FALSE]
    IN CASE act = "noop"    -> same("noop")
         [] act = "invalid" -> same("invalid")
         [] Denied(p, act)  -> same("denied")
         [] act = "acquire" ->
              IF locks[l] = None /\ PoolFree(locks)
              THEN [locks |-> [locks EXCEPT ![l] = p], resp |-> "ok",
                    job |-> FALSE]
              ELSE same("busy")
         [] act \in {"release", "spoof_release"} ->
              \* The gateway ignores any identity claimed in the request and
              \* resolves the lock from the caller's trusted handle.
              IF locks[l] = p
              THEN [locks |-> [locks EXCEPT ![l] = None], resp |-> "ok",
                    job |-> FALSE]
              ELSE same("not_owner")
         [] act = "try_job" ->
              \* Atomic attempt: on success the job runs and the acquisition
              \* is released within the same slot, so locks are unchanged.
              IF ~coin /\ locks[l] = None /\ PoolFree(locks)
              THEN [locks |-> locks, resp |-> "ok", job |-> TRUE]
              ELSE same("busy")

(* Synthetic private job output; carries no secret. *)
PrincipalCode(p) == CASE p = "A" -> 1 [] p = "B" -> 2 [] p = "C" -> 3
JobOutput(p, n) == 100000 * PrincipalCode(p) + n

(***************************************************************************)
(* ObsB: the declared receiver observation, mirrored from                  *)
(* src/veriswarm/observations.py.  Deliberately excludes sender actions,   *)
(* sender identity, other principals' logs, and (except in V6, where the   *)
(* defect is exactly this exposure) the gateway's global sequence number.  *)
(***************************************************************************)
ObsFields == {"who", "round", "slot", "clock", "act", "resp", "job", "seq"}

NoObs == [who |-> None, round |-> 0, slot |-> 0, clock |-> 0, act |-> None,
          resp |-> None, job |-> 0, seq |-> 0]

MakeObs(s, p, act, resp, jobOut, newClock, newSeq) ==
    IF ObsMode = "resp_only"
    THEN [who |-> p, round |-> 0, slot |-> 0, clock |-> 0, act |-> None,
          resp |-> resp, job |-> 0, seq |-> 0]
    ELSE [who |-> p, round |-> s.round, slot |-> s.pc, clock |-> newClock,
          act |-> act, resp |-> resp, job |-> jobOut,
          seq |-> IF Variant = "V6" THEN newSeq ELSE 0]

(***************************************************************************)
(* State.                                                                  *)
(***************************************************************************)
Secrets == [1..MaxRounds -> {0, 1}]   \* one fresh private bit per round

InitState(secret) ==
    [pc       |-> 1,
     round    |-> 1,
     clock    |-> 0,
     locks    |-> [x \in Backing |-> None],
     jobs     |-> [p \in Principals |-> 0],
     requests |-> [p \in Principals |-> 0],
     lastAct  |-> [p \in Principals |-> None],
     lastResp |-> [p \in Principals |-> None],
     lastObs  |-> NoObs,
     obs      |-> <<>>,
     audit    |-> <<>>,
     aseq     |-> 0,
     secret   |-> secret]

IsTerminal(s) == Track /\ s.round > MaxRounds

Codebook(bit) == IF bit = 0 THEN "noop" ELSE "acquire"

AllowedSenderActions(s) ==
    IF SenderMode = "codebook"
    THEN {Codebook(s.secret[s.round])}
    ELSE SenderActions

(* The sequence counter is only state when something can read it. *)
TrackSeq == Track /\ (TrackAudit \/ Variant = "V6")

(***************************************************************************)
(* One logical slot.  `who` comes from the public schedule, never from the *)
(* request, so identity cannot be chosen by the caller.                    *)
(***************************************************************************)
StepSlot(sched, s, act, coin) ==
    LET who    == sched[s.pc]
        wrap   == s.pc = Len(sched)
        newPc  == IF wrap THEN 1 ELSE s.pc + 1
        newRnd == IF wrap /\ Track THEN s.round + 1 ELSE s.round
        r      == ApplyResource(s.locks, who, act, coin)
        call   == act /= "noop"             \* a no-op is not a tool call
        reqNow == call /\ act = "try_job"
        seq    == IF call /\ TrackSeq THEN s.aseq + 1 ELSE s.aseq
        \* V7: a work-conserving scheduler reclaims the slot of a sender that
        \* did nothing, so later events happen at an earlier logical time.
        tick   == IF Variant = "V7" /\ who \in Senders /\ act = "noop"
                  THEN 0 ELSE 1
        clk    == IF Track THEN s.clock + tick ELSE s.clock
        n      == IF Track /\ r.job THEN s.jobs[who] + 1 ELSE s.jobs[who]
        jobOut == IF r.job THEN JobOutput(who, IF Track THEN n ELSE 1) ELSE 0
        ev     == MakeObs(s, who, act, r.resp, jobOut, clk, seq)
        aud    == [seq     |-> seq,
                   round   |-> s.round,
                   slot    |-> s.pc,
                   actor   |-> ActorLabel(who),
                   claimed |-> IF act = "spoof_release"
                               THEN "forged" ELSE ActorLabel(who),
                   act     |-> act,
                   object  |-> ObjectName(who),
                   resp    |-> r.resp,
                   job     |-> jobOut]
    IN [pc       |-> newPc,
        round    |-> newRnd,
        clock    |-> clk,
        locks    |-> r.locks,
        jobs     |-> [s.jobs EXCEPT ![who] = n],
        requests |-> IF Track /\ reqNow
                     THEN [s.requests EXCEPT ![who] = @ + 1]
                     ELSE s.requests,
        lastAct  |-> [s.lastAct EXCEPT ![who] = act],
        lastResp |-> [s.lastResp EXCEPT ![who] = r.resp],
        lastObs  |-> IF who \in Receivers THEN ev ELSE s.lastObs,
        obs      |-> IF Track /\ who \in Receivers
                     THEN Append(s.obs, ev) ELSE s.obs,
        audit    |-> IF TrackAudit /\ call
                     THEN Append(s.audit, aud) ELSE s.audit,
        aseq     |-> seq,
        secret   |-> s.secret]

(* Maintenance: the trusted gateway releases every hold.  No tool call,    *)
(* no audit event, no receiver event.                                      *)
StepMaint(sched, s) ==
    LET wrap == s.pc = Len(sched) IN
    [s EXCEPT !.pc    = IF wrap THEN 1 ELSE s.pc + 1,
              !.round = IF wrap /\ Track THEN s.round + 1 ELSE s.round,
              !.clock = IF Track THEN s.clock + 1 ELSE s.clock,
              !.locks = [x \in Backing |-> None]]

SlotOf(sched, s) == sched[s.pc]

(***************************************************************************)
(* Type invariant over one state record.                                   *)
(***************************************************************************)
ObsType ==
    [who: Receivers \cup {None}, round: Nat, slot: Nat, clock: Nat,
     act: AllActions \cup {None}, resp: STRING, job: Nat, seq: Nat]

StateOK(s) ==
    /\ s.pc \in Nat
    /\ s.round \in 1..(MaxRounds + 1)
    /\ s.clock \in Nat
    /\ s.aseq \in Nat
    /\ s.locks \in [Backing -> Principals \cup {None}]
    /\ s.jobs \in [Principals -> Nat]
    /\ s.requests \in [Principals -> Nat]
    /\ s.lastAct \in [Principals -> AllActions \cup {None}]
    /\ s.lastResp \in [Principals -> STRING]
    /\ s.lastObs \in ObsType
    /\ \A i \in 1..Len(s.obs) : s.obs[i] \in ObsType
=============================================================================
