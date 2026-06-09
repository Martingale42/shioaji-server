# Code Review — shioaji-data CLI, Batch 2 (Task 4, "Batch + parallel engine")

> Reviewer: Code Reviewer · Date: 2026-06-09 · Branch: `main`
> Commit reviewed: `de0fdb0` (1 commit) — `db30424..de0fdb0`
> Design: `docs/plans/2026-06-09-scripts-cli-design.md` · Plan: `docs/plans/2026-06-09-shioaji-data-cli.md`

## Verdict: APPROVED

The batch/quota engine matches the design and is correct. Both gates are
green (`ruff` clean, 61 tests pass, 5 new). The two subtle correctness
properties this batch hinges on — the **lock-free `QuotaGate` throttle**
and the **`run_batch` concurrency bound + failure isolation** — both hold
and are locked by meaningful tests that track the right quantities (a live
in-flight counter, an actual `get_usage` call count). The await-race
question on `QuotaGate.ok()` resolves in favour of the implementation: the
read-decide-write of the throttle slot is fully synchronous, so the
"queried once per TTL" guarantee cannot be broken by two coroutines racing
the stale check. No blocking findings. Notes below are Minor.

---

## Change surface

`git diff db30424..de0fdb0 --stat`: 2 files, +385/−13.

| File | Δ | What |
|---|---|---|
| `src/shioaji_server/data/fetch.py` | +201/−13 | `QuotaGate`, `run_batch`, `format_batch_report`; `fetch_ticks_one` signature `min_remaining_mb` → `gate: QuotaGate`; drop `USAGE_CHECK_INTERVAL` periodic-poll branch |
| `tests/test_batch_fetch.py` | +197 (new) | 5 tests: 2× `run_batch`, 2× `QuotaGate`, 1× `format_batch_report` carry-forward |

---

## Batch-2-specific correctness checks

### QuotaGate — wall-clock ban: PASS
`grep -nE 'time\.time|datetime\.now|time\.monotonic|datetime\.utcnow|perf_counter'`
over `fetch.py` returns **zero** matches. The only clock source is the
event-loop clock: `asyncio.get_event_loop()` + `loop.time()`
(`fetch.py:203-204`), exactly as the plan mandates. Deterministic under the
test's `FakeClock`; immune to system clock changes.

### QuotaGate — `tripped` latches: PASS
`ok()` short-circuits `if self._tripped: return False` *before* any poll
(`fetch.py:200-201`). Once `_tripped` is set (`fetch.py:217`) it is never
cleared anywhere in the class — no reset path exists. The test
`test_quota_gate_trips_and_stays` asserts the latch *and* that no further
poll happens after tripping: it advances the clock to `t=1000` (far past
the 10s TTL, so a re-query *would* fire if not latched), calls `ok()` twice
more, and asserts `client.calls == 1`. This is the strong form of the
"trips and stays" property, not just `tripped is True`.

### QuotaGate — throttle "queried once per TTL": PASS
`test_quota_gate_throttles_usage_query` counts real `get_usage` calls via
`FakeUsageClient.calls` and asserts the throttle boundary directly: 25
calls inside a frozen window → `calls == 1`; still inside (`t=9 < ttl=10`)
→ `calls == 1`; cross the boundary (`t=11 > 10`) → `calls == 2`. It tests
the throttle *behaviour*, not merely that the code runs.

### QuotaGate — the await-race window: ANALYSED, NO RACE
This was the flagged subtle risk. Tracing `ok()` (`fetch.py:200-222`):

```
200  if self._tripped: return False        # read _tripped
203  loop = asyncio.get_event_loop()
204  now  = loop.time()
205  stale = self._last_check is None or (now - self._last_check) > self._ttl   # READ + DECIDE
206  if stale:
207      self._last_check = now             # WRITE  ── synchronous, BEFORE any await
208      try:
209          usage = await self._client.get_usage()   # ← the ONLY await in ok()
...
217          self._tripped = True
222  return not self._tripped
```

