# State machine fixes

Branch: `claude/implicit-state-machines-ig36w5`. One commit per fix. `main` is untouched.
Backend tests: `cd backend && python -m pytest -q tests/`. **303 passed** at the last commit, using an in-memory Firestore fake (`tests/fake_firestore.py`). No test touches a real database.

| # | Commit | Area |
|---|--------|------|
| 0 | `1d39031` | Test infra: Firestore fake, `run_transaction` helper |
| 1 | `fe3f3e8` | Job lock |
| 2 | `b6b1a18` | SystemState transition/permission tables |
| 3 | `8f00799` | Atomic `transition()`, no cache, checked call sites |
| 4 | `3ad3254` | Signal gating |
| 5 | `b63a95b` | Kill switch / trading mode |
| 6 | `0a1501a` | Model lifecycle + promotion + forward cycle |
| 7 | `e93a9b5` | Forward prediction resolver |

None of the fixes was reverted. Each one's tests passed on the first full run.

---

## Flags you must review and switch to `enforce`

All three default to `log_only`. Unknown values fall back to `log_only` with a warning.

| Env var | log_only (default) | enforce | Log line to grep before switching |
|---|---|---|---|
| `SIGNAL_STATE_GATE` | Never blocks signals | Returns `system_not_ready` when `can_run_service(symbol, "signal_generation")` is false. A state-read error fails closed | `SIGNAL_STATE_GATE log_only: WOULD HAVE BLOCKED` |
| `KILLSWITCH_AUTO` | Automatic kill conditions only log. `can_trade`'s max-drawdown rejection is also log-only | Drawdown, consecutive losses, daily loss, API/data error and model degradation trigger the kill switch | `KILLSWITCH_AUTO log_only: WOULD HAVE KILLED` / `WOULD HAVE REJECTED` |
| `FIRST_MODEL_GATE` | First model per symbol is still auto-promoted, now marked `provisional: true` with the gate verdict recorded | First model must pass the gates. If not, the symbol waits in `FORWARD_TESTING`/`REJECTED` with no signals until the forward cycle promotes it | `FIRST_MODEL_GATE log_only: promoting first model` |

Always enforced, no flag: the **manual** kill switch (admin API) and the kill-switch **hard block** on `GET /api/signals/{symbol}`.

Other new knobs: `MIN_FORWARD_SAMPLES` (default 20), the resolved predictions a model needs before its forward metrics are written.

### Ops steps you need to do
1. **Add a Cloud Scheduler job: `POST /api/training/forward-cycle`, hourly.** Without it, predictions never resolve, challengers never get re-evaluated, and paper equity never moves.
2. Leave the three flags at `log_only` for at least a few days of forward-cycle runs. Read the "WOULD HAVE" logs, then switch them one at a time.
3. `backend/scripts/reset_job_retries.py` resets the `job_retries` docs. It does a dry run unless you pass `--apply`. **It has not been run against any real database.**

---

## Fix 1: Job lock (`app/core/job_lock.py`)

**What changed**
- `release(lock, JobOutcome)`: `SUCCESS` resets the retry count, `SKIPPED` leaves it alone, `FAILED` adds 1. Training maps `completed` to SUCCESS, `skipped` (invalid state, insufficient data, rejected transition) to SKIPPED, and everything else to FAILED. This fixes the permanent lockout from 3 scheduled skips.
- Acquire (including stale takeover and the retry check) is one Firestore transaction.
- release and heartbeat check `job_id` ownership. A job that lost its lock can't clobber the new owner, and a lost heartbeat aborts that symbol's training.
- `force=True` bypasses an exhausted retry count, never a live lock. The denial reason is now accurate: `max_retries_exceeded` vs `locked_by_another_job`.
- A malformed lock doc is treated as stale. A double release is a no-op.

**Still risky**
- A stale lock that gets taken over is not counted as a failure of the orphaned job (unchanged behaviour).
- `POST /api/training/trigger` is unauthenticated, so anyone can send `force=True` and bypass the retry limit and the state checks. That was true before and is out of scope here, but fix it.

## Fix 2: SystemState tables (`app/core/system_state.py`)

