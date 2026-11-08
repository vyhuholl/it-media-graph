# Design

## Context

See `proposal.md`, Why; what counts as a person is `specs/personal-data`. What shapes the approach, read on 2026-11-06:

- **Payloads are Telethon's `to_dict()`, re-encoded by `tg/payload.py`** and by nothing else. Every object carries its type in `_`, and every field is present, absent ones as `null`: 274,486 posts hold a `post_author` key, 26,977 of them non-null.
- **Where people were, measured on production:**

  | Where | Rows | People |
  |---|---|---|
  | `replies.recent_repliers` | 91,650 | 62,167 users; also 2,182 channels |
  | `reactions.top_reactors` | 13,671 | 6,341 users; also 147 channels and 5,899 anonymous entries |
  | `post_author` | 26,977 | names |
  | `from_id` a user | 2,920 | signatures with a profile |
  | `fwd_from`: a user, `from_name`, `post_author` | 292 / 790 / 1,230 | |
  | mentions by id | 64 | |
  | poll voters, giveaway winners, `recent_requesters` | none seen | |
  | `raw_channels.users` | 18 | all bots |

  109,480 posts name someone; 93,638 hold a user's peer somewhere.
- **Nothing reads any of it.** `derive` takes channel peers, whether `fwd_from` is null, and counters; `top_reactors` and the repliers are read by no code.
- **`store_messages` keeps the first fetch of a post.** A post stored before the change is never stored again, so only a migration clears it.
- **A restore runs the migrations before neutralising** (`adopt-infrastructure-requirements`, Decision 12), with `ITGRAPH_SKIP_BACKUP=1`.
- **The database has a 2 GiB limit,** and an out-of-memory kill of any backend restarts every connection. A recursive walk over every payload did exactly that on 2026-11-06.

## Goals / Non-Goals

**Goals:**

- One rule, applied at the one place that shapes payloads and once to what is stored, with nothing derived changing.
- The restore's read-back checks for a person anywhere in a payload, so the next shape Telegram adds is caught by the test restore rather than missed.

**Non-Goals:**

- A complete model of Telegram's schema. The rule is generic by type; a person's id as a bare integer under a key it does not name is its limit, stated below.
- Deleting old backups early.
- A contact a channel posts (`MessageMediaContact`: a user id, a phone number, names). It is content the channel chose to publish, like its text, and stays; none was seen on production. Decided by the operator on 2026-11-07.

## Decisions

### 1. The rule, by type before path

A list of paths is what `neutralise.sql` had, and it missed the paid reactors. Telethon tags every object with its type, so the rule walks the payload and acts on types wherever they are:

| What | Becomes |
|---|---|
| an object of type `PeerUser`, `InputPeerUser`, `InputPeerUserFromMessage`, `InputUser`, `InputUserFromMessage`, `UserEmpty` (a user reduced to an id), or `User` without `bot: true` | `null` as a field; dropped from a list |
| a list item of type `MessageEntityMentionName` or `InputMessageEntityMentionName` | dropped |
| a list item whose `peer_id` is of a type above (a reactor, a reaction) | dropped |
| the fields `post_author`, `from_name`, `saved_from_name` | `null` |
| the fields `winners`, `recent_requesters` (lists of bare user ids) | `[]` |

- **Null, not a missing key.** Telethon writes every field, so a removed key would make a cleared payload look unlike any other, and the migration would rewrite every row instead of the 40% that name someone. `tests/test_payload.py`'s "no key is dropped" keeps holding.
- **A channel is never touched.** `PeerChannel` is not on the list, so a channel among the commenters or the reactors stays with its count: decision 3 of the proposal.
- **The limit:** a person's id as a bare integer under a key the table does not name. None was seen on production. If one appears, the rule and a new migration take it, as below.

### 2. In `tg/payload.py`, at the boundary

`encode_payload` returns the payload with the rule applied: a function of its own, beside `json_safe`, over dicts, lists and scalars, which cannot raise on a shape it does not know. Its three callers (`tg/watch.py`, `tg/backfill.py`, `tg/full_channel.py`) are unchanged, so `tg/watch.py` keeps no diff, as the first production run asked for. The module's docstring states the exception to "verbatim". It is not parsing in the collector: nothing is read out of the payload, and nothing is kept beside it.

### 3. A data migration in SQL, frozen

One revision, revising `b7d2e4f19a30`:

1. Create a function in `pg_temp` with the rule, a copy of decision 1 in PL/pgSQL.
2. `UPDATE raw_messages SET payload = <function>(payload)` where a JSON path finds a type of decision 1 anywhere, or one of its fields is not null or not empty. Then the same for `raw_channels`.
3. `VACUUM FULL` of both tables, in Alembic's autocommit block, since `VACUUM` cannot run in a transaction.
4. The downgrade does nothing, and says so.

