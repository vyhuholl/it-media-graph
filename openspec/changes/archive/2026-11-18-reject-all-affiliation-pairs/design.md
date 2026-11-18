# Design

## Context

Both review loops already exist end to end: detection stores candidates,
`family` decides them. YouTube has `--reject-token` — the one bulk move in
either command — and its shape (`db/yt_channels.py: reject_token`: count
pending, one UPDATE, return the count) is the template this change follows on
both sides. Telegram's `family` takes channels as a required argument;
YouTube's is already optional because of `--reject-token`, and Telegram now
needs the same loosening.

The "shown" list is defined in two places already — `list_candidates` in
`db/affiliation.py` and `db/yt_affiliation.py`, both filtering pending pairs
to those with `status = seed` on at least one side. The bulk rejection must
reject exactly what that filter shows, no more.

## Goals / Non-Goals

**Goals:**

- One statement clears the shown review list, on both platforms.
- The operator's consent is informed: count before the write, undo story in
  the prompt, exact `yes` to proceed.

**Non-Goals:**

- No `--withdraw-all` (undo stays per pair; the prompt says so).
- No change to detection, thresholds, or what future runs propose.

## Decisions

- **The DB layer never prompts.** Each side gets two small functions:
  `count_shown_pending(session) -> int` and `reject_all(session, *, note)`,
  the second mirroring `reject_token` (count, one UPDATE over the same WHERE,
  return the count). The CLI counts, prompts between the two, and only then
  writes. The alternative — one function taking a confirm callback — couples
  the DB layer to the console for no second caller.
- **The shown-pending predicate is shared within each module**, a
  module-private helper returning the `decision = pending AND (a.status =
  seed OR b.status = seed)` condition with the channel joins, used by both
  functions. Duplicating it from `list_candidates` by hand is how "what the
  list shows" and "what the bulk command rejects" drift apart.
- **Exact `yes`, read with `input()`, not `typer.confirm`.** The spec names
  the word; `confirm` would also take `y` and bare Enter-as-default paths.
  Anything else — including EOF, which is what a non-interactive context
  produces — declines: a message that nothing was written, exit code 1 (an
  aborted command, matching how Typer exits on `Abort`).
- **`decided_at = now`, note defaults to `--reject-all`.** The note names the
  mechanism because no token or pair identifies a bulk decision;
  `--note` overrides, as on every other decision form.
- **Zero pending skips the prompt.** The count comes first; nothing to reject
  means nothing to confirm, and the command just says so.
- **Telegram's `channel_refs` becomes `list[str] | None`**, exactly the
  loosening YouTube's `--reject-token` already made there, with the same
  mutual-exclusion checks: `--reject-all` takes no channels and combines with
  none of `--reject`, `--withdraw`, `--reject-token`.

## Risks / Trade-offs

- [Count shown and rows written can drift if another decision lands between
  prompt and write] → Only the operator runs these commands; the same window
  already exists in `--reject-token`, and the UPDATE re-applies the same
  predicate, so a pair decided in between is simply not touched.
- [A bulk rejection of ~180 pairs is undoable only pair by pair] → The prompt
  states that before asking; the table sits in the hand-reviewed backup tier,
  so a mistyped run is recoverable from a dump too.
- [Hidden non-seed pairs can accumulate silently after the shown list is
  cleared] → Deliberate: they were never on the list the operator rejected,
  and the next detection run's report still counts what awaits review.

## Migration Plan

No schema change; deploy is the usual image build. Nothing to roll back
beyond restoring candidate rows from a backup, which the weekly and
hand-reviewed tiers already take.
