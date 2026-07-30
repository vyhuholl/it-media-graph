## Why

The first question the analytics must answer is variety, not volume: a channel reposting forty different people is a connector, one reposting the same target two hundred times is an echo. That measurement is currently wrong at the top of the ranking, because one author's channels are counted as different people.

Authors run several channels — a main channel and its podcast, a personal channel and its jobs board — and repost themselves between them. Every such repost is a real observation and a genuine edge, but it is not a relationship between two people, and counting it as one puts self-reposters into the top of "who reposts the most different sources". Nothing in the inventory records that two channels share an author, so nothing downstream can subtract them.

Measuring the four proposed signals over the current database (504 seeds, 15 651 edges, read-only, the project's own `t.me` parser) says how much is actually there, and two of the four do not behave as expected:

| signal | pairs found |
|---|---|
| mutual `about` reference | **1** |
| one-way `about` reference | 37 |
| `max_share ≥ 0.7`, ≥ 20 out-edges | 19 |
| mutual edges, ≥ 5 each way | 45 |
| shared ≥ 4-char username token | 216 |

**Mutual `about` references barely exist, and mutuality is mostly unknowable rather than absent.** Only 202 of 504 seeds have a stored `GetFullChannelRequest` payload, so 302 seeds have no description to read at all. Of the 37 one-way references, 31 point at a channel with no metadata row — the return link cannot be checked, only missed. Requiring mutuality measures metadata coverage, not affiliation.

**A shared username token is mostly a shared topic.** The 216 pairs come from tokens like `channel`, `tech`, `news`, `jobs` and `data`, each shared by 5–11 unrelated seeds. Restricting to tokens carried by at most two seeds leaves 27 pairs. The parameter that separates signal from noise is the token's *rarity across the inventory*, not its length — length alone cannot tell `podcast` from a company name.

**The signals do not corroborate each other.** Mutual `about` and shared token overlap in 0 pairs; mutual `about` and mutual edges in 0; `max_share` and shared token in 6 of 19. Any rule demanding two independent signals before proposing a pair would propose almost nothing.

So the useful design is a union of independent weak-to-strong signals, ranked and reviewed by hand — roughly 100 candidate pairs at sane thresholds, which is one review session, not a project.

## What Changes

- **`channels.operator_id`** — a nullable self-reference naming the canonical channel of the family this channel belongs to, in the shape `linked_to` already uses. Written only by human confirmation. A channel with no family keeps it empty.
- **`itgraph affiliates`** — a new offline command that scores channel pairs on the four signals and prints them ranked, strongest first. It reads `edges`, `channels` and the stored `raw_channels` payloads; it makes **no network request** and needs none, since every input is already collected.
- **Signals are stored, not just printed.** Each proposed pair is persisted with the per-signal evidence that produced it — which reference was found in whose description, the observed `max_share` and its denominator, the shared token, the edge counts each way. A ranked list whose reasoning has to be re-derived to be checked is not reviewable, and re-running the detection to re-read the reason it gave yesterday is how a threshold change silently rewrites history.
- **Confirmation is a separate, explicit step.** Detection never writes `operator_id`. The operator confirms a pair, names which side is canonical, and only then is the link written. A rejected pair is recorded as rejected, so the next run does not propose it again.
- **Every threshold is a parameter**, with defaults taken from the measurements above rather than from intuition: minimum out-edges before `max_share` means anything, the `max_share` floor, the minimum token length **and the maximum number of seeds a token may appear on**, the minimum edge count each way for the mutual-density signal. Signal weights are parameters too, since the ranking is what the operator is tuning.
- **The `about` link scan is new code, and the per-link parser is reused.** `parse_tme_link` and `normalize_username` apply unchanged, but `extract_references` does not: it reads Telegram entity offsets, and `ChannelFull.about` is a plain string carrying no entities. The description needs a plain-text scan for `t.me` links and `@mentions`, handing each hit to the existing parser.
- **Detection is re-runnable and derives from the raw layer.** Re-running over unchanged data proposes the same pairs and writes no duplicate; confirmations and rejections survive it.

Out of scope, deliberately:

- **Subtracting affiliated edges from the role metrics.** That is the analysis this change unblocks, and it belongs in a notebook until the shape of the correction is settled. Edges inside a family stay in `edges` — the repost really happened.
- **Automatic merging.** No threshold, on this data, separates an author's second channel from a close collaborator. The command proposes; the operator decides.
- **Transitive family closure.** Whether A→B and B→C implies one family of three is a question the confirmation step can answer per pair; inferring it is not worth the ambiguity yet.
- **Running `itgraph metadata` over the 302 seeds that lack a description.** It is the single largest improvement available to the strongest signal, but it is a quota-bearing pass on its own budget (~2 days at ~200 `channels.getFullChannel`/day), not a code change. Worth doing before trusting the `about` signal's coverage; not part of this change.

## Capabilities

### New Capabilities

- `channel-affiliation`: recognizing that several channels share an author. The signals and how they are computed from already-collected data, the ranked candidate list and the evidence stored with it, the parameters that govern it, and the confirmation step that turns a candidate into a recorded family.

### Modified Capabilities

- `channel-inventory`: **Channel Record** gains the family pointer — a channel record may name the canonical channel of the family it belongs to, and that field is written only by confirmed review, never by an import or a detection pass.

## Impact

- New column on `channels` + a table for candidate pairs and their evidence, in one Alembic revision. No existing column changes; a `SET NULL` self-reference like `linked_to`.
- `src/itgraph/db/models.py` — the `operator_id` column and the candidate model.
- `src/itgraph/db/channels.py` — writing a confirmed family link; the listing gains the family.
- New `src/itgraph/affiliation/` (outside `tg/` — it touches no network): the signal computations and the scoring.
- `src/itgraph/derive/references.py` — a plain-text link scan alongside the entity-based one.
- `src/itgraph/cli.py` — the `affiliates` command and its parameters.
- Tests: signal computation on synthetic inventories, threshold behaviour, idempotence of re-runs, and that detection writes no `operator_id`.
- `README.md` — the new command and where it sits in the workflow.
- **The `about` signal is only as good as metadata coverage** — 40% of seeds today. The command must report that denominator rather than let a small candidate list read as a small problem.
