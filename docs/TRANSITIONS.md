# Exact transition semantics

This is the table that `specs/Semantics.tla` and `src/veriswarm/semantics.py` each implement separately, and that `tests/test_semantics.py` states a third time as literal values.

## One slot

The acting principal comes from the public schedule, never from the request. "Own lock" means the backing lock the variant assigns to that principal.

| Action | Own lock free | Held by self | Held by another | Lock changes | Audit event |
|---|---|---|---|---|---|
| `noop` | `noop` | `noop` | `noop` | no | none: not a tool call |
| `acquire` | `ok` | `busy` | `busy` | taken on `ok` | yes |
| `release` | `not_owner` | `ok` | `not_owner` | released on `ok` | yes |
| `spoof_release` | `not_owner` | `ok` | `not_owner` | released on `ok` | yes, `claimed = forged` |
| `try_job` | `ok`, job runs | `busy` | `busy` | no: atomic, released within the slot | yes |
| `invalid` | `invalid` | `invalid` | `invalid` | no | yes |

**Order of the checks:** `noop`, then `invalid`, then denial, then the action.

**Denial (V3).** A receiver's `acquire`, `release`, `spoof_release` and `try_job` return `denied` with no state change. `noop` and `invalid` are unaffected.

**Quota (V5).** `acquire` and `try_job` also require that no backing lock is held by anyone. Otherwise `busy`, even if the principal's own lock is free.

**Noise (V8).** A receiver's `try_job` with the coin set returns `busy` regardless of lock state. In the product the coin is shared between the two copies. In the simulator it is true with probability `p`.

**Forged identity at the gateway.** A `release` with a `claimed_actor` different from the handle's principal becomes `spoof_release`. Any other request with a forged claim becomes `invalid`. So does an unknown tool name, or a direct request for `spoof_release`.

## Maintenance slot (`M`)

Releases every lock. It is not a tool call, produces no audit event and no receiver event, and advances the clock by one.

## Bookkeeping after each slot

| Field | Rule |
|---|---|
| `pc` | Next slot, wrapping to 1 at the end of the schedule |
| `round` | +1 on wrap, in bounded mode |
| `clock` | +1, except V7 when a sender plays `noop`. Fixed at 0 in unbounded mode. |
| `aseq` | +1 on any tool call, when tracked |
| `jobs[p]` | +1 when a job ran, in bounded mode |
| `requests[p]` | +1 on `try_job`, including failed and denied ones, in bounded mode |
| `lastAct[p]`, `lastResp[p]` | This slot's action and response |
| `obs`, `lastObs` | Receiver slots only; see ObsB below |
| `audit` | Appended on a tool call when audit tracking is on |

The run is terminal when `round > MaxRounds`, and terminal states stutter explicitly. So TLC's deadlock check stays on and would still catch a genuinely stuck state.

## ObsB: what a receiver observes

`who, round, slot, clock, act, resp, job, seq`

- `job` is the receiver's own synthetic output, or 0.
- `seq` is always 0, except in V6, where exposing it is the injected defect.
- `resp_only` mode zeroes everything except `who` and `resp`. It exists only to show that weakening the observation hides V6 and V7.

**Excluded, and not solved:** sender actions and identity, other principals' logs, gateway sequence numbers (except V6), wall-clock duration, CPU and cache behaviour, memory pressure, network latency, host process state, content steganography in legitimate outputs, and anything outside the gateway.

## Variants

| ID | Backing | Object name in audit | Identity recorded | Other |
|---|---|---|---|---|
| V0 | one | `unrecorded` | no | |
| V1 | one | `lock` | yes | |
| V2 | per principal | `lock_<p>` | yes | |
| V3 | one | `lock` | yes | receiver denied |
| V4 | one | `lock_<p>` | yes | names lie about the backing |
| V5 | per principal | `lock_<p>` | yes | global capacity of one |
| V6 | per principal | `lock_<p>` | yes | `seq` in the receiver's reply |
| V7 | per principal | `lock_<p>` | yes | sender no-op yields its slot |
| V8 | one | `lock` | yes | random `busy` to the receiver |
| V9 | one | `lock` | yes | schedule `AMBM` |