The stale check (205) and the throttle-slot claim (207) execute with **no
`await` between them** (confirmed by grepping every `await` in `ok()` — the
sole one is line 209). In single-threaded asyncio a coroutine is never
preempted except at an `await`, so the read-decide-write 205→207 is atomic
with respect to other coroutines. Two coroutines therefore cannot both pass
the stale check before one updates `_last_check`: the first runs 205→207
without suspending and only yields at 209; any sibling that runs afterwards
reads the already-bumped `_last_check`, sees `stale=False`, and skips the
poll. **The "queried once per TTL" guarantee is sound; the lock-free claim
in the docstring is justified.** The `_tripped` write at 217 is after the
await, but since only one coroutine ever holds the in-flight poll per
window, this does not affect the throttle or the latch (see Minor 1 for the
one benign consequence).

### run_batch — concurrency bound: PASS
`test_run_batch_respects_concurrency` tracks a **live** in-flight counter
(`live`/`peak`), double-yields (`await asyncio.sleep(0)` twice) inside each
worker to force interleaving, runs 20 codes at `concurrency=3`, and asserts
`peak <= concurrency`. This is the correct form — it would catch a broken
semaphore (peak climbing past N), not just a wrong total count. The bound
is enforced by `asyncio.Semaphore(concurrency)` wrapping `per_ticker`
(`fetch.py:395-398`).

### run_batch — failure isolation + order: PASS
`asyncio.gather(..., return_exceptions=True)` (`fetch.py:400-402`) keeps a
raising ticker from cancelling siblings; the post-loop maps any
`BaseException` → `TickerResult(status="failed", error=str(...))` while
preserving input order via `zip(codes, raw)` (`gather` returns results
positionally). `test_run_batch_isolates_failures` asserts the raising
ticker is `failed` with the right `error`, siblings are `complete`, and
`[r.code for r in results] == codes` (order + cardinality).

### format_batch_report — `last_date=None` carry-forward (M2): PASS
The CRITICAL carry-forward from Batch 1: a pre-flight quota stop yields
`partial` with `last_date=None` (`fetch_ticks_one` line 285-286). The
report branches on `r.last_date is not None` (`fetch.py:455`); the `None`
arm emits `--start <original>`, never a literal `--start None`
(`fetch.py:464`). `test_format_batch_report_handles_none_last_date` locks
this with three assertions: `"--start None" not in report`, the mid-run
case `--code 2330 --start 2024-03-03` (last_date+1), and the pre-flight
case `--code 00631L --start <original>`. The earlier-noted Batch-1 crash
risk is closed.

### Behaviour parity of fetch_ticks_one: PASS
Diffed against `db30424:fetch.py`. The signature changed exactly as the
plan specifies (`min_remaining_mb: float` → `gate: QuotaGate`). Otherwise
semantics are preserved:
- Pre-flight quota check present (`gate.ok()` replaces `check_quota`), still
  returns `partial` on stop.
- Per-day quota check present; the old `i % USAGE_CHECK_INTERVAL` gating is
  removed and replaced by an unconditional `gate.ok()` every day — correct,
  because the gate now self-throttles the actual poll, so calling it every
  day is cheap. The dropped `USAGE_CHECK_INTERVAL` constant is genuinely
  dead after this.
- `no_data` (probe false), `partial`/`complete` (`stopped_early`),
  instrument-def side-write, `volume<=0` filtering, the consecutive-error /
  consecutive-empty stop conditions, and the final `check_quota(client, 0)`
  report line are all byte-equivalent to the pre-batch source.

No surviving caller passes the old `min_remaining_mb` kwarg
(`grep` across `src/`, `tests/`, `scripts/` — `fetch_ticks_one` is defined
once and not yet wired; CLI is Task 5). The signature change is internally
consistent.

---

## Findings

### Critical
None.

### Important
None.

### Minor

