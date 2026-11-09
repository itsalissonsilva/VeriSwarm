import VeriSwarm.Partition

/-!
# The gateway core, bound to the proof

`GatewayCore.lean` compiles to the program that holds the VeriSwarm gateway's lock
state and decides every reply. It does nothing but call `serveCall` and `serveMaint`
below. The Python wrapper around it only checks identity, enforces the public turn
order and writes the audit log; it takes each reply from this program.

This file proves two things about those two functions:

* `core_noninterference`: over any two request streams that differ only in what
  *other* principals ask for, the replies the core sends to the receiver are
  identical. This is noninterference for the deployed request loop itself.
* `runFrom_act` and `runFrom_maint`: the abstract model of `Partition.lean` takes each
  of its steps through exactly these functions, so `noninterference` (with an
  adaptive receiver and any encoder) is also a statement about runs of this code.

What stays trusted: the wrapper (identity, turn order, I/O), the Lean compiler and
runtime, and the operating system.
-/

namespace VeriSwarm

/-- A request reaching the core, after the wrapper has checked identity and turn. -/
inductive Req (P : Type) where
  | call (p : P) (a : Action)
  | maint

/-- What the core sends back to the caller: the response and the caller's job count. -/
structure Reply where
  resp : Resp
  jobs : Nat
  deriving DecidableEq, Repr

variable {P : Type} [DecidableEq P]

/-- The core's answer to one call: the new store, and the reply for the caller. -/
def serveCall (s : Store P) (p : P) (a : Action) : Store P × Reply :=
  (update s p (nextCell a (s p)), ⟨respond a (s p), (nextCell a (s p)).jobs⟩)

/-- Trusted maintenance: every hold is released. -/
def serveMaint (s : Store P) : Store P := fun q => flush (s q)

/-- The replies the core sends to `r` over a request stream, each tagged with its position. -/
def repliesFrom (r : P) : Nat → List (Req P) → Store P → List (Nat × Reply)
  | _, [], _ => []
  | n, .maint :: xs, s => repliesFrom r (n + 1) xs (serveMaint s)
  | n, .call p a :: xs, s =>
      if p = r then (n, (serveCall s p a).2) :: repliesFrom r (n + 1) xs (serveCall s p a).1
      else repliesFrom r (n + 1) xs (serveCall s p a).1

/--
Two request streams look the same to `r` when, position by position, both are
maintenance, or both are the same call by `r`, or both are calls by principals other
than `r`, which may be different principals asking for different things.
-/
inductive SameFor (r : P) : List (Req P) → List (Req P) → Prop where
  | nil : SameFor r [] []
  | maint {xs ys : List (Req P)} : SameFor r xs ys → SameFor r (.maint :: xs) (.maint :: ys)
  | own {xs ys : List (Req P)} (a : Action) : SameFor r xs ys → SameFor r (.call r a :: xs) (.call r a :: ys)
  | other {xs ys : List (Req P)} {p p' : P} (a a' : Action) (hp : p ≠ r) (hp' : p' ≠ r) :
      SameFor r xs ys → SameFor r (.call p a :: xs) (.call p' a' :: ys)

/-- From stores that agree on `r`'s cell, streams that look the same to `r` give `r` the same replies. -/
theorem core_view_independent (r : P) {xs ys : List (Req P)} (h : SameFor r xs ys) :
    ∀ (n : Nat) (s t : Store P), s r = t r → repliesFrom r n xs s = repliesFrom r n ys t := by
  induction h with
  | nil =>
    intro n s t _
    rfl
  | maint _ ih =>
    intro n s t hs
    simp only [repliesFrom]
    apply ih
    simp only [serveMaint, hs]
  | own a _ ih =>
    intro n s t hs
    simp only [repliesFrom, serveCall, ↓reduceIte, hs]
    congr 1
    apply ih
    simp only [update_self]
  | other a a' hp hp' _ ih =>
    intro n s t hs
    simp only [repliesFrom, hp, hp', ↓reduceIte]
    apply ih
    simp only [serveCall]
    rw [local_respect _ _ hp, local_respect _ _ hp']
    exact hs

/--
**Noninterference for the deployed core.** Started clean, the core sends the receiver
the same replies, at the same positions, over any two request streams that differ only
in what other principals ask for.
-/
theorem core_noninterference (r : P) {xs ys : List (Req P)} (h : SameFor r xs ys) :
    repliesFrom r 0 xs cleanStore = repliesFrom r 0 ys cleanStore :=
  core_view_independent r h 0 cleanStore cleanStore rfl

/-- A receiver's step in the abstract model is exactly the core's `serveCall`. -/
theorem runFrom_act_self (r : P) (recv : List Event → Action) (others : Nat → P → Action)
    (rest : List (Slot P)) (n : Nat) (s : Store P) (h : List Event) :
    runFrom r recv others (.act r :: rest) n s h =
      runFrom r recv others rest (n + 1) (serveCall s r (recv h)).1
        (h ++ [⟨n, recv h, (serveCall s r (recv h)).2.resp, (serveCall s r (recv h)).2.jobs⟩]) := by
  simp [runFrom, serveCall]

/-- Another principal's step in the abstract model is exactly the core's `serveCall`. -/
theorem runFrom_act_other (r : P) (recv : List Event → Action) (others : Nat → P → Action)
    (p : P) (hp : p ≠ r) (rest : List (Slot P)) (n : Nat) (s : Store P) (h : List Event) :
    runFrom r recv others (.act p :: rest) n s h =
      runFrom r recv others rest (n + 1) (serveCall s p (others n p)).1 h := by
  simp [runFrom, serveCall, hp]

/-- A maintenance step in the abstract model is exactly the core's `serveMaint`. -/
theorem runFrom_maint (r : P) (recv : List Event → Action) (others : Nat → P → Action)
    (rest : List (Slot P)) (n : Nat) (s : Store P) (h : List Event) :
    runFrom r recv others (.maint :: rest) n s h =
      runFrom r recv others rest (n + 1) (serveMaint s) h := by
  simp only [runFrom]
  rfl

/-- Non-vacuity: the core really does answer another principal's hold, and the receiver still runs its job. -/
theorem core_receiver_completes_job :
    repliesFrom (P := Nat) 1 0 [.call 0 .acquire, .call 1 .tryJob, .maint] cleanStore = [(1, ⟨.ok, 1⟩)] := by
  decide

/-- Non-vacuity: with one shared cell instead, the same requests would answer busy. -/
theorem shared_cell_would_answer_busy :
    respond .tryJob (nextCell .acquire ⟨false, 0⟩) = .busy := by
  decide

end VeriSwarm
