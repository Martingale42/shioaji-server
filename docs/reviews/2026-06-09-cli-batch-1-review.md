# Code Review — shioaji-data CLI, Batch 1 (Tasks 1–3, "Relocate into package")

> Reviewer: Code Reviewer · Date: 2026-06-09 · Branch: `main`
> Range reviewed: `94cfefb..edc7be4` (3 commits)
> Design: `docs/plans/2026-06-09-scripts-cli-design.md` · Plan: `docs/plans/2026-06-09-shioaji-data-cli.md`

## Verdict: APPROVED WITH NOTES

The relocation is clean and behaviour-preserving. Both gates are green
(`ruff` clean, 56 tests pass). All hard parity requirements are met: the
two math docstrings are byte-identical to the pre-move source, internal
`data/` imports are relative, every external importer is repointed, no
`from scripts.<moved>` import survives anywhere in the tree, and the
maintenance + grep-guard tests are green. The notes below are minor
(one stale docstring `python -m` example, one documentable edge in the
`partial`/`last_date=None` mapping). None block the batch.

---

## Commits in scope

| SHA | Task | Subject |
|---|---|---|
| `65abcd2` | 1 | scaffold `shioaji_server.data`, move catalog inspect into it |
| `acf11c2` | 2 | relocate client/bars/instruments into `shioaji_server.data`, repoint importers |
| `edc7be4` | 3 | consolidate fetch_single/fetch_single_ticks into `data/fetch.py` callables |

Change surface (`git diff 94cfefb..HEAD --stat`): 10 files, +146/−183. Net
deletion is expected — two argparse `main()`/`parse_args()` bodies were
dropped and two fetchers folded into one module.

---

## Findings

### Critical

None.

### Important

None.

### Minor

**M1 — Stale `python -m scripts.inspect_catalog` example in moved module docstring.**
`[src/shioaji_server/data/inspect.py:6]`
```
    uv run python -m scripts.inspect_catalog --catalog-path ./catalog
```
The module moved to `shioaji_server.data.inspect` and `scripts/inspect_catalog.py`
no longer exists, so this usage line is now dead. It is harmless (the file
still carries its own `main()`/argparse, removed in Task 5 per plan), but the
invocation it advertises is gone. This is the only remaining textual reference
to a moved-away path anywhere in the `.py` tree (verified by grep). Recommend
deleting or updating the docstring example when Task 5 strips the leftover
`main()` from `inspect.py`. Not a blocker — `inspect.py` has no importers and
the CLI front-end (Task 5) supersedes this entry point.

**M2 — `partial` with `last_date=None` on pre-flight quota stop.**
`[src/shioaji_server/data/fetch.py:204-205]`
```python
if not await check_quota(client, min_remaining_mb):
    return TickerResult(code=code, status="partial")
```
When the *pre-flight* quota check fails, zero days were processed, so
`last_date` is correctly `None`. This is behaviourally right (resume must
start from the original `--start`, not `last_date+1`). However the design's
status contract (§4.3 / §6) phrases `partial` as "quota/empty stop **with
last_date**", and the method docstring (L196–198) likewise promises a
last completed date. The Task 4 `format_batch_report` must therefore handle
`status="partial" and last_date is None` (emit the resume hint with the
original `--start`, not `None+1day`). Flagging so the Batch 2 reviewer
confirms that branch is covered. Not a parity break — the original `main()`
simply `return`ed here with no status at all; the new mapping is a faithful,
defensible promotion.

---

## Behaviour-parity audit (vs pre-move `scripts/fetch_single*.py` @ 94cfefb)

Parity is the whole point of this batch. Verified item by item:

| Behaviour | Pre-move source | `data/fetch.py` | Parity |
|---|---|---|---|
| Math docstring `_ns_to_trade_id` | present | byte-identical (`diff` empty) | ✅ verbatim |
| Math docstring `ticks_to_trade_ticks` | present | byte-identical (`diff` empty) | ✅ verbatim |
| `volume<=0 or close<=0` tick filter | `fetch_single_ticks` L86 | L260 | ✅ identical |
| Pre-flight quota stop | `main()` `return` | `return partial` | ✅ (status added) |
| Periodic quota stop (`% USAGE_CHECK_INTERVAL`) | `break` | `stopped_early → partial` | ✅ |
| `MAX_CONSECUTIVE_ERRORS` stop | `break` | `stopped_early → partial` | ✅ |
| `MAX_CONSECUTIVE_EMPTY` stop | `break` | `stopped_early → partial` | ✅ |
| `--start` resume (`last_date` returned) | `(total, last_date)` tuple | `TickerResult.last_date` | ✅ |
| Instrument-def side-write before fetch | `catalog.write_data([inst])` | L213-214 (ticks), L173 (bars) | ✅ |
| Tick sort by `ts_init` before write | L91 | L265 | ✅ |
| Final quota report `check_quota(client, 0)` | present | L286 | ✅ |
| All progress/diagnostic `print(...)` lines | present | preserved verbatim | ✅ |
| Bars: probe → def → `fetch_stock_bars` | `fetch_single.main` | `fetch_bars_one` L158+ | ✅ |

