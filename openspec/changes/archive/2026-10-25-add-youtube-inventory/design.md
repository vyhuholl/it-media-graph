## Context

First YouTube change; like `add-channel-inventory` for Telegram, it fixes conventions the later YouTube changes inherit: how a request is made and paid for, how a channel is identified, and how API data is kept. Motivation is in `proposal.md`; the requirements are in the three specs.

What already exists and is reused: the `channel_status` enum, the backup tiers in `db/backup.py`, the ansible deploy to the Ubuntu collection host, the pattern of `usernames.py` (refuse bad entries together, before anything is spent) and of `itgraph add` (bounded, resumable, failures written back out).

What does not carry over from Telegram: the account-safety section. There is no session, no lease, no shared exit — the API is keyed, and the host's always-on VPN covers reachability.

Where the operator works matters to the design. The live database, the bot and the collection run on the Ubuntu host, which is inconvenient to use; the operator's own machine holds only old dumps. Review is routine work at volume, and it has to happen on the operator's machine against the live database.

### Facts checked against the documentation, 2026-10-25

| Fact | Source |
|---|---|
| `channels.list` costs 1 unit; `id` takes a comma-separated list, `forHandle` one handle with or without `@`; at most one filter per call | developers.google.com/youtube/v3/docs/channels/list |
| `channelNotFound` (404) is documented for `id` only; the response to a handle with no channel is not documented | same page, errors table |
| "All API requests, including invalid requests, incur a quota cost of at least one point"; quota resets at midnight Pacific time; 10,000 units a day, with `search.list` in its own bucket of 100 calls | developers.google.com/youtube/v3/determine_quota_cost |
| `quotaExceeded` is a 403; the YouTube errors page shows no JSON example | developers.google.com/youtube/v3/docs/errors |
| An API key goes in the `x-goog-api-key` header; the `key` parameter is a fallback that exposes the key in URLs | docs.cloud.google.com/docs/authentication/api-keys-use |
| Non-Authorized Data at most 30 days (III.E.4.d), statistics likewise (III.E.4.b); no "new or derived data or metrics" (III.E.4.h(ii)); one project per API client (III.D.1.c); nothing said about channel ids | developers.google.com/youtube/terms/developer-policies |
| Under the derived-metrics amendment, statistics and derived metrics up to 36 months; titles, creator names and descriptions stay at 30 days | developers.google.com/youtube/terms/derived-metrics-policy (updated 2026-09-14) |
| `viewCount`: from 2026-08-24, every format counts a view the moment playback begins, autoplay and hover included | developers.google.com/youtube/v3/docs/videos |
| httpx logs every request's full URL at INFO; `MockTransport` serves requests in tests without a network | Context7, encode/httpx |
| google-api-python-client is "complete and in maintenance mode"; httplib2 is not thread-safe | Context7, googleapis/google-api-python-client |

## Goals / Non-Goals

**Goals:**
- A record of each YouTube channel that outlives any API data about it.
- One ledger every YouTube request, present or future, books through.
- A review that can be done on the operator's machine, in a text editor, with every mistake caught before anything is written.
- Compliance with the base policies, so the derived-metrics application can take as long as it takes.

**Non-Goals:**
- Videos, statistics history, the poll loop and its own pool — change 2.
- Recording which sources pointed at a channel. One source exists; the same deferral as on Telegram.
- A review interface in the bot (see "Review happens in a file").
- Removing `other_scene` from the Telegram enum. It would mean recreating the type under the live `channels` table; a separate change if ever wanted.

## Decisions

### Identity is the channel id, kept indefinitely

The `UC…` id is the primary key. Handles change, titles change, a channel can have no handle at all. The id is kept for as long as the record — the policies say nothing about identifiers, and the operator decided to treat the id as an identifier rather than as expiring API data (**not covered by the requirements**: a policy reading, not a policy statement). Everything else read from the API expires.

A `CHECK` holds the id to `^UC[0-9A-Za-z_-]{22}$`. The shape is undocumented; if a real id fails it, the first import says so loudly, and the check is relaxed by migration.

### Two tables: what the operator decided, and what the API said

```sql
CREATE TABLE yt_channels (                      -- ours; kept indefinitely
    channel_id      text PRIMARY KEY,
    status          channel_status NOT NULL DEFAULT 'candidate',  -- shared with Telegram
    kind            yt_channel_kind,
    kind_note       text,
    reject_reason   yt_reject_reason,
    reject_note     text,
    lang            yt_lang,
    discovered_via  yt_discovery_source NOT NULL,
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    reviewed_at     timestamptz,
    gone_at         timestamptz,  -- YouTube stopped returning it; payload removed
    CONSTRAINT id_shape            CHECK (channel_id ~ '^UC[0-9A-Za-z_-]{22}$'),
    CONSTRAINT rejected_has_reason CHECK ((status = 'rejected') = (reject_reason IS NOT NULL)),
    CONSTRAINT seed_is_labelled    CHECK (status <> 'seed' OR (kind IS NOT NULL AND lang IS NOT NULL))
);

CREATE TABLE yt_raw_channels (                  -- the API's; at most 30 days old
    channel_id  text PRIMARY KEY REFERENCES yt_channels ON DELETE CASCADE,
    payload     jsonb NOT NULL,                 -- the whole channel resource
    fetched_at  timestamptz NOT NULL
);
CREATE INDEX ON yt_raw_channels (fetched_at);
```

Unlike Telegram's `channels`, the record carries no title or handle columns. Listing, `mark @handle` and the review export read them out of `payload`. That costs a JSON accessor in three queries and buys the property the retention spec depends on: removing a payload removes the data, with no second copy to forget. Rejected: Telegram's denormalized `username`/`title` — every expiry and every "gone" would have to clear two places, and the one it missed would be the violation.

`status` reuses `channel_status`. The migration must declare it without creating it, and its downgrade must not drop it.

### Shared status, separate taxonomies

Status is a workflow: code filters on `'seed'`, the export rule is written against it, and its meaning is the same on both platforms. Kind, rejection reason and discovery source are taxonomies of a platform. A shared enum would make YouTube's values valid for Telegram, show `education` in `itgraph mark --kind`, and turn removing a value into recreating a type under the live `channels` table.

### The taxonomy

`kind` keeps Telegram's rule — what a channel *is*, not what it is about:

- `personal`, `company`, `media`, `event` carry over. `event` also takes meetups and communities: on YouTube both are archives of talks.
- `education` is new: institutions and course publishers. A person who teaches is `personal`.
- No `podcast`: a format, not an identity. If formats turn out to need their own curves, that axis comes from videos, as Shorts will.
- No `vacancies` or `aggregator`: the first hardly exists on YouTube, the second is a re-upload channel and is rejected as `content_farm`.

Rejection reasons are Telegram's, less `other_scene` — never used in Telegram review, where nearly every rejection was `not_it` or `adjacent` — plus `consumer_tech` for gadget reviews and PC building, which every machine-assembled list of "tech YouTubers" carries. A `content_farm` that slips through is not an incident; it is how the Telegram bot found a weakness in its own detector.

Language is `en`, `ru` or `other`, set by the reviewer. An IT channel in a third language is in scope as `other`: subject decides, not language. Detection from titles was rejected: under the base policies it is "new or derived data" (III.E.4.h(ii)), and language routes alerts in change 6, where a wrong guess is expensive. The language and country a channel declares are shown as a hint; showing API data is not deriving from it.

A seed needs a kind and a language, at the database level. Telegram defaults kind to `personal`; YouTube does not, because most review happens in a file, where an empty column is a slip, not an intent.

The boundaries the operator reviews against go into `src/itgraph/README.md`, "YouTube: разметка":

| Channel | Decision |
|---|---|
| gadget reviews, consumer tech, PC building, hardware | reject `consumer_tech` |
| game development: devlogs, engines | seed |
| games, game reviews | `not_it` |
| science explainers on CS and maths | seed |
| general popular science | `adjacent` |
| "make money with AI" | `infobiz` |
| faceless AI channels, re-uploaded talks | `content_farm` |
| IT in a third language | seed, `lang = other` |
| an institution or course publisher / a person who teaches | `education` / `personal` |

### The quota ledger

```sql
CREATE TABLE yt_quota_days (            -- the counter; bookings are atomic against it
    day    date,                        -- Pacific date
    pool   yt_quota_pool,               -- 'general' now; 'watch' arrives with change 2
    spent  integer NOT NULL DEFAULT 0 CHECK (spent >= 0),
    PRIMARY KEY (day, pool)
);
CREATE TABLE yt_quota_exhausted (       -- YouTube said quotaExceeded: the day is over
    day          date PRIMARY KEY,
    observed_at  timestamptz NOT NULL,
    command      yt_command NOT NULL
);
CREATE TABLE yt_requests (              -- the journal: who spent what
    id            bigserial PRIMARY KEY,
    requested_at  timestamptz NOT NULL DEFAULT now(),
    day           date NOT NULL,
    pool          yt_quota_pool NOT NULL,
    command       yt_command NOT NULL,          -- 'add', 'refresh'
    method        text NOT NULL,                -- 'channels.list'
    units         integer NOT NULL,
    outcome       yt_request_outcome            -- NULL: never learned
);
```

A booking is one transaction: refuse if the day is in `yt_quota_exhausted`; `INSERT … ON CONFLICT DO NOTHING` the day's row; `UPDATE yt_quota_days SET spent = spent + :u WHERE day = :d AND pool = :p AND spent + :u <= :cap RETURNING spent` — no row back means the pool is spent; insert the journal row with no outcome; commit; only then send. The conditional `UPDATE` is what keeps two processes inside a cap without a lock. The outcome is filled in after the response; a process killed in between leaves an honest NULL, as `BackfillStatus.RUNNING` does.

Units are never returned: YouTube charges invalid requests, and a timeout may or may not have been counted, so the ledger counts it. Overcounting costs headroom; undercounting costs the day.

The day is computed in Python with `zoneinfo` (`America/Los_Angeles`), so 23- and 25-hour days need no special case. Tests pin 2026-11-01 (25 hours) and 2027-03-14 (23 hours).

Caps: `general = daily − margin − watch reserve`. Defaults: 10,000 a day (Google's default), a 10% margin against the console's own count (the key is used by itgraph alone), a reserve of 3,000 for change 2's poll loop. The reserve is a setting from day one so no command written before change 2 grows used to spending it; its size is PLAN's estimate of polling, about 2,000 a day, plus headroom — **not covered by the requirements**, to be measured in change 2. Settings that leave the general pool nothing are refused at load.

`quotaExceeded` ends the day for every pool. Google's quota is the project's, not ours: our count only decides who may spend what; Google's decides whether anyone can.

Rejected: a per-process in-memory budget (two processes would each spend it); Telegram's `flood_events` (a record of limits hit, not of units spent — YouTube's limit is a counter we can keep ourselves).

### The client: httpx, key in a header

| | google-api-python-client | **httpx** | aiohttp |
|---|---|---|---|
| async | no: httplib2, not thread-safe | yes | yes |
| status | maintenance mode | maintained | maintained |
| tests without network | `HttpMock` | `MockTransport`, built in | needs `aioresponses` or a test server |
| mypy strict | needs a new `ignore_missing_imports` override — a config change | typed | typed |

What is needed is three or four GET endpoints returning JSON; discovery documents and OAuth would go unused. `httpx` is added as a direct dependency (today it arrives only through `weasel`, in the `data` group).

The key goes in `x-goog-api-key`. httpx logs every URL at INFO, so a key in `?key=` would reach journald on every request. `youtube_api_key` is a `SecretStr | None`: required, the settings would fail to load on any machine without it, taking every Telegram command and the bot down with them.

`yt/client.py` is the only place an `AsyncClient` is built, one per command run, with a 30-second timeout. Every request goes through the ledger. Errors are read from Google's standard body, `error.errors[].reason`, falling back to the status:

