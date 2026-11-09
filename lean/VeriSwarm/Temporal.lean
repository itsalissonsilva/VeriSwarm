import VeriSwarm.Partition

/-!
# Noninterference for the temporal defence

One shared lock, flushed between domains. Each round is sender, flush, receiver,
flush, which is the schedule `AMBM` of the TLA+ model. No partition is needed,
because no hold survives to the receiver's slot. The cost is an extra logical slot
per round, and no hold can span another principal's turn.

The sender's action really does change the shared lock here. The theorem is that the
flush erases the difference, not that the action was ignored.
-/

namespace VeriSwarm.Temporal

open VeriSwarm

/-- The single shared lock, and the receiver's job count. -/
structure Shared where
  held : Bool
  jobs : Nat
  deriving DecidableEq, Repr

/-- The sender acts on the shared lock. Its job counter is not shared. -/
def senderAct (a : Action) (s : Shared) : Shared :=
  { s with held := (nextCell a ⟨s.held, 0⟩).held }

/-- Trusted maintenance releases the shared lock. -/
def flushShared (s : Shared) : Shared := { s with held := false }

/-- The receiver acts on the same shared lock. -/
def receiverAct (a : Action) (s : Shared) : Shared × Resp :=
  let c := nextCell a ⟨s.held, s.jobs⟩
  (⟨c.held, c.jobs⟩, respond a ⟨s.held, s.jobs⟩)

/-- One round: sender, flush, receiver, flush. -/
def round (a b : Action) (s : Shared) : Shared × Resp :=
  let r := receiverAct b (flushShared (senderAct a s))
  (flushShared r.1, r.2)

/-- The sender really can change the lock, so the flush has something to erase. -/
theorem sender_changes_lock :
    senderAct .acquire ⟨false, 0⟩ ≠ senderAct .noop ⟨false, 0⟩ := by decide

/-- The flush erases every difference a sender's action made. -/
theorem flush_erases_sender (a a' : Action) (s : Shared) :
    flushShared (senderAct a s) = flushShared (senderAct a' s) := by
  simp [flushShared, senderAct]

/-- A run: one sender action per round, and an adaptive receiver. -/
def run (recv : List Resp → Action) : List Action → Shared → List Resp → List Resp
  | [], _, h => h
  | a :: rest, s, h =>
      let r := round a (recv h) s
      run recv rest r.1 (h ++ [r.2])

/-- Any two equally long sender behaviours give the same receiver history. -/
theorem temporal_noninterference (recv : List Resp → Action) :
    ∀ (u u' : List Action), u.length = u'.length →
      ∀ (s : Shared) (h : List Resp), run recv u s h = run recv u' s h := by
  intro u
  induction u with
  | nil =>
    intro u' hlen s h
    cases u' with
    | nil => rfl
    | cons _ _ => simp at hlen
  | cons a rest ih =>
    intro u' hlen s h
    cases u' with
    | nil => simp at hlen
    | cons a' rest' =>
      have hround : round a (recv h) s = round a' (recv h) s := by
        simp only [round]
        rw [flush_erases_sender a a' s]
      simp only [run]
      rw [hround]
      exact ih rest' (by simpa using hlen) _ _

/-- The same shared lock without the flush leaks: the receiver sees different replies. -/
theorem shared_without_flush_leaks :
    (receiverAct .tryJob (senderAct .noop ⟨false, 0⟩)).2 ≠
      (receiverAct .tryJob (senderAct .acquire ⟨false, 0⟩)).2 := by
  decide

end VeriSwarm.Temporal