- **SQL, not the application's Python.** A migration is frozen, while `tg/payload.py` may grow; and SQL keeps 270 MB of payloads inside the database rather than through a pod.
- **One statement per table.** It streams, holding one row at a time, unlike the walk of 2026-11-06. Measured on the operator's machine on synthetic data of production's size (275,000 posts, 797 MB, 2.6 KB each, 110,000 naming someone): with the revision's own statements, 26.4 s for the update with its prefilter and 5.4 s for `VACUUM FULL`; the table went from 1,209 MB after the update to 751 MB, and no post named anyone after it. A first sketch without the prefilter took 21 s and 5.5 s. The test environment measures it on production's dump, with production's limits, before production runs it.
- **The two rules are held equal.** One table of cases, every scenario of `specs/personal-data` among them, runs through `tg/payload.py` and through the newest such revision's function, and any difference fails. So a rule that grows in Python fails the suite until a new revision clears stored rows by it too.
- **Rejected:** a command run by hand, by the operator's decision 4. It would need running in every environment and remembering, and a restored copy of an old dump would depend on it.

### 4. The restore: no payload statements, a read-back by type

`neutralise.sql` loses its payload statements. `readback.sql` looks for every type and field of decision 1, anywhere in `raw_messages` and `raw_channels`, by JSON path, and fails naming what it found and how often.

- **A dump taken before this revision** is cleared by the revision itself, which every restore runs before neutralising.
- **A dump taken after it that names someone** means production does too: the collector's rule missed a shape. The restore fails, and the database is emptied as for any read-back failure, rather than neutralising what production keeps. The weekly test restore becomes an audit of production.
- **The end-to-end suite** gets a dump of the previous revision for real: a database of its own, brought by Alembic to the revision before this one, seeded, and dumped by the backup job pointed at it. Setting the version back on a database at head would stop working at the first later revision that changes the schema. It also seeds a person at head into a second dump, which the restore must refuse.
- Specified in `adopt-infrastructure-requirements`' `database-backups` delta, which still holds the restore's requirements. Its tasks point here.

### 5. Text and docs

- **The rules:** `CLAUDE.md`'s "The raw layer is immutable" gains the exception, in its own words: what names a person is never stored, and was removed once. "What the restore neutralises keeps up with the schema" and `README.md` follow.
- **`src/itgraph/README.md` and the `edges` docstring:** where they say user ids "stay in the raw layer", they say they are never stored.
- **`docs/PLAN.md`:** the legal note keeps the measurement and says when it was removed.
- **`docs/privacy.html`** and the plan's note are published from `master`'s `docs/`. By the operator's decision of 2026-11-07 they change before production, in the agent's words, so they describe what the version of that date does, not the day production ran it: people removed before storing, those stored deleted when the version is first installed, the backups holding them gone about four weeks later. Pushed before production runs it, the Telegram paragraph speaks for a version not yet installed.

## Risks / Trade-offs

- **[A rule that raises stops every poll]** → it walks plain JSON values and has no branch that raises; its tests cover every fixture and every scenario, and a channel whose poll fails is isolated as before.
- **[The migration on production runs out of memory, as the walk did]** → a single streaming update, measured at production's size, and run first in the test environment's restore of production's dump under the same limits.
- **[Something read a removed field after all]** → nothing in `src/` does; the test that derives over the same posts as received and as cleared compares every derived table.
- **[`VACUUM FULL` locks the tables]** → for seconds, while the deploy has writers stopped.
- **[Backups hold people for 28 more days]** → accepted, by decision 6. The dump the migration takes first is the way back if the migration damages a payload, which the equality test is there to prevent.
- **[An old image deployed again collects people again]** → rolling back the pin brings back the old collector. The rows it adds name people until a release with this revision is deployed again, and the restore's read-back says so.
- **[Telegram adds a shape the rule does not know]** → a tagged one is caught by the type list; an untagged one is the stated limit. The test restore's read-back finds the tagged kind on real data.

## Migration Plan

1. **Agent:** this change and the restore's side of `adopt-infrastructure-requirements`; `make validate` and `make test-e2e` green.
2. **Operator:** the tag `v0.1.0`, as that change's tasks say.
3. **Operator:** the test environment's restore of production's newest full dump runs this revision on real data. Read its duration and the read-back.
4. **Operator:** production's first run, inside quiet hours. Its migration step takes a full dump, then runs both pending revisions.
5. **Done on 2026-11-07, before production, by the operator's decision:** the agent wrote `docs/privacy.html`'s paragraph and the plan's note, describing what the version does. They go live with the push of `master`, whenever the operator makes it.
6. **Nobody:** the dumps that hold people are gone 28 days after step 4 on the host, a day later off-site. The operator's machine's old dumps go in `adopt-infrastructure-requirements`' 15.3.

**Rollback:** reverting the pin restores the previous collector; the removed values do not come back, and nothing needs them. The downgrade does nothing.
