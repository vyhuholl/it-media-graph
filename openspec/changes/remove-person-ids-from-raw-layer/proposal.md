# Proposal

## Why

The raw layer stores Telegram's objects verbatim, and with them people who never chose to be in this database: measured on 2026-11-06, 109,480 of 274,486 stored posts carry a person's account id or name — 62,167 distinct users among a post's latest commenters, 6,341 among its top paid reactors, 66,805 across the two — unhashed, in production and in every full dump. Nothing reads them. `docs/PLAN.md` says commenter ids are personal data to be stored hashed if at all, and `docs/privacy.html` says itgraph keeps no data about readers; neither is true of the raw layer. It is fixed before the first release, so that what an operator first deploys collects none of it.

## What Changes

- **One rule says what names a person, and the system keeps none of it:** a user's account wherever it sits, a mention of a user by id, a reactor or a reaction given by a user, the name signed under a post or a forward, and the users of a channel's payload who are not bots. A channel stays, wherever it sits: a channel commenting under a post, or among its reactors, is the graph.
- **The collector applies the rule before it stores a payload,** in the one place that shapes payloads, for posts and channel payloads alike. **BREAKING** for the invariant: the raw layer is verbatim except for what names a person. The exception is stated in the rules, the specs and the README.
- **A migration applies the same rule once to what is already stored,** then rewrites the two tables on disk. It runs in the deploy, with writers stopped, in every environment, and in every restore of a dump taken before it.
- **The restore's read-back looks for a person anywhere in a payload,** not at a list of paths, and `neutralise.sql` no longer touches payloads. That list missed the paid reactors, so a restored copy would have carried 6,341 of them. A dump taken after this change that names someone means production does, and the restore fails saying so. Specified in `adopt-infrastructure-requirements`, whose delta still holds the restore's requirements; the code lands here.
- **Backups that hold people expire by themselves,** the dump the migration takes first among them: 28 days on the host, 28 and one in the off-site store. Nothing is deleted early: they are the way back.
- **`docs/PLAN.md`'s legal note** says what was removed and when; **`docs/privacy.html`** says so too. Both describe what the version does, since they go live from `master` when it is pushed, which may come before production runs it.

**Out of scope:**

- Hashing. Nothing reads these ids, an unkeyed hash of a Telegram id is reversed by enumerating ids, and a keyed one is still personal data with a key to keep. The comments phase in `docs/PLAN.md` designs its own.
- Post text, the author names on linked web pages and a contact a channel posts: public content the channel chose to publish, kept as published. Handles found in text (`pending_mentions`) are derived from it.
- Comments themselves, and alerts on them.

## Capabilities

### New Capabilities

- `personal-data`: what names a person in what Telegram returns, that the system never stores it, that it was removed from what was stored before, and that nothing derived depends on it.

### Modified Capabilities

- `message-backfill`: "Raw Message Storage" and "Channel Metadata Pass" store payloads as received but for what names a person, instead of verbatim.

## Impact

- **Code:** `tg/payload.py` gains the rule; `tg/watch.py`, `tg/backfill.py` and `tg/full_channel.py` keep calling `encode_payload` as they do. One Alembic revision, data only, revising `b7d2e4f19a30`. `deploy/ansible/itgraph/files/neutralise.sql` and `readback.sql`.
- **Production:** the first deploy of the release runs the migration: about half a minute measured locally at production's size, inside the collector's quiet hours, with writers already stopped for the takeover. Rehearsed on production's dump in the test environment first.
- **Backups:** dumps taken before the deploy hold people until they expire, 28 days after it at most.
- **Rules and docs:** root `CLAUDE.md`, `README.md`, `src/itgraph/README.md`, `docs/PLAN.md`, `docs/privacy.html`, the `edges` model's docstring.

## Decision record

Brought as questions in the explore session of 2026-11-06, each with a recommendation. The operator's answer to all of them: «Согласна». So, confirmed as of that date:

1. Every person in the table measured that day: commenters, paid reactors, signatures, authors who are users, forwards from users, hidden senders' names, mentions by id, poll voters, users of a channel's payload who are not bots.
2. Removed, not hashed.
3. Channels among commenters and reactors kept.
4. Production cleared by a migration in the deploy, not by a command run by hand.
5. `VACUUM FULL` of the two tables in the migration.
6. Backups holding people left to expire.
7. `docs/privacy.html` says what was removed, with the date; its exact words are the operator's. On 2026-11-07 the operator left the words to the agent and moved the edit before production (`tasks.md`, 5.1).
8. Author names of linked web pages kept.
9. The restore's side specified in `adopt-infrastructure-requirements`, the rest here, both before the first tag.
10. A contact a channel posts kept as content, on 2026-11-07, by the operator's answer to `/opsx:verify` («Да, правь всё»).

Nothing here is still uncovered by the requirements.
