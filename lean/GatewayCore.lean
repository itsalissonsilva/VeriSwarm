import VeriSwarm.Core

/-!
The VeriSwarm gateway's decision core, as a program.

It keeps the lock state in a `Store String` and answers each request with the functions
`serveCall` and `serveMaint` from `VeriSwarm/Core.lean`, which `core_noninterference`
is proved about. It reads one request per line on standard input and writes one reply
per line on standard output:

  call <principal> <action>   ->  <response> <jobs>
  maint                       ->  done
  reset                       ->  done
  quit                        ->  (exits)

Actions: noop, acquire, release, try_job. Responses: noop, ok, busy, not_owner.
Identity and turn order are checked by the wrapper before a request reaches this program.
-/

open VeriSwarm

def parseAction : String → Option Action
  | "noop" => some .noop
  | "acquire" => some .acquire
  | "release" => some .release
  | "try_job" => some .tryJob
  | _ => none

def respName : Resp → String
  | .noop => "noop"
  | .ok => "ok"
  | .busy => "busy"
  | .notOwner => "not_owner"

def clean : Store String := cleanStore

partial def loop (stdin stdout : IO.FS.Stream) (s : Store String) : IO Unit := do
  let line ← stdin.getLine
  if line.isEmpty then return
  -- A byte-order mark some shells prepend is not part of a request.
  let text := (line.dropWhile (· == '﻿')).trimAscii.toString
  let words := (text.splitOn " ").filter (· ≠ "")
  let reply (text : String) : IO Unit := do
    stdout.putStrLn text
    stdout.flush
  match words with
  | ["call", p, a] =>
    match parseAction a with
    | some act =>
      let (s', out) := serveCall s p act
      reply s!"{respName out.resp} {out.jobs}"
      loop stdin stdout s'
    | none =>
      reply "error unknown_action"
      loop stdin stdout s
  | ["maint"] =>
    reply "done"
    loop stdin stdout (serveMaint s)
  | ["reset"] =>
    reply "done"
    loop stdin stdout clean
  | ["quit"] => return
  | _ =>
    reply "error bad_request"
    loop stdin stdout s

def main : IO Unit := do
  loop (← IO.getStdin) (← IO.getStdout) clean