**What changed**
- Training is permitted in `READY_FOR_TRAINING`, `TRAINING_FAILED`, `FORWARD_TESTING`, `REJECTED`, `PROMOTED`, `LIVE_DEGRADED` and legacy `CHALLENGER_READY`. A test enforces that every training-permitted state has `TRAINING` as a valid transition. Scheduled retrain no longer needs `force`.
- `TRAINING` can now go to `COLLECTING_DATA` (the insufficient-data path). If a champion is serving, it returns to the serving state instead.
- A non-promoted challenger with a live champion returns the symbol to `PROMOTED`, or to `LIVE_DEGRADED` if it was degraded (sticky via `metadata.resume_state`). It no longer goes to a dead-end `CHALLENGER_READY`.
- Dead states:
  - `FORWARD_TESTING` is **wired**: no champion and the challenger is pending.
  - `REJECTED` is **wired**: no champion and the challenger failed the gates.
  - `KILLED` is **deprecated**: the capital kill switch is the single kill authority.
  - `CHALLENGER_READY` is **deprecated**.
  - The deprecated states keep their exits so existing Firestore docs can recover. Nothing enters them.
- The hardcoded `ADMIN_FORCE_KEY` is gone. `force=True` uses `override()`, which logs at WARNING and writes the audit log. `force_state()` checks `ADMIN_SECRET_KEY` from the environment.
- `signal_generation` is permitted in every state except `NO_DATA`, `COLLECTING_DATA` and `KILLED`, so enforcing the gate never silences a champion during an ordinary retrain.

**Still risky**
- Because the permission is so broad, `SIGNAL_STATE_GATE=enforce` blocks little. Whether a champion exists is still decided by the registry.
- Prod docs currently in `CHALLENGER_READY` stay there until the next training run, which is now allowed from that state.

## Fix 3: Atomic `transition()` (`app/core/system_state.py`)

**What changed**
- Read, check, write and the audit row happen in one Firestore transaction, validated against the stored state.
- The 30s per-process cache is removed, so every `get_state` reads Firestore. Initialising a missing doc is transactional and can't clobber a concurrent first transition.
- Every call site checks the return value:
  - training start: skips the symbol (`state_transition_rejected`, no retry burned);
  - training end: logs `STATE TRANSITION REJECTED`;
  - data_store and demotion: log.

**Still risky**
- Real Firestore contention and retries are untested. The fake serialises transactions.
- More Firestore reads: one per `get_state` / `can_run_service`, including one per signal request.

## Fix 4: Signal gating (`app/api/signals.py`)

**What changed**
- Kill-switch hard block before the generator runs. It returns the existing `no_trade` shape with `rejection_reason: "kill_switch_active"`. Prediction and indicators are still included, and forward logging still happens.
- State gate behind `SIGNAL_STATE_GATE` (see the flag table).
- Correction to my audit: signals already reached the kill switch via generator filter 5, but only if the earlier filters passed.

**Still risky**
- Nothing new beyond the flags above.

## Fix 5: Kill switch (`app/capital/killswitch.py`, `app/capital/controller.py`)

**What changed**
- One state, `KillSwitchState`: `active` / `forced_flat` / `killed`. It is stored in `system_state/capital_controller.trading_mode`.
- `apply_mode_event()` is the only writer. It runs in a transaction and writes an audit row to `capital_mode_transitions`. Duplicate events are idempotent. Flat on/off while killed raises.
- `is_killed`, `is_forced_flat` and `KillSwitch.is_active` are read-only views now. `THROTTLED` and `BASELINE_ONLY` were dropped as unused.
- Kill, then reset, resumes forced-flat if the controller was forced-flat before the kill.
- **Bug: admin and signals had separate controllers.** An admin kill never reached signals until a restart. Both now share `get_capital_controller()`, and the mode is read from Firestore on every check, so the kill applies across instances.
- **Bug: an equity save wrote `is_killed` from memory**, so another instance could un-kill trading. Equity saves now write only equity fields.
- Forced-flat is persisted. `trades_today`/`rejections_today` reset on a new UTC day via `last_reset`.
- `update_equity`, `add_position`, `close_position` and `check_and_activate` are wired through `record_closed_trade()`. The forward cycle calls it for each resolved, served, sized prediction. There is no live execution in this system, so these are paper trades. Equity is added transactionally.

