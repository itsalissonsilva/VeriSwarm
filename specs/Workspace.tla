----------------------------- MODULE Workspace -----------------------------
(***************************************************************************)
(* One execution of the gateway under a fixed public schedule.             *)
(*                                                                         *)
(* The invariants here are the checks a conventional review would run:     *)
(* typing, lock ownership, identity, job integrity, audit completeness.    *)
(* They are single-state or single-trace properties.  Variants that leak   *)
(* satisfy all of them, which is the point: a leak is a relation between   *)
(* two executions and no property of one execution can express it.         *)
(***************************************************************************)
EXTENDS Semantics

VARIABLES sched, s
vars == <<sched, s>>

Init ==
    /\ sched \in ScheduleFamily
    /\ \E b \in Secrets : s = InitState(b)

Terminal == IsTerminal(s)

SenderSlot ==
    /\ ~Terminal
    /\ SlotOf(sched, s) \in Senders
    /\ \E a \in AllowedSenderActions(s) : s' = StepSlot(sched, s, a, FALSE)
    /\ UNCHANGED sched

ReceiverSlot ==
    /\ ~Terminal
    /\ SlotOf(sched, s) \in Receivers
    /\ \E a \in ReceiverActions :
         \E c \in Coins(SlotOf(sched, s), a) :
            s' = StepSlot(sched, s, a, c)
    /\ UNCHANGED sched

MaintSlot ==
    /\ ~Terminal
    /\ SlotOf(sched, s) = Maint
    /\ s' = StepMaint(sched, s)
    /\ UNCHANGED sched

(* Explicit terminal stutter, so a finished run is not a TLC deadlock while *)
(* any genuinely stuck state still is one.                                  *)
TerminalStutter == Terminal /\ UNCHANGED vars

Next == SenderSlot \/ ReceiverSlot \/ MaintSlot \/ TerminalStutter

Spec     == Init /\ [][Next]_vars
FairSpec == Spec /\ WF_vars(SenderSlot \/ ReceiverSlot \/ MaintSlot)

-----------------------------------------------------------------------------
(* Single-execution invariants. *)

TypeOK == StateOK(s)

(* Each backing lock has at most one holder (functional by construction,    *)
(* so the content is: only a principal mapped to it may hold it, and the    *)
(* quota of V5 is respected).                                               *)
LockOwnership ==
    /\ \A x \in Backing :
         s.locks[x] = None \/ BackingOf(s.locks[x]) = x
    /\ Variant = "V5" =>
         Cardinality({x \in Backing : s.locks[x] /= None}) <= 1

(* A receiver event has exactly the declared fields, and any job output in  *)
(* it is that receiver's own.  The secret and sender actions have no field. *)
PrivateDataAccess ==
    \A i \in 1..Len(s.obs) :
        /\ DOMAIN s.obs[i] = ObsFields
        /\ s.obs[i].who \in Receivers
        /\ s.obs[i].job = 0 \/
           \E n \in 1..(MaxRounds * Len(sched)) :
              s.obs[i].job = JobOutput(s.obs[i].who, n)

(* The recorded actor is the principal the trusted schedule assigned to     *)
(* that slot, even when the request forged an identity.  V0 records none.   *)
GatewayIdentity ==
    Variant = "V0" \/
    \A i \in 1..Len(s.audit) : s.audit[i].actor = sched[s.audit[i].slot]

OkJobEvents(p) ==
    {i \in 1..Len(s.audit) :
        /\ s.audit[i].act = "try_job"
        /\ s.audit[i].resp = "ok"
        /\ s.audit[i].object \in {ObjectName(p)}
        /\ sched[s.audit[i].slot] = p}

(* Each success yields the specified output exactly once: outputs are the   *)
(* consecutive job numbers, and never exceed the number of requests.        *)
JobIntegrity ==
    \A p \in Principals :
        /\ s.jobs[p] <= s.requests[p]
        /\ Cardinality(OkJobEvents(p)) = s.jobs[p]
        /\ {s.audit[i].job : i \in OkJobEvents(p)}
             = {JobOutput(p, n) : n \in 1..s.jobs[p]}

(* Every tool call has an audit record, numbered gaplessly. *)
AuditComplete ==
    /\ Len(s.audit) = s.aseq
    /\ \A i \in 1..Len(s.audit) : s.audit[i].seq = i

(* The legal-action monitor.  It sees the audit log and the public schedule *)
(* and flags forged identities, malformed actions and misattribution.  It   *)
(* has no view of secrets and does not attempt information-flow control.    *)
Flagged(e) ==
    \/ e.act \in {"invalid", "spoof_release"}
    \/ e.claimed /= e.actor
    \/ (Variant /= "V0" /\ e.actor /= sched[e.slot])

(* Every flagged event is one of the actions the policy declares illegal,   *)
(* so a run using only legal actions raises no flag.                        *)
MonitorSound ==
    \A i \in 1..Len(s.audit) :
        Flagged(s.audit[i]) => s.audit[i].act \in {"invalid", "spoof_release"}

(* Utility: every receiver job request has succeeded so far.  Checked with  *)
(* ReceiverActions = {"try_job"}: it holds iff no sender behaviour can make *)
(* a receiver's job fail.                                                   *)
NoBlockedReceiver == \A q \in Receivers : s.jobs[q] = s.requests[q]

-----------------------------------------------------------------------------
(* Action properties. *)

(* Invalid actions never mutate resource state. *)
InvalidInert ==
    [][ (~Terminal /\ SlotOf(sched, s) /= Maint
           /\ s'.lastAct[SlotOf(sched, s)] = "invalid")
        => s'.locks = s.locks ]_vars

(* Local respect, an unwinding condition: a step by one principal leaves    *)
(* every other principal's backing lock unchanged, except at maintenance.   *)
LocalRespect ==
    [][ (~Terminal /\ SlotOf(sched, s) /= Maint) =>
          \A q \in Principals \ {SlotOf(sched, s)} :
            s'.locks[BackingOf(q)] = s.locks[BackingOf(q)] ]_vars

(* Progress under weak fairness of slot advancement. *)
Progress == <>Terminal

-----------------------------------------------------------------------------
(* Non-vacuity probes.  Each is stated so that TLC must find a violation:    *)
(* the violation is a witness that the behaviour is reachable.               *)

ProbeNoReceiverJob == \A q \in Receivers : s.jobs[q] = 0
ProbeNoSenderHold  == \A p \in Senders : s.locks[BackingOf(p)] /= p
ProbeNoEffectiveRelease ==
    \A p \in Senders : ~(s.lastAct[p] = "release" /\ s.lastResp[p] = "ok")
=============================================================================
