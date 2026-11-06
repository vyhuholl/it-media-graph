# Proposal

## Why

The shared server is being brought to the requirements in `docs/INFRASTRUCTURE.md`, one project at a time, each in one pass: review, a role in its own repository, pinned releases, `restricted` and read-only, a one-command restore, graphs and alerts, and the old deploy path removed. This project is next.

Today it meets few of them. Its playbook logs in as root, and root's login on the server has been closed since 2026-11-02, so nothing can deploy until this pass ends. The inventory and the vault live in this repository; production's belong on the controller alone. Postgres runs as root with a writable root filesystem, and the namespace enforces no standard. A restore is a manual drill. Prometheus reads nothing of this project, and a pod that sleeps forever holds the Telegram session and every secret.

What sets this pass apart from the projects before it is a live collector on the one Telegram account the project has, and data that cannot be rebuilt. The test environment must never hold the session, so the collector cannot be tried there the way everything else can. The design answers that by changing as little as possible of what cannot be tried.

## What Changes

- **An Ansible role in this repository** (`deploy/ansible/itgraph`), with entry points `main` and `restore`. It takes everything about where it runs as inputs and names no host and no repository. **BREAKING:** `deploy/playbook.yml`, `deploy/decommission.yml`, the inventory example and the committed vault go at the end of the pass. The encrypted vault stays in history.
- **A release is a version tag.** `release.yml` runs the tests and the role's checks, retags the image CI built for that commit by digest, and publishes the role's archive with `SHA256SUMS`. Nothing deploys on a merge or a tag. A push to `master` still gates nothing.
- **An environment says whether it collects.** Production runs the collector. The test environment runs everything else on restored data, with `watch` at zero replicas, no YouTube key, and a proxy whose only outbound closes every connection. There, one short-lived pod with the collector's own spec must start, take the lease, log its route and fail. **The session never reaches a test environment.**
- **`restricted` and read-only.** The namespace enforces `restricted`. Postgres runs as uid 999 with a read-only root, on the volume as it stands: nothing on it needs changing.
- **Each workload holds only its own secrets.** The one Secret every pod reads today is split, so that the bot's pod no longer holds the superuser's URL, the api hash, the B2 key or the YouTube key, and rotating the bot's token no longer restarts the collector. The Telegram credentials become optional in settings, refused only where a client is built.
- **Egress is denied unless named.** The collector may reach DNS, the database and the proxy, and nothing else, so "goes through the exit" becomes a network rule and not only a setting.
- **The console becomes a Job per command.** `scripts/on-host.sh` creates it, runs the command with standard input and output passed through, and deletes it. The session is mounted only when asked for. The role's migrations and checks run in pods of their own. **BREAKING:** the script reaches the host through the operator's own account and sudo, and no longer reads an inventory.
- **`alerts` and `score` become one CronJob,** `itgraph-detect`: same cadence, sibling containers, so a failed `derive` does not stop scoring. The other five stay as they are.
- **Restore.** The `restore` entry point loads a full dump, checks every table against it, migrates, neutralises what would act on anyone or name anyone, and proves the service with real writes. The same command tries the off-site copy.
- **Metrics.** A small read-only exporter, `itgraph-metrics`, serves series worked out from the database, with a role of its own that can read no API data. The collector and the bot gain no listener. Rules and the dashboard live in the infrastructure repository, handed over in `design.md`.
- **Database access.** A second Service at a fixed address, an input, for operators' SSH tunnels.
- **Secrets.** Values move to the infrastructure repository's vault. The database and SOCKS passwords derive from its seed. The bot token, the YouTube key and the B2 key are rotated. The session and the api hash are kept. Done by the operator; see the Migration Plan.
- **`docs/privacy.html`** says who can reach the server, which has been inaccurate since the move.
- **Leftovers removed:** the launchd backup agent and its `make` targets, the vault's hooks and scripts, the cutover variables.
- **Rules rewritten.** These describe today's path and change with it (`docs/INFRASTRUCTURE.md`, "While this is the target", item 5): the root `CLAUDE.md`, `src/itgraph/README.md`, `README.md`, `deploy/README.md` (which becomes `deploy/runbook.md`), and the parts of `docs/PLAN.md` that describe the server. `docs/INFRASTRUCTURE.md` itself does not change.

**Out of scope:**

