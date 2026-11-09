/-!
# Noninterference for the partitioned gateway

An abstract transition system, proved in full generality:

* any type of principals, so any number of them, including infinitely many;
* any public schedule, so any number of rounds and any interleaving;
* any behaviour by every other principal, which may differ arbitrarily between
  the two executions, and so may depend on anything, private bits included;
* an adaptive receiver, whose next action may depend on its own history.

This is a theorem about this abstract system. It does not certify the Python
simulator, the TLA+ model, a real lock implementation, or physical timing.
-/

namespace VeriSwarm

/-- Actions a principal may submit to the gateway. -/
inductive Action where
  | noop
  | acquire
  | release
  | tryJob
  deriving DecidableEq, Repr

/-- Return codes visible to the caller. -/
inductive Resp where
  | noop
  | ok
  | busy
  | notOwner
  deriving DecidableEq, Repr

/-- One principal's private backing lock and private job counter. -/
structure Cell where
  held : Bool
  jobs : Nat
  deriving DecidableEq, Repr

/-- The cell after an action. Depends only on the action and the cell. -/
def nextCell : Action → Cell → Cell
  | .noop, c => c
  | .acquire, c => { c with held := true }
  | .release, c => { c with held := false }
  | .tryJob, c => if c.held then c else { c with jobs := c.jobs + 1 }

/-- The response to an action. Depends only on the action and the cell. -/
def respond : Action → Cell → Resp
  | .noop, _ => .noop
  | .acquire, c => if c.held then .busy else .ok
  | .release, c => if c.held then .ok else .notOwner
  | .tryJob, c => if c.held then .busy else .ok

/-- Trusted maintenance releases the hold. -/
def flush (c : Cell) : Cell := { c with held := false }

/-- A partitioned store has one independent cell per principal. -/
abbrev Store (P : Type) := P → Cell

/-- A slot in the public schedule. -/
inductive Slot (P : Type) where
  | act (p : P)
  | maint
  deriving Repr

/-- What the receiver sees of one of its own slots. -/
structure Event where
  pos : Nat
  act : Action
  resp : Resp
  jobs : Nat
  deriving DecidableEq, Repr

variable {P : Type} [DecidableEq P]

/-- Replaces one principal's cell. The partition is exactly this: nothing else moves. -/
def update (s : Store P) (p : P) (c : Cell) : Store P :=
  fun q => if q = p then c else s q

/--
Runs a public schedule. `r` is the observing receiver. Its action comes from its own
history through `recv`. Every other principal's action comes from `others`, an
arbitrary function.
-/
def runFrom (r : P) (recv : List Event → Action) (others : Nat → P → Action) :
    List (Slot P) → Nat → Store P → List Event → Store P × List Event
  | [], _, s, h => (s, h)
  | .maint :: rest, n, s, h =>
      runFrom r recv others rest (n + 1) (fun q => flush (s q)) h
  | .act p :: rest, n, s, h =>
      if p = r then
        let a := recv h
        let c := s p
        runFrom r recv others rest (n + 1) (update s p (nextCell a c))
          (h ++ [⟨n, a, respond a c, (nextCell a c).jobs⟩])
      else
        runFrom r recv others rest (n + 1) (update s p (nextCell (others n p) (s p))) h

/-! ### The three unwinding conditions -/

/-- Local respect: a step by another principal leaves the receiver's cell unchanged. -/
theorem local_respect (s : Store P) {p r : P} (c : Cell) (hne : p ≠ r) :
    update s p c r = s r := by
  simp [update, Ne.symm hne]

/-- Step consistency: from equal cells, the same action gives equal cells. -/
theorem step_consistent (a : Action) {c d : Cell} (h : c = d) : nextCell a c = nextCell a d := by
  rw [h]

/-- Output consistency: from equal cells, the same action gives the same response. -/
theorem output_consistent (a : Action) {c d : Cell} (h : c = d) : respond a c = respond a d := by
  rw [h]