1. **Benign one-request over-shoot while a tripping poll is in flight**
   [`src/shioaji_server/data/fetch.py:207-222`]. The throttle slot
   (`_last_check`) is claimed before the `await`, but `_tripped` is set
   only after `get_usage()` returns. So during the single in-flight poll
   that *will* trip the gate, concurrent workers calling `ok()` in the same
   TTL window see `stale=False` and `_tripped` still `False`, get `True`,
   and may launch one more day each. This is at most `concurrency-1` extra
   day-requests at the moment of tripping — bounded, harmless, and
   consistent with the design's "in-flight wind down" intent (the design
   never promised a hard stop, only that no *new tickers/days* launch after
   the trip is observed). Not worth a lock. Noting only so it is a
   documented property, not a latent surprise. No action required.

2. **`get_usage` exception is fully swallowed in `ok()`**
   [`src/shioaji_server/data/fetch.py:218-220`]. Fail-open is intentional
   and matches legacy `check_quota` (a monitoring hiccup must not stall a
   fetch), and the error is surfaced via `print`. One consequence worth
   noting: because `_last_check` is bumped before the `try`, a persistently
   failing usage endpoint is still only polled once per TTL (it does not
   hot-loop) — good. But it also means a genuinely exhausted quota that
   *only* manifests as a usage-endpoint error would never trip the gate;
   the batch would instead lean on the downstream consecutive-empty /
   consecutive-error stops in `fetch_ticks_one`. That is acceptable
   defence-in-depth, not a gap. No action required.

3. **`format_batch_report` tally tolerates unknown status silently**
   [`src/shioaji_server/data/fetch.py:438-440`]. `tally[r.status] =
   tally.get(r.status, 0) + 1` will count an out-of-vocabulary status into
   a key that the summary line never prints, so it would silently vanish
   from the tally total wording (the `(len(results))` count stays correct).
   Statuses are produced only by the four drivers and are a closed set, so
   this is theoretical. If you want it defensive, assert/normalise unknown
   statuses; otherwise leave as-is (YAGNI). No action required.

4. **`asyncio.get_event_loop()` deprecation surface**
   [`src/shioaji_server/data/fetch.py:203`]. `get_event_loop()` is
   soft-deprecated when there is no running loop (3.12+). Here it is always
   called from inside a running coroutine, where it returns the running
   loop without warning — confirmed by running the batch tests under
   `-W error::DeprecationWarning` (clean). The plan explicitly prescribes
   `asyncio.get_event_loop().time()` and the test monkeypatches that exact
   symbol, so this is per-spec. If a future refactor ever calls `ok()`
   outside a loop, prefer `get_running_loop()`. No action required now.

---

## Verification

```
$ uv run ruff check .
All checks passed!

$ uv run pytest -q
.............................................................            [100%]
61 passed, 1 warning in 1.35s
  (the 1 warning is a third-party Pydantic-V2 deprecation in shioaji/contracts.py — pre-existing, not from this batch)

$ uv run pytest tests/test_batch_fetch.py -v
tests/test_batch_fetch.py::test_run_batch_isolates_failures PASSED       [ 20%]
tests/test_batch_fetch.py::test_run_batch_respects_concurrency PASSED    [ 40%]
tests/test_batch_fetch.py::test_quota_gate_trips_and_stays PASSED        [ 60%]
tests/test_batch_fetch.py::test_quota_gate_throttles_usage_query PASSED  [ 80%]
tests/test_batch_fetch.py::test_format_batch_report_handles_none_last_date PASSED [100%]
5 passed in 0.63s

$ uv run pytest tests/test_batch_fetch.py -W error::DeprecationWarning -q
5 passed in 0.62s   (no DeprecationWarning from get_event_loop in-coroutine)

$ grep -nE 'time\.time|datetime\.now|time\.monotonic|datetime\.utcnow|perf_counter' src/shioaji_server/data/fetch.py
(no matches — gate uses event-loop clock only)
```

---

## Summary

Task 4 is correct and per-design. The two correctness-critical properties —
the lock-free per-TTL throttle and the bounded-concurrency failure-isolating
orchestrator — hold under analysis and are guarded by tests that measure the
right quantities. The flagged await-race window does not exist: the throttle
slot is claimed synchronously before the only `await` in `ok()`. The M2
carry-forward (`last_date=None` resume hint) is closed with a dedicated test.
All four Minor notes are documentation-of-behaviour, not defects. **APPROVED.**