Note on the `print()` lines: the global "no print for results" rule targets
research/analysis code; these are operational CLI progress diagnostics and
the explicit batch mandate is parity, so preserving them verbatim is correct.

`TickerResult` status semantics confirmed against the design control flow:
`no_data` on probe-false (L211), `partial` on quota/empty/error stop with
`last_date` (L288-291), `complete` on reaching end (L288), `failed` reserved
for the Task 4 `run_batch` exception mapping. Mapping matches the original
flow.

---

## API conformance

| Public name | Design spec | Implementation | Match |
|---|---|---|---|
| `TickerResult(code, status, n_written=0, last_date=None, error=None)` | §4.4 / Task 3 L102-109 | L42-48 | ✅ |
| `write_instrument_def_one(client_url, code, catalog)` | Task 3 L114 | L149-151 | ✅ |
| `fetch_bars_one(client, gateway_url, code, start, end, catalog)` | Task 3 L117 | L158-165 | ✅ |
| `fetch_ticks_one(client, gateway_url, code, start, end, catalog, min_remaining_mb)` | Task 3 L120 | L185-193 | ✅ |
| `_ns_to_trade_id`, `ticks_to_trade_ticks`, `trading_days`, `_tick_type_to_aggressor`, `check_quota` | §4.4 helpers, moved verbatim | present | ✅ |

> Note: design doc §4.4 (L107) shows `write_instrument_def_one(code, catalog,
> gateway_url)` — a different arg order than Task 3 (L114). The implementation
> follows the **Task 3** ordering (`client_url, code, catalog`), which is the
> authoritative task spec. Internally consistent; flagging only so the CLI
> layer (Task 5) wires it with the right positional order.

---

## Relocation hygiene

- **No leftover `from scripts.<moved>` imports** — grep of the whole tree for
  `from scripts.(client|bars|instruments|fetch_single|fetch_single_ticks|inspect_catalog)`
  returns nothing.
- **`data/` internal imports are relative** — `bars.py` → `from .client`,
  `fetch.py` → `from .bars`, `from .client`, `from .instruments`. No absolute
  self-import (the one `from shioaji_server.data.instruments` hit is a docstring
  usage example in `instruments.py`, correctly updated).
- **External importers repointed** —
  `scripts/maintenance/regen_catalog_instruments.py` (import + docstring),
  `tests/test_trade_id.py`, `tests/test_instrument_provider_path.py` all on
  `shioaji_server.data.*`.
- **`git mv` rename detection** — `client.py` shows `R100` (pure move);
  `bars.py`/`instruments.py` near-pure (1-line relative-import / docstring edit).
  Clean history.
- **Deleted files gone** — `scripts/{fetch_single,fetch_single_ticks,inspect_catalog,
  client,bars,instruments}.py` all removed; `scripts/__init__.py` is the only
  top-level `.py` left.

## Tests

- **Grep-guard** (`test_legacy_hardcoded_builders_are_gone`) now reads
  `src/shioaji_server/data/fetch.py`, asserts no `make_equity`/`contract_to_equity`,
  and asserts `from .instruments import load_instrument` (relative form). Invariant
  preserved. ✅
- **Maintenance tests** (`test_regen_catalog_instruments`, `test_restamp_metadata`)
  green — the one import repoint did not disturb them. ✅
- **`test_trade_id`** repointed to `shioaji_server.data.fetch`, green. ✅
- Batch/CLI tests (`test_batch_fetch`, `test_data_cli`) are Tasks 4/5 scope —
  not expected this batch.

---

## Verification results

```
$ uv run ruff check .
All checks passed!

$ uv run pytest -q
........................................................                 [100%]
56 passed, 1 warning in 1.29s
```

Targeted re-run (parity-critical subset):
```
$ uv run pytest tests/test_regen_catalog_instruments.py tests/test_restamp_metadata.py \
    tests/test_instrument_provider_path.py::test_legacy_hardcoded_builders_are_gone \
    tests/test_trade_id.py -q
13 passed in 0.79s
```

Docstring parity (byte-level `diff` vs `94cfefb`):
```
_ns_to_trade_id:      IDENTICAL
ticks_to_trade_ticks: IDENTICAL
```

Design verification command (Task 3 L138):
```
$ uv run python -c "from shioaji_server.data import fetch; print([n for n in dir(fetch) if n.endswith('_one')])"
['fetch_bars_one', 'fetch_ticks_one', 'write_instrument_def_one']
```

---

## Recommendation

Proceed to Batch 2 (Tasks 4–5). Carry forward two cleanup items for the
next touch of these files (neither blocks the batch):

1. **M1** — fix/remove the stale `python -m scripts.inspect_catalog` docstring
   example in `inspect.py` when Task 5 strips its `main()`.
2. **M2** — ensure Task 4 `format_batch_report` handles `partial` +
   `last_date=None` (pre-flight quota stop) by emitting the resume hint against
   the original `--start`.