- 403 `quotaExceeded` → exhaustion, stop.
- 404, or an empty `items` → not found.
- 5xx and 429 → retried up to three attempts, waiting 2, then 4 seconds, each attempt booked.
- Anything else, or a body that cannot be read → stop with the status and whatever reason came back. An unreadable 403 is not taken for exhaustion: failing closed must not mean marking a day spent that is not.

### Requesting a channel

`part=snippet,contentDetails,statistics,brandingSettings,status,topicDetails`. The cost is per call, not per part, so "greedy when collecting" would take everything; III.E.4.d allows "limited amounts". These six are what review needs and what change 2 needs (the uploads playlist id in `contentDetails`); `localizations` is left out.

Lookups after parsing:
- Channel ids: `id=`, fifty to a call.
- Handles: `forHandle=@h`, one per call.
- Legacy names: `forUsername=`, one per call.

If a batch of ids is answered 404 (undocumented), each id in it is asked for alone, so one dead channel cannot fail forty-nine live ones. An empty or 404 answer for one entry means not found.

"Already known" without a request: an id with a record; a handle equal, case-insensitively, to `payload->'snippet'->>'customUrl'` without its `@`. That `customUrl` is the `@handle` is **not covered by the requirements** — the documentation says only "the channel's custom URL"; if it is wrong, a known handle costs one unit, and `mark @handle` falls back to the id.

### Entry parsing refuses before anything is spent

A pure module, `yt_entries.py`, mirrors `usernames.py`:

- Accepted: `@h`, `h`, `youtube.com/@h[/…]`, `youtube.com/channel/UC…`, a bare `UC…`, `youtube.com/user/NAME`. With or without a scheme, `www.` or `m.`, a trailing path or a query; percent-encoded handles are decoded.
- Refused, with the line number and what to supply instead: `/c/NAME` and bare `youtube.com/NAME` (no API method resolves them, and guessing `forHandle(NAME)` can land on someone else's channel), video, Shorts, live and playlist links, other hosts.

A bare word is a handle, never a legacy name. Handles compare case-insensitively (**not covered by the requirements**: the API reference does not say). Local validation stays minimal — no whitespace, no `/?#`; the API judges the rest.

### Retention mechanics

- **Expiry** is one statement, `DELETE FROM yt_raw_channels WHERE fetched_at < now() - interval '30 days'`, run in every `yt` command, network or not, before it reads or writes anything else in the database. It lives in `_run_yt`, the wrapper every command runs in — not the sub-app's callback, which click runs before a subcommand's `--help` and which made help need a database. A command that refuses its arguments or input exits before reaching the database, and so before expiry; the next command that reaches it expires. Thirty is the policy, a constant rather than a setting.
- **Refresh** asks for every record whose payload is missing or older than `youtube_refresh_after_days` (default 25, refused at 30 or more), rejected and gone channels included: 500 channels are ten units a cycle, and nothing then expires by neglect. A channel absent from the answer is gone: payload deleted, `gone_at` set. One that comes back gets its payload and loses `gone_at`.
- The **timer** is `itgraph-yt-refresh.timer`, daily at 05:00 host time (`OnCalendar=*-*-* 05:00:00`, overridable as `yt_refresh_schedule` like `baselines_schedule`) with `Persistent=true`, so a day missed while the host was off runs on its return. A host off for more than 30 days runs no expiry until it is back — a risk the operator accepted.
- **Logs** carry channel ids only. The refresh logs to journald, whose retention is not ours to bound.

### Backups

`yt_channels` joins `INVENTORY_TABLES`: it is hand work. Every full dump, the pre-migration one included, gets `--exclude-table-data='yt_raw_*'`. The payloads are the one thing in this database cheaper to fetch again (about ten units for the whole inventory) than to keep — and forbidden to keep. Current retention (inventory 30 dumps, full 4 weekly) happens to sit near 30 days, but pruning follows only a good dump, so a run of failures would keep old dumps past it; leaving the data out is what makes retention irrelevant. The ledger tables ride in full dumps only; losing a day's count costs at most an early `quotaExceeded`.

A restore leaves `yt_raw_channels` empty; the next refresh fills it. Said so in `src/itgraph/README.md`, beside the restore procedure.

### Review happens in a file, on the operator's machine

`review-export` writes TSV to standard output; `review-apply -` reads it from standard input; `scripts/on-host.sh` carries both over SSH:

```
scripts/on-host.sh yt review-export > ~/itgraph-review/batch.review.tsv
# edit in VS Code or Sublime; open each channel by its link
scripts/on-host.sh yt review-apply - < ~/itgraph-review/batch.review.tsv
```

The file:

```
# itgraph yt review-export
# exported: 2026-10-26T09:12:00Z
# expires:  2026-11-25 — holds YouTube API data: delete it once applied, never commit it
# decision: seed | maybe | reject | (empty: leave undecided)
# kind:     personal company media event education          (seed: required)
# lang:     en ru other                                      (seed: required)
# reason:   not_it adjacent crypto infobiz ads content_farm consumer_tech   (reject: required)
# guide:    src/itgraph/README.md, "YouTube: разметка"
channel_id	decision	kind	lang	reason	note	handle	title	subscribers	declared	link
```

- **Columns.** Editable columns come first, so they line up in a text editor before the long title; read-only columns are ignored on apply. Tabs and newlines in read-only values are flattened to spaces on export; a row with more fields than the header is an error. Enum values are matched case-insensitively. `note` is the rejection note on a rejection and the kind note otherwise, and is exported that way.
- **Validation** collects every error with its line number before writing anything, as `usernames.py` does. Two refusals go beyond what the database would refuse: a row with a kind, language, reason or note and no decision is a decision forgotten rather than one deferred, and a reason on a seed or a maybe is a value in the wrong column. Both are mistakes the operator would rather hear about than have applied.
- **Conflicts.** A row equal to the record's current review is "unchanged" and moves no timestamp — which is what makes a second apply a no-op. Otherwise, if the record's `reviewed_at` is later than the header's export moment, someone changed it since: the row is a conflict, reported, not written. Everything else is written in one transaction. Changing a decision already made is `yt mark`'s job.
- **`mark` keeps what it is not given.** The file shows the current values, so an emptied cell there means emptied. `mark` shows none, so a flag left out means "unchanged": kind, language, and the kind note unless the decision is a rejection — the way Telegram's `mark` keeps a kind it was not given. Without that, `mark --maybe` would erase a reviewer's kind and note. A rejection's note goes with its reason, as on Telegram.
- **Expiry.** The header's export moment is also the file's expiry: past 30 days `review-apply` refuses it. Deleting the file is the operator's job; the header and the README say so, and `.gitignore` gains `*.review.tsv` for a file saved inside the repository by mistake.

Alternatives rejected:
- `mark` per channel over SSH: no tooling, and 100–500 commands.
- An SSH tunnel to Postgres with the CLI on the operator's machine: local code can run ahead of the host's schema, and `DATABASE_URL` would switch between the working and the local database — the one-keystroke hazard `db/guard.py` exists for.
- Buttons in the bot: the bot's role would gain writes to the inventory, widening what a leaked token reaches, and `bot/` makes no collection writes by design.
- A copy of the database on the operator's machine: two-way sync of a database.

### `scripts/on-host.sh`

Bash. Reads `ansible_host` and `ansible_user` from `deploy/inventory.ini`, and runs `ssh -T user@host "cd ~/itgraph && ~/.local/bin/uv run itgraph <args>"`, the paths the playbook installs (`repo_dir`, `uv`). Arguments are quoted with `printf '%q '`; `-T` keeps standard input byte-clean; ssh returns the remote exit status. Network commands go the same way — `scripts/on-host.sh yt add --from-file - --failures - < seeds.txt > failed.txt` — so the seed list never has to sit on the host.

### Commands

A typer sub-app, `itgraph yt` — the first in the CLI, so YouTube's seven commands do not crowd Telegram's in `--help`. Only `add` and `refresh` make requests and carry a `yt_command` value; the others are offline and say so in their help.

### Settings

`youtube_api_key` (`SecretStr | None`; empty counts as unset), `youtube_daily_quota` (10,000), `youtube_quota_margin` (0.10), `youtube_watch_reserve` (3,000), `youtube_refresh_after_days` (25, < 30), `youtube_request_timeout_seconds` (30). Constants, not settings, where the value is a fact about YouTube rather than a choice: the 30-day limit, the Pacific time zone, method costs, fifty ids per call, the parts requested.

Deploy: `youtube_api_key` in the vault and its example; `env.j2` writes `YOUTUBE_API_KEY` in a block skipped when it is empty, like the proxy's; the refresh unit is installed always and enabled only when the key is set.

### Documentation

- `src/itgraph/README.md` (Russian): the API key, `yt` commands, the review round trip, the review guide with the table above, the restore note.
- Root `CLAUDE.md` (agreed with the operator): the YouTube exception to "the raw layer is immutable"; under "Never commit", the YouTube API key and review files.
- `src/itgraph/CLAUDE.md`: layout rows for the new modules.
- `docs/PLAN.md`: `search.list` is 1 unit from its own bucket of 100 calls, not 100 units of the 10,000; III.E.4.h(ii) covers "new or derived data", which reaches language detection and change 4's mention edges; the derived-metrics page as updated on 2026-09-14; the decision (apply, from the operator's personal Google account, base rules until accepted); `viewCount` counting at playback start since 2026-08-24, Shorts no longer differing by counting rule; change 1's row in the order of changes.