theorem update_self (s : Store P) (p : P) (c : Cell) : update s p c p = c := by
  simp [update]

/-! ### The main theorem -/

/--
From stores that agree on the receiver's cell, any two behaviours of the other
principals produce the same receiver history and leave the receiver's cell equal.

The stores may differ arbitrarily everywhere else, and so may `others` and `others'`.
-/
theorem receiver_view_independent (r : P) (recv : List Event → Action)
    (others others' : Nat → P → Action) :
    ∀ (sched : List (Slot P)) (n : Nat) (s t : Store P) (h : List Event),
      s r = t r →
        (runFrom r recv others sched n s h).2 = (runFrom r recv others' sched n t h).2 ∧
        (runFrom r recv others sched n s h).1 r = (runFrom r recv others' sched n t h).1 r := by
  intro sched
  induction sched with
  | nil =>
    intro n s t h hs
    exact ⟨rfl, hs⟩
  | cons slot rest ih =>
    intro n s t h hs
    cases slot with
    | maint =>
      simp only [runFrom]
      apply ih
      simp only [hs]
    | act p =>
      simp only [runFrom]
      by_cases hp : p = r
      · subst hp
        simp only [↓reduceIte]
        rw [hs]
        apply ih
        simp only [update_self]
      · simp only [hp, ↓reduceIte]
        apply ih
        rw [local_respect _ _ hp, local_respect _ _ hp]
        exact hs

/-- A clean start, with every lock free and no jobs done. -/
def cleanStore : Store P := fun _ => ⟨false, 0⟩

/-- `k` repetitions of a round. -/
def rounds {α : Type} : Nat → List α → List α
  | 0, _ => []
  | k + 1, round => round ++ rounds k round

/--
**Noninterference for the partitioned gateway.** For any principals, any round, any
number of rounds, any adaptive receiver and any encoder, the receiver's complete
history is the same for all values of the secret.
-/
theorem noninterference {Secret : Type} (r : P) (recv : List Event → Action)
    (encode : Secret → Nat → P → Action) (b b' : Secret)
    (round : List (Slot P)) (k : Nat) :
    (runFrom r recv (encode b) (rounds k round) 0 cleanStore []).2 =
      (runFrom r recv (encode b') (rounds k round) 0 cleanStore []).2 :=
  (receiver_view_independent r recv (encode b) (encode b') (rounds k round) 0
    cleanStore cleanStore [] rfl).1

/--
The equality also holds at every intermediate point, since a prefix of a schedule is
itself a schedule. So there is no first moment at which the histories differ.
-/
theorem noninterference_every_prefix {Secret : Type} (r : P) (recv : List Event → Action)
    (encode : Secret → Nat → P → Action) (b b' : Secret) (sched : List (Slot P)) (m : Nat) :
    (runFrom r recv (encode b) (sched.take m) 0 cleanStore []).2 =
      (runFrom r recv (encode b') (sched.take m) 0 cleanStore []).2 :=
  (receiver_view_independent r recv (encode b) (encode b') (sched.take m) 0
    cleanStore cleanStore [] rfl).1

/-- Non-vacuity: the receiver really does complete jobs in this system. -/
theorem receiver_completes_job :
    (runFrom (P := Nat) 1 (fun _ => .tryJob) (fun _ _ => .acquire)
      [.act 0, .act 1, .maint] 0 cleanStore []).2 = [⟨1, .tryJob, .ok, 1⟩] := by
  decide

/-- Non-vacuity: the two behaviours really do reach different backing states. -/
theorem backing_state_can_differ :
    (runFrom (P := Nat) 1 (fun _ => .tryJob) (fun _ _ => .acquire)
        [.act 0, .act 1] 0 cleanStore []).1 0 ≠
      (runFrom (P := Nat) 1 (fun _ => .tryJob) (fun _ _ => .noop)
        [.act 0, .act 1] 0 cleanStore []).1 0 := by
  decide

end VeriSwarm