- **People in the raw layer.** Removing them from what is collected and from production's raw layer is `remove-person-ids-from-raw-layer`, which lands before this pass's first release, so that its first deploy runs both. The restore's read-back is specified here, and relies on that change's migration (Decision 13).
- **Alerts on comments.** Comments are measured and do not alert, by decision (`docs/PLAN.md`, "Comments are measured but do not alert"). Measured on 2026-11-06 over 14 days, enabling them at the current threshold would raise 2 alerts against 87 from the other metrics: the model needs replacing, and that is its own change.
- **A session backup.** It is left out of off-site copies on purpose, and stays out.

## Capabilities

### New Capabilities

- `collection-monitoring`: what the project tells the shared monitoring: the series, where they come from, on which port, who may read them, and that they carry no API data. The rules and the dashboard are the infrastructure repository's.

### Modified Capabilities

- `collection-deploy`:
  - Gone: restoring the operator's dump from a deploy, deploy parts selected by tags, the session moving between hosts, retiring a previous host, resolving the tip of `master`.
  - New: a deploy as a role run with its inputs, a release by tag, runs that never interleave, restricted and read-only pods, each workload's own secrets, denied egress, a fixed address for the database, an environment without collection, taking over what is already there, writers stopped before the database changes.
  - Kept, in the new terms: the schema at head before services, a broken database stopping the deploy, the session never replaced, one image, the exit and its check, restart detection.
- `database-backups`: one-command restore into a test environment, its checks, and a restored copy that acts on nobody and names nobody.
- `youtube-data-retention`: a restored copy is shown to hold no API data.
- `youtube-inventory`: "Remote Operation" runs commands in a short-lived Job through the operator's own account, with the session mounted only on request.

## Impact

- **Code:**
  - `src/itgraph/metrics/` and an `itgraph metrics` command.
  - An Alembic revision for the exporter's role.
  - `config.py`: the Telegram credentials optional, and a setting for the Bot API's address.
  - `tg/client.py`: a refusal when they are absent. `tg/watch.py` and the bot's delivery are not touched.
- **Deploy:** `deploy/ansible/`, `deploy/release.sh`, `deploy/runbook.md`, `.github/workflows/release.yml`, `scripts/on-host.sh`.
- **Dependencies:** none in the application. `k3d` and `promtool` on the operator's machine for the end-to-end suite and for trying the proposed rules.
- **Scheduled work:** one CronJob fewer, one Deployment replaced by another. Idle cost is in `design.md`.
- **The server:** a request to the infrastructure repository (`design.md`, Decision 16), and a migration in place with a gap of about five minutes, started inside the collector's quiet hours so that no reading is lost.
- **Personal data:** a restored copy names no person and holds no trace of the operator's subscriptions. Production's raw layer is cleared by `remove-person-ids-from-raw-layer`, in the same first deploy.

## Decision record

Brought as questions in the explore session of 2026-11-06, each with options and a recommendation; `design.md` carries each decision. The user's answer, verbatim, in the part that concerns the project:

```
Делай всё как рекомендуешь.

Q12 — потом сделаем отдельный change, удаляющий ID комментаторов. Всё равно они не нужны, раз с содержимым комментариев мы ничего не делаем и в ближайшее время не планируем.

Q13 — согласна.
```

So these, marked "not covered by the requirements" until then, are confirmed as of that date:

1. The test environment's shape, with `blackhole` as the proxy's outbound and a failing pod as the collector's trial.
2. The production run starting at about 06:15 Moscow time.
3. A Job per command in place of the console.
4. `alerts` and `score` merged; the rest kept.
5. Per-workload secrets.
6. Egress denied by default.
7. Rotating the bot token, the YouTube key and the B2 key; keeping the session and the api hash; the VPN credential only if its address stays.
8. Tests in CI on a tag alone.
9. An exporter of its own with a read-only role.
10. A `dir` key and a name prefix in the restore's sources; the off-site copy tried in this pass and after any change to the off-site code.
11. Neutralising channels' provenance, notes, undelivered alerts and the person fields in payloads.
12. Commenter ids handled by a later change. On the same date the operator moved it before the first tag: `remove-person-ids-from-raw-layer`, with its own decision record.
13. `docs/privacy.html` changed in this change.
14. A single YouTube timeout left to raise the shared alert; the noise to be read for a week.
15. The session's old copy on the operator's machine kept until the pass ends, then deleted.

The wording of `docs/privacy.html` was confirmed by the operator on the same date (`tasks.md`, 11.1).

**Still not covered by the requirements:** every alert threshold in `design.md`, which are proposals.
