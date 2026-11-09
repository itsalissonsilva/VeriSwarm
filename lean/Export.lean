import VeriSwarm

/-!
Prints the Lean model's receiver responses for every pair of action sequences on a
few schedules, as JSON lines. tests/test_lean_differential.py compares these with
the Python semantics, so the three implementations are tied together on this
finite set. Run with `lake env lean --run Export.lean`.
-/

open VeriSwarm

def actionName : Action → String
  | .noop => "noop"
  | .acquire => "acquire"
  | .release => "release"
  | .tryJob => "try_job"

def respName : Resp → String
  | .noop => "noop"
  | .ok => "ok"
  | .busy => "busy"
  | .notOwner => "not_owner"

def allActions : List Action := [.noop, .acquire, .release, .tryJob]
def receiverActions : List Action := [.noop, .tryJob]

/-- All lists of length n over the given alphabet. -/
def sequences (alphabet : List Action) : Nat → List (List Action)
  | 0 => [[]]
  | n + 1 => alphabet.flatMap fun a => (sequences alphabet n).map fun rest => a :: rest

def slotOf : Char → Slot Nat
  | 'A' => .act 0
  | 'B' => .act 1
  | _ => .maint

def jsonList (xs : List String) : String :=
  "[" ++ ", ".intercalate (xs.map fun x => "\"" ++ x ++ "\"") ++ "]"

def emit (scheduleName : String) (roundsCount : Nat) : IO Unit := do
  let round := scheduleName.toList.map slotOf
  let sched := rounds roundsCount round
  let countSlots (c : Char) := roundsCount * (scheduleName.toList.filter (· == c)).length
  for ua in sequences allActions (countSlots 'A') do
    for ub in sequences receiverActions (countSlots 'B') do
      -- The sender's i-th slot uses ua[i], found by counting earlier A slots.
      let others : Nat → Nat → Action := fun n _ =>
        let earlier := ((sched.take n).filter fun
          | .act 0 => true
          | _ => false).length
        ua.getD earlier .noop
      -- The receiver is scripted through its own history length.
      let recv : List Event → Action := fun h => ub.getD h.length .noop
      let (_, hist) := runFrom (P := Nat) 1 recv others sched 0 cleanStore []
      let resps := hist.map fun e => respName e.resp
      IO.println s!"\{\"schedule\": \"{scheduleName}\", \"rounds\": {roundsCount}, \"ua\": {jsonList (ua.map actionName)}, \"ub\": {jsonList (ub.map actionName)}, \"resps\": {jsonList resps}}"

def main : IO Unit := do
  emit "ABM" 1
  emit "ABM" 2
  emit "ABABM" 1
  emit "AB" 2
  emit "BAM" 2
