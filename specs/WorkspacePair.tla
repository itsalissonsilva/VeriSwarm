--------------------------- MODULE WorkspacePair ---------------------------
(***************************************************************************)
(* Two executions of Semantics.tla in synchronous self-composition.        *)
(*                                                                         *)
(*   ObsB(Run(b, uA, uB, sched)) = ObsB(Run(b', uA', uB, sched))           *)
(*                                                                         *)
(* for all secrets b, b', all sender choices uA, uA' (chosen independently *)
(* in each copy), and all common receiver choices uB, under a common       *)
(* public schedule sched.                                                  *)
(*                                                                         *)
(* The two copies share only the schedule, the receiver's choice and (V8)  *)
(* the gateway's coin.  Secrets and sender actions are independent.  A     *)
(* no-op is always available to the sender, so coupling the copies slot by *)
(* slot discards no behaviour of either copy.                              *)
(*                                                                         *)
(* Adaptive receivers.  uB is chosen freshly at each receiver slot, so an  *)
(* adaptive receiver strategy is covered: while histories agree it makes   *)
(* the same choice in both copies, and the check fires at the first step   *)
(* where they do not.                                                      *)
(*                                                                         *)
(* Unbounded horizon (MaxRounds = 0).  No history or round counter is      *)
(* kept, so the reachable product is finite with no round bound.  Round    *)
(* and slot labels are public and equal in both copies by synchrony, and a *)
(* receiver's job number is determined by its own earlier responses.  So   *)
(* per-step agreement of (slot, action, response) at every reachable       *)
(* product state implies, by induction on the length of the run, agreement *)
(* of complete histories at every horizon.  lean/ machine-checks this      *)
(* argument for the abstract partitioned system.                           *)
(***************************************************************************)
EXTENDS Semantics

VARIABLES sched, l, r
vars == <<sched, l, r>>

InitPair ==
    /\ sched \in ScheduleFamily
    /\ \E bl, br \in Secrets :
          /\ l = InitState(bl)
          /\ r = InitState(br)

Terminal == IsTerminal(l)

SenderStep ==
    /\ ~Terminal
    /\ SlotOf(sched, l) \in Senders
    /\ \E al \in AllowedSenderActions(l), ar \in AllowedSenderActions(r) :
          /\ l' = StepSlot(sched, l, al, FALSE)
          /\ r' = StepSlot(sched, r, ar, FALSE)
    /\ UNCHANGED sched

ReceiverStep ==
    /\ ~Terminal
    /\ SlotOf(sched, l) \in Receivers
    /\ \E a \in ReceiverActions :
         \E c \in Coins(SlotOf(sched, l), a) :
            /\ l' = StepSlot(sched, l, a, c)
            /\ r' = StepSlot(sched, r, a, c)
    /\ UNCHANGED sched

MaintStep ==
    /\ ~Terminal
    /\ SlotOf(sched, l) = Maint
    /\ l' = StepMaint(sched, l)
    /\ r' = StepMaint(sched, r)
    /\ UNCHANGED sched

TerminalStutter == Terminal /\ UNCHANGED vars

PairNext == SenderStep \/ ReceiverStep \/ MaintStep \/ TerminalStutter

SpecPair == InitPair /\ [][PairNext]_vars

-----------------------------------------------------------------------------
TypeOKPair == StateOK(l) /\ StateOK(r)

(* The two copies stay on the same public position. *)
PublicSynchrony == l.pc = r.pc /\ l.round = r.round

(* The relational property: equal complete receiver observations after every *)
(* step.  Slot labels and logical clock values are part of each event, so    *)
(* logical timing cannot hide in stuttering.                                 *)
ReceiverEquivalent == l.obs = r.obs /\ l.lastObs = r.lastObs

-----------------------------------------------------------------------------
(* Non-vacuity probes: TLC must find a violation of each, proving the check  *)
(* really explored the behaviours it claims to quantify over.                *)

(* The two copies' senders really can act differently... *)
ProbeSendersNeverDiffer == \A p \in Senders : l.lastAct[p] = r.lastAct[p]

(* ...their backing state really can diverge... *)
ProbeBackingNeverDiffers == l.locks = r.locks

(* ...and the receiver really completes jobs while that is happening. *)
ProbeNoReceiverJob ==
    \A q \in Receivers : l.lastResp[q] /= "ok" \/ l.lastAct[q] /= "try_job"
=============================================================================