## Risks / Trade-offs

- [The ledger is an estimate; the console is the truth] → the 10% margin, and `quotaExceeded` overrides any count.
- [Error bodies and the not-found answer are undocumented] → read defensively, fail closed, and treat both 404 and an empty `items` as not found. The operator's first real run settles the shapes; fixtures stay synthetic.
- [`x-goog-api-key` is documented for Google Cloud generally, not for YouTube specifically] → the first real call settles it. If refused, fall back to `?key=` with the `httpx` logger raised to WARNING — a fallback, not the plan.
- [`customUrl` may not be the `@handle`] → costs a unit per known handle on import and nothing else.
- [The id `CHECK` may be wrong] → fails loudly on the first import; relaxed by migration.
- [The project belongs to a personal Google account] → if that account loses access, all YouTube work stops, and one project per client leaves no fallback. Accepted by the operator.
- [A review file can outlive 30 days on the operator's machine] → apply refuses it; deleting it rests on the operator, and the header says so.
- [Keeping channel ids is a reading of silent policy] → the operator's decision, recorded here and in `docs/PLAN.md`.
- [The host must be on for review] → it is on: it runs the bot.

## Migration Plan

1. The migration: five tables, seven enum types, `channel_status` reused. Verified with `upgrade head` and `downgrade -1` on a `_test` database only; the downgrade drops the YouTube types and leaves `channel_status`.
2. The backup change lands with or before the migration, so the pre-migration dump on the host is already the new kind.
3. The operator adds the key to the vault and runs the playbook: `.env` gains the key, the timer is enabled, `alembic upgrade head` runs on the host.
4. First real run, by the operator: `scripts/on-host.sh yt add @<a handle they know>`, then `yt channels` and `yt quota` — confirms the header, `customUrl` and the ledger. Then the seed list with `--limit`.

Rollback: disable the timer; `alembic downgrade -1` after a backup. Nothing outside `yt_*` is touched.

## Assumptions not covered by the requirements

Confirmed by the operator on 2026-10-25: the derived-metrics application is filed (base rules until accepted); the project is owned by the operator's personal Google account; the channel id is kept indefinitely; rejected channels are refreshed like any other; no expiry runs while the host is off; the 10% margin; the key is used by itgraph alone; review by file over SSH, in a text editor; titles in the review file, bounded at 30 days.

Open, settled by the first real run: the `x-goog-api-key` header; `customUrl` as `@handle`; the id shape; the response to a handle or id with no channel; Google's error body on YouTube; case-insensitive handles.

Decided in this design, open to veto: kind is required for a seed, with no `personal` default; the 3,000-unit watch reserve.

## Open Questions

None that change the specs or the tasks. Where alerts about Russian-language YouTube go stays with change 6.