**Still risky**
- **Not behind a flag:** paper equity is now real, so `drawdown_pct` moves and `PositionSizer` shrinks `position_size` on drawdown (down to 0.2× at >15%). This is live as soon as the forward cycle runs.
- Starting equity is the default 100000. Paper PnL depends on `position_size` values the API returns, not on real fills.
- If `record_closed_trade` fails after a prediction resolved, that PnL is lost. It is logged and not retried.
- `trades_today`/`rejections_today` are per-instance memory.
- On a mode-read failure the last known mode is used. That is fail-static, not fail-closed. This is deliberate, but know it.

## Fix 6: Model lifecycle and promotion (`app/models/registry/*`, `app/evaluation/forward_cycle.py`)

**What changed**
- Model status table: `registered → pending|rejected|champion|retired`, `pending → pending|rejected|champion|retired`, `rejected → retired`, `champion → retired`.
  - Retired models record `retired_reason`: superseded, demoted or manual.
  - Promoting a retired or rejected model needs `override=True`. It is logged as `ADMIN OVERRIDE` and audited in `model_transitions`.
  - `POST /api/admin/promote` takes an optional `override` field and returns 400 without it.
- `_find_rollback_target` returns only former champions retired as **superseded**, most recent first. Before, it picked challengers that had failed promotion. The `sharpe_ratio > 0` filter was dropped, because the stored validation metrics have no Sharpe.
- **Bug: registry clobbering.** Each instance wrote its whole in-memory registry. Every mutation is now a transaction on the current doc.
- **Bug: a later promotion kept serving the old cached model.** The cache is refreshed on every promotion. If the load fails, the old model is kept and an error is logged.
- PENDING challengers:
  - The signals endpoint logs **shadow** predictions for them. These are never served, have no size and no equity effect, are hidden from history, and their errors are swallowed.
  - `POST /api/training/forward-cycle` resolves outcomes, updates forward metrics (≥ `MIN_FORWARD_SAMPLES`), re-evaluates pending models using the baseline Sharpe values stored on each model, and syncs SystemState.
- First-model auto-promotion now goes through the same evaluation, then `FIRST_MODEL_GATE` decides (see the flag table). A completed forward test now substitutes for multi-window validation. Without that, a first model could never pass.

**Still risky**
- **The signals process's registry is an in-memory snapshot from start-up.** It doesn't see promotions made on other instances until a restart, and the model cache is per-process. This existed before and is not fixed.
- Model files live on ephemeral disk (`runs/`). Shadow loads and cache refreshes fail on instances that didn't train the model. They fail safely, with logs, but forward data for challengers then only accumulates on the training instance.
- Shadow predictions add a model load and predict per pending challenger to each signal request.
- `ModelDemotion` is still never called on a schedule. Champion forward metrics are now updated, but nothing acts on degradation.
- The registry is one Firestore doc (1 MB limit), and every mutation contends on it.
- `jobs/retrain.py` still bypasses the job lock and SystemState.
- Legacy prod models in `registered` status are ambiguous (old rejected challengers). They are never re-evaluated and never rollback targets.
- `/forward-cycle` is unauthenticated, like the other training endpoints. Its lock ignores the retry limit so the hourly job can't lock itself out.

## Fix 7: Forward predictions (`app/evaluation/forward_engine.py`)

**What changed**
- `status` is `pending` / `resolved` / `expired` and stored in Firestore. Expiry happens 7 days after maturity with no price. `is_resolved` is kept as a mirror for the existing index.
- `resolve_pending()` reads Firestore, not per-process memory. Each prediction transitions in its own transaction, so resolution is exactly-once and idempotent.
- The exit price is the close of the 1h Binance candle at maturity, not "now".
- Metrics come from Firestore. `/api/signals/forward/metrics` now returns real data. `no_trade` is scored like hold. tz-aware timestamps are handled.

**Still risky**
- If any pending predictions are already in prod, the first forward-cycle run resolves the whole backlog. That's correct, since it uses maturity prices, but it is many Binance kline calls. Older backlog that Binance can't price will expire after 7 days.
- `get_forward_metrics(model_version)` scans all predictions of that version, with no time index on that query.

---

## Out of scope tonight (as instructed)
- Frontend state machines (WebSocket reconnect, Settings page, auth store).
- Pure refactors: `_training_status` global dict (finding #6), `TrainingRun.status` strings (#7).
- Auth on training/admin trigger endpoints.
