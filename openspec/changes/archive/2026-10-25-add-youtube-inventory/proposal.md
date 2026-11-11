## Why

Every later YouTube change — the poll loop, the history walk, the mention graph, the alerts — keys off a reviewed channel record, and none exists yet. The seed set is about a hundred channels assembled by hand from a language model's and the manager's suggestions, so it carries renamed, dead and invented handles; each entry has to be resolved through the API and looked at by a person before it counts.

## What Changes

- A YouTube Data API v3 client authenticated by an API key. The key is a secret on the same footing as the Telegram api hash; it is optional in settings, so a machine without it still runs every Telegram command and the bot.
- One quota ledger every YouTube command spends through: a day that ends at midnight Pacific time, units booked before each request, a `quotaExceeded` answer that stops all YouTube work until the reset, a 10% margin against the console's own count, and a share held back for the poll loop that change 2 adds.
- `yt_channels` — the operator's records: channel id, status, kind, rejection reason, language, provenance.
- `yt_raw_channels` — the latest channel payload, the only place API data lives. Never backed up.
- New commands under `itgraph yt`:
  - `add` — resolves handles, `@handles`, channel URLs, `UC…` ids and legacy `/user/` links to channel ids; refuses forms the API cannot resolve before anything is spent; creates unreviewed candidates only.
  - `review-export` / `review-apply` — a tab-separated review file the operator edits in a text editor on another machine; validated whole before anything is written.
  - `mark` — one review decision, and the only way to change a decision already made.
  - `channels` — the listing and review progress.
  - `quota` — today's spending and the time of the next reset.
- `scripts/on-host.sh` — runs an `itgraph` command on the collection host over SSH with stdin and stdout passed through, so review is done on the operator's own machine against the live database.
- Review taxonomy for YouTube: kind `personal`, `company`, `media`, `event`, `education`; rejection reasons `not_it`, `adjacent`, `crypto`, `infobiz`, `ads`, `content_farm`, `consumer_tech`; language `en`, `ru`, `other`. Status values are shared with Telegram.
- Documentation: the review boundaries in `src/itgraph/README.md`; the YouTube exception to "the raw layer is immutable" and two "Never commit" entries in the root `CLAUDE.md`; corrections to `docs/PLAN.md` found while checking it against the API documentation.

Out of scope: videos, polling, history, the mention graph, discovery, alerts, comments. Each follows in its own change (`docs/PLAN.md`, "Order of changes").

## Capabilities

### New Capabilities

- `youtube-api-access`: how every request to the YouTube Data API is authenticated, accounted against the daily quota, and stopped when the quota is gone.
- `youtube-inventory`: the YouTube channel record, importing the seed list, review by file and by command, and the listing.

### Modified Capabilities

None. The Telegram inventory is untouched: YouTube gets its own tables and its own kind, rejection and discovery types; only the `channel_status` type is shared, and it gains no values.

## Impact

- New code: `src/itgraph/yt/` (client, resolution, import, refresh), pure modules for entry parsing, the review file and the quota day, `db/` modules for the inventory and the ledger, seven CLI commands under one `yt` sub-app.
- New dependency: `httpx` (runtime).
- Schema: one Alembic migration adding five tables and seven enum types; `channel_status` is reused, so the downgrade must not drop it.
- Deploy: `YOUTUBE_API_KEY` in the vault and `.env`; an `itgraph-yt-refresh` service and timer, enabled only when the key is set.
