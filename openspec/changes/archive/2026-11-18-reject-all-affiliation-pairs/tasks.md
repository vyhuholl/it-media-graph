# Tasks

## 1. Telegram bulk rejection

- [x] 1.1 In `db/channels.py`, add a module-private shown-pending predicate
      (pending decision, `status = seed` on at least one side, over the
      channel joins) and the two functions built on it:
      `count_shown_pending(session)` and `reject_all(session, *, note)`,
      the latter mirroring `reject_token`'s count-then-one-UPDATE shape.
      Verify in `tests/test_affiliation_db.py`: shown pending pairs are
      rejected with `decided_at` and the default note, hidden non-seed pairs
      stay pending, already-decided pairs are untouched, `--note` overrides
      the default, and an empty list returns 0 writing nothing.
- [x] 1.2 In `cli.py`, give `family` an optional `--reject-all`: channels
      become an optional argument, mutually exclusive with `--reject` and
      `--withdraw`, taking no channels. The prompt states the count and the
      per-pair undo, accepts only the exact word `yes` (anything else or EOF:
      nothing written, exit 1), and is skipped when nothing is shown-pending.
      Verify in `tests/test_affiliation_family.py`: a confirmed run rejects
      exactly the shown pairs, a declined and an EOF run write nothing, the
      empty case prompts nothing, and each bad flag combination fails.

## 2. YouTube bulk rejection

- [x] 2.1 In `db/yt_channels.py`, add the same predicate and function pair
      next to `reject_token`, over `YtChannel` status. Verify in
      `tests/test_yt_affiliation_db.py` with the same cases as 1.1.
- [x] 2.2 In `cli.py`, give `yt family` the same `--reject-all`, mutually
      exclusive also with `--reject-token`. Verify in
      `tests/test_yt_affiliation_family.py` with the same cases as 1.2, plus
      the `--reject-token` combination failing.

## 3. Verification

- [x] 3.1 `make validate` green, and `uv run itgraph family --help` /
      `uv run itgraph yt family --help` show the flag with the confirmation
      and seed-scope wording on both commands.
