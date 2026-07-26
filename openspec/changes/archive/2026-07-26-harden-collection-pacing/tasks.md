## 1. The pacing module

- [x] 1.1 Add `src/itgraph/tg/pacing.py`. Module-level `secrets.SystemRandom()` as the only random source in the project; nothing else imports `random`.
- [x] 1.2 `request_gap(delay)` — returns `0` for a delay of `0` or less; otherwise, with probability `pacing_long_pause_chance`, a value drawn uniformly from `[pacing_long_pause_min, pacing_long_pause_max]`, and otherwise one drawn uniformly from `[delay × (1 − pacing_jitter), delay × (1 + pacing_jitter)]`. The long pause replaces the band, it is not added to it.
- [x] 1.3 `channel_gap()` — uniform over `[backfill_channel_pause_min, backfill_channel_pause_max]`.
- [x] 1.4 `async pace(delay)` and `async pause_between_channels()` — the awaited seam. Every pacing sleep in the project goes through one of these, and the tests patch these rather than `asyncio.sleep` per module.
- [x] 1.5 Add the row for `tg/pacing.py` to the module map in `src/itgraph/CLAUDE.md`.

## 2. Settings

- [x] 2.1 Add `pacing_jitter: float = 0.5`, `pacing_long_pause_chance: float = 0.02`, `pacing_long_pause_min: float = 20.0`, `pacing_long_pause_max: float = 60.0`.
- [x] 2.2 Add `backfill_channel_pause_min: float = 10.0`, `backfill_channel_pause_max: float = 40.0`.
- [x] 2.3 Add `flood_abort_threshold: float = 1800.0`. Comment why it sits above `flood_sleep_threshold` and below anything that reads as a daily quota.
- [x] 2.4 Add `channel_metadata_max_age_days: int = 30`.
- [x] 2.5 Validate the ranges on load — a min above its max, a jitter outside `[0, 1)`, or a chance outside `[0, 1]` should fail at import, not produce a negative sleep at hour three of a run.

## 3. Randomized pacing in both commands

- [x] 3.1 Replace `asyncio.sleep(delay)` in `backfill_channel`'s window loop with `pace(delay)`.
- [x] 3.2 Replace both `asyncio.sleep(pause)` calls in `resolve_inventory` with `pace(delay)`.
- [x] 3.3 Pace the metadata request too, so no request in `tg/` is issued without a gap before it. This is the call that currently has none.

## 4. The inter-channel pause

- [x] 4.1 Call `pause_between_channels()` in `backfill_channels` after every skip guard and immediately before `backfill_channel` — so a channel skipped for being complete, at its ceiling, or without a username costs nothing.
- [x] 4.2 Skip it before the first channel that does work. It separates channels; there is nothing before the first one.
- [x] 4.3 Leave `backfill_channel` alone. Called on its own it has no predecessor to be separated from.

## 5. The conditional metadata pass

- [x] 5.1 Add a query for the age of a channel's stored payload — `raw_channels.fetched_at`, which already exists. No migration.
- [x] 5.2 In `backfill_channel`, skip `fetch_full_channel` when that payload is younger than `channel_metadata_max_age_days`, and obtain the peer with `client.get_input_entity(username)` instead. Telethon serves this from the session's entity cache; on a miss it resolves the username itself, which is the request the full pass would have made anyway.
- [x] 5.3 Fall back to the full metadata pass on any failure of the cached path. A skip must never be worse than not skipping.
- [x] 5.4 Add `--refresh-metadata` to `itgraph backfill`, forcing the pass for every channel the run walks.
- [x] 5.5 Rewrite the docstring in `tg/full_channel.py`: the reachability probe it describes no longer runs on every channel, and the reason it is acceptable to lose belongs next to the code that lost it.

## 6. Halting on a long FloodWait

- [x] 6.1 Add `FloodWaitTooLong(RuntimeError)` carrying `seconds` and `resume_after`. **Not** an `RPCError` subclass — see 6.3 for what that would cost.
- [x] 6.2 In `waiting_out_floods`, raise it instead of sleeping when the wait exceeds `flood_abort_threshold`. Below the threshold, behaviour is unchanged.
- [x] 6.3 Catch it explicitly in `backfill_channels`, before the `except (RPCError, OSError, ValueError, TypeError)` handler can see it, and break. Regression-test that a halt is not recorded as a transient channel failure: were it absorbed there, the run would request the next channel immediately after being told to stop, which is the ban-escalating behaviour the FloodWait handling exists to prevent.
- [x] 6.4 Catch it in `resolve_inventory` the same way, breaking out of whichever queue is running.
- [x] 6.5 Return the partial summary from both, so a halted run still reports what it committed.
- [x] 6.6 Report the halt in the CLI — the wait, the resume time, and the partial summary — and exit non-zero, so a scheduled run cannot pass for a clean one.

## 7. Tests

- [x] 7.1 Rewrite the existing exact-value pacing assertions — `test_channels_are_paced_and_sequential` and `test_the_defaults_are_the_slow_ones` in `tests/test_backfill.py`, and the paced-request assertion in `tests/test_resolve.py` — as band assertions. The band is the contract now.
- [x] 7.2 Test `request_gap` as a pure function against a stubbed random source: band bounds, the long-pause branch, and that a delay of `0` yields `0` with no branch taken.
- [x] 7.3 Test that the inter-channel pause is taken between channels, not before the first, and not for skipped ones.
- [x] 7.4 Test that the metadata request is skipped for a fresh payload, made for a stale one, made when the cached peer is unavailable, and made for every channel under `--refresh-metadata`.
- [x] 7.5 Test both halt paths: the run stops, remaining channels are untouched, the walked channel is not marked failed, and committed progress survives.
- [x] 7.6 Test that a wait at or below the threshold is still slept off and retried.
- [x] 7.7 Keep every test offline and deterministic. Patch the pacing seam; no test may take a real pause.

## 8. Documentation

- [x] 8.1 Document the new settings in README with their defaults and, for each, the circumstance that would justify moving it.
- [x] 8.2 Document what a halt looks like to the operator and what to do about it: wait out the reported time, then re-run — the walk resumes from its cursor.
- [x] 8.3 State in README that a first pass over the full inventory is expected to span several days, and that `--limit` is how it is spread deliberately rather than discovered the hard way.

## 9. Validation

- [x] 9.1 `make validate` clean.