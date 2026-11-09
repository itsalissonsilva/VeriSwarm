import VeriSwarm.Partition

/-!
# Each structural condition is necessary

One operational round, sender step then receiver step, with a parameter for which
condition is dropped. With no defect the receiver's view never depends on the sender.
Dropping any single condition gives a concrete pair of sender actions the receiver can
tell apart, and `decide` checks each pair by evaluating the steps.

These are witnesses, not a general theory of necessity: each shows that its defect
reopens the channel in this model.
-/

namespace VeriSwarm.Mutants

open VeriSwarm

/-- Which structural condition is dropped. -/
inductive Defect where
  | none            -- all conditions hold (variant V2)
  | sharedLock      -- no partition at all (V0, V1)
  | aliasedBacking  -- partition in name, one backing lock (V4)
  | sharedQuota     -- private locks, shared capacity 1 (V5)
  | exposedCounter  -- private locks, audit sequence in the reply (V6)
  | workConserving  -- private locks, a sender no-op gives up its slot (V7)
  deriving DecidableEq, Repr

/-- Gateway state for one round. -/
structure Gw where
  lockA : Bool
  lockB : Bool
  seq : Nat
  clock : Nat
  deriving DecidableEq, Repr

def start : Gw := ⟨false, false, 0, 0⟩

/-- Does the sender's lock resolve to the receiver's backing lock? -/
def aliased : Defect → Bool
  | .sharedLock => true
  | .aliasedBacking => true
  | _ => false

/-- The sender's step, changing whichever backing lock its name resolves to. -/
def senderStep (d : Defect) (a : Action) (g : Gw) : Gw :=
  let isCall := a != .noop
  let g1 : Gw :=
    if aliased d then { g with lockB := (nextCell a ⟨g.lockB, 0⟩).held }
    else { g with lockA := (nextCell a ⟨g.lockA, 0⟩).held }
  { g1 with
    seq := if isCall then g.seq + 1 else g.seq
    clock := if d == .workConserving && a == .noop then g.clock else g.clock + 1 }

/-- What the receiver sees of its own slot. -/
structure View where
  resp : Resp
  clock : Nat
  seq : Nat
  deriving DecidableEq, Repr

/-- The receiver's `try_job`: free iff its backing lock is free and capacity remains. -/
def receiverStep (d : Defect) (g : Gw) : View :=
  let poolFull := d == .sharedQuota && (g.lockA || g.lockB)
  let free := !g.lockB && !poolFull
  let seq := g.seq + 1
  { resp := if free then .ok else .busy
    clock := g.clock + 1
    seq := if d == .exposedCounter then seq else 0 }

/-- The receiver's view after one operational round. -/
def view (d : Defect) (a : Action) : View := receiverStep d (senderStep d a start)

/-- With no defect, the receiver's view is independent of the sender's action. -/
theorem partition_closes_channel (a a' : Action) : view .none a = view .none a' := by
  cases a <;> cases a' <;> decide

theorem sharedLock_leaks : view .sharedLock .noop ≠ view .sharedLock .acquire := by decide

theorem aliasedBacking_leaks : view .aliasedBacking .noop ≠ view .aliasedBacking .acquire := by
  decide

theorem sharedQuota_leaks : view .sharedQuota .noop ≠ view .sharedQuota .acquire := by decide

theorem exposedCounter_leaks : view .exposedCounter .noop ≠ view .exposedCounter .acquire := by
  decide

theorem workConserving_leaks : view .workConserving .noop ≠ view .workConserving .acquire := by
  decide

/-- Hide logical time and telemetry, and the last two mutants would look safe. -/
theorem weakened_observation_hides_defects (d : Defect)
    (hd : d = .exposedCounter ∨ d = .workConserving) (a a' : Action) :
    (view d a).resp = (view d a').resp := by
  rcases hd with h | h <;> subst h <;> cases a <;> cases a' <;> decide

end VeriSwarm.Mutants
