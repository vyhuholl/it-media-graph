## Context

First change in the project, so it fixes several conventions that later changes inherit: how the Telegram client is constructed, how records are upserted, and how manual labels are shaped. Constraints come from `docs/PLAN.md` — collection is MTProto-only, the object of study is the community rather than the topic, and no discovered data is ever discarded.

## Decisions

### Identity is the Telegram id, and nothing else

Usernames change and can be absent on private channels; titles change freely. Only the numeric id is stable, so it is the primary key.

Store the id in the bare form Telethon exposes as `entity.id`, without the `-100` prefix used in some Bot API contexts. The prefixed form is a different representation of the same channel, and mixing the two silently creates duplicate rows. Conversion happens at the edges if ever needed, never in storage.

### Channel versus chat is a fact, not a label

`is_chat` is a boolean taken from the Telegram entity type, separate from `kind`. A community's discussion group is both a chat and, say, company-run; forcing that into one enum would lose information. The comments phase reads this column, which is why chats are imported now rather than later.

### `kind` describes what a channel *is*, not what it is about

This rule decides the cases that would otherwise be argued repeatedly:

- A well-known recruiter writing in their own voice about hiring is `personal`. `vacancies` means a feed of job listings with no authorial voice.
- A developer's channel about their own startup is `personal`, not `company`.
- `media` is separated from `company` because they behave differently in the graph: an outlet acts as a high-degree hub and will need edge down-weighting, a corporate blog does not.

### Enums small, free text mandatory beside them

Values cover only what is already known to recur. Everything else goes into a note column, and a value is promoted to the enum once the same wording keeps appearing. Guessing the taxonomy up front produces categories that never occur and misses the ones that do.

```sql
CREATE TYPE channel_status    AS ENUM ('candidate', 'seed', 'maybe', 'rejected');
CREATE TYPE channel_kind      AS ENUM ('personal', 'aggregator', 'company',
                                       'vacancies', 'media', 'community', `event`);
CREATE TYPE reject_reason     AS ENUM ('not_it', 'adjacent', 'crypto', 'infobiz',
                                       'ads', 'content_farm', 'other_scene');
CREATE TYPE discovery_source  AS ENUM ('own_subscriptions', 'forward',
                                       'recommendation', 'mention', 'manual', 'linked_chat');

CREATE TABLE channels (
    tg_id           bigint PRIMARY KEY,
    username        text,
    title           text,
    is_chat         boolean NOT NULL DEFAULT false,

    status          channel_status NOT NULL DEFAULT 'candidate',
    reject_reason   reject_reason,
    reject_note     text,

    kind            channel_kind,
    kind_note       text,

    discovered_via  discovery_source NOT NULL,
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    reviewed_at     timestamptz,

    CONSTRAINT rejected_has_reason
        CHECK ((status = 'rejected') = (reject_reason IS NOT NULL))
);
```

`discovery_source` already carries values this change cannot produce. They are declared now so later changes add rows rather than alter the type.

### `reviewed_at` marks the review queue

Whether a channel still needs a decision is `reviewed_at IS NULL`, not a combination of null labels. `kind` being null would otherwise mean both "ordinary channel" and "not looked at yet", which is why `personal` exists as an explicit default rather than as the absence of a value.

### Writing a rejection note is enforced in the UI, not the database

The `CHECK` constraint requires a reason, not a note. Demanding free text on every obvious `not_it` makes review slow enough that it stops happening; the triage UI in a later change can insist on notes where they matter.

### One upsert helper, first discovery wins

All import paths go through a single upsert. Identity fields are refreshed on conflict; `discovered_via`, `first_seen_at` and every review field are not.

```sql
INSERT INTO channels (tg_id, username, title, is_chat, discovered_via)
VALUES (...)
ON CONFLICT (tg_id) DO UPDATE SET
    username = EXCLUDED.username,
    title    = EXCLUDED.title;
```

This answers "which source brings in new channels". It deliberately does not answer "how many sources pointed at this channel" — that needs a separate discovery-event table, which is empty of meaning while only one source exists and is deferred to the change that adds the second.

### Async throughout

Telethon is async, so the data layer is too: SQLAlchemy async with `asyncpg`. Alembic is initialised from its async template, avoiding a second sync driver and a second connection URL.

## Alternatives considered

- **Surrogate primary key with `tg_id` unique.** Rejected: an extra indirection with no benefit, since the Telegram id is already stable and globally unique.
- **`kind` as an array of tags.** Genuinely tempting — a corporate channel that also aggregates exists. Rejected for now because single-valued labelling is faster to perform, and the graph-building rules this feeds only need one dominant type. Revisit if `kind_note` fills up with "also an aggregator".
- **Dropping non-IT channels at import.** Rejected: re-crawling is the expensive operation, and rejections are the training data for the later classifier.

## Deferred

- Rejected channels must not resurface in the triage queue when rediscovered by forwards. Nothing to enforce yet — belongs to the change that introduces automatic candidates.
- `lang` and `lang_ratio` columns: no messages exist yet to detect language from.
- Participant counts, activity and other facts derivable without manual review.
- `linked_to` is added now but stays empty: resolving it needs a per-channel `GetFullChannelRequest`, which belongs to the change that already visits every channel. Until then, linked discussion chats imported from the dialog list sit as unreviewed candidates.

## Testing

Telethon is mocked; `dump-dialogs` is exercised against a fixed list of fake entities, with no network access. Idempotency is tested by running the import twice against a reviewed inventory and asserting review fields are untouched. Tests run on a separate `_test` database, created and dropped per session.