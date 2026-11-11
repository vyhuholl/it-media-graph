# Design

## Context

See `proposal.md`, Why, and its decision record. The requirements are in `specs/`. What shapes the approach, read on 2026-11-06:

- **The running image** is commit `8cbd0bb`, the backup's retry fix; only a documentation commit has followed it. The Error pods left in the namespace tell nothing new: B2's 503 before that fix, the bad kernel on 2026-11-02, and one `ReadTimeout` from YouTube on 2026-11-04.
- **Objects** carry the field manager `itgraph` with operation `Apply`. The StatefulSet mounts its claim as a plain volume, not through `volumeClaimTemplates`.
- **Volumes.**
  - Every data file is 999:999, and `pgdata` is 999:0 with mode 700. Each claim's mount root is 0:0 with mode 777.
  - The backups and the session are uid 3000.
  - `fsGroup` has no effect on these claims, which are hostPath volumes: `/backups` stays root:root inside a pod with `fsGroup: 3000`. It works on `emptyDir`.
- **Postgres** runs as uid 999 with no capabilities once started. Since start it has written outside its volume only its socket and lock in `/run/postgresql`. `/tmp` is empty. `/dev/shm` holds 1.1 of 256 MiB, used for parallel queries. Local connections are `trust`, and the file that says so is on the volume.
- **Dumps.** A full dump is 280 MB, and the database 1.4 GB. Full dumps fall due weekly, the last on 2026-11-01. `daily/` holds only four tables' worth, which cannot bring a database up.
- **The collector** dropped no poll for longer than 30 minutes in 14 days, outside its nightly five hours of quiet. It takes about 29 readings a minute. A missed reading is dropped, and new posts are caught by the next history window.
- **Runtimes of the passes:** `alerts` 36 s, `score` 5 s, `baselines` 98 s, `backup` 4 s when nothing is due, `yt-watch` 9–13 s. An image builds in about a minute.
- **The infrastructure repository**, read from its code:
  - Its main playbook runs a project's role as root through `sudo`, with pipelining on and no Kubernetes library on the host.
  - Its restore carries a backup from production through the controller to the test host, calls the role's `restore`, runs the main playbook again, and watches the namespace for five minutes.
  - Its monitoring holds scrape jobs, rules and dashboards, and already covers every namespace's failed CronJobs, crash loops and short workloads.
- **The session lease is per database.** It cannot see across environments, so nothing but keeping the session out of test protects the account from a second address.
- **Telethon** builds the client after taking the lease, and logs the route before connecting ([client.py](../../../src/itgraph/tg/client.py)). A proxy that fails raises out of `connect`, with no fallback.

## Goals / Non-Goals

**Goals:**

- A deploy and a restore, each one operator command at a pinned release, in the test environment first.
- Every requirement of the delta specs, with the old path gone and its rules rewritten.
- As little as possible changed in what cannot be tried outside production: `tg/watch.py`, the YouTube jobs, the session.
- A request the operator can implement on the infrastructure repository's side without asking back.

**Non-Goals:**

- People in production's raw layer, and the privacy page's sentence about readers: `remove-person-ids-from-raw-layer`, deployed with this pass's first release.
- Alerting on comments, which needs a different model.
- A Prometheus client library, a Pushgateway, or rules and dashboards in this repository.
- Changing Postgres's major version or its flags.
- More than one replica of anything.

## Decisions

### 1. Layout

```
deploy/
  ansible/
    itgraph/                     the role; the release's archive holds this directory
      meta/argument_specs.yml    entry points main, restore, with their inputs
      defaults/main.yml          configuration tied to the code
      vars/main.yml              the lock's life and clock, the database pod's command prefix
      tasks/main.yml             the deploy (Decisions 3, 11), through inputs, render, apply, password,
                                 quiesce, migrate, rollout, verify, lock, unlock
      tasks/restore.yml          the restore (Decision 12)
      templates/*.yaml.j2        namespace, secrets, config, network-policies, storage, postgres, proxy,
                                 watch, bot, metrics, cronjobs, console, pods (one-shots)
      files/neutralise.sql       Decision 13
      files/readback.sql         Decision 13
    render.yml                   renders the objects to a directory, touching no cluster
    inputs/a.yml, b.yml, e2e.yml synthetic input sets
    e2e.sh                       the role on k3d
  release.sh                     what release.yml runs
  runbook.md                     what remains of deploy/README.md, with no root in it
.github/workflows/image.yml      unchanged but for its comment
.github/workflows/release.yml    new
src/itgraph/metrics/             the exporter
tests/test_deploy_role.py        replaces tests/test_deploy_manifests.py
tests/test_release.py            the release's harness
```

Python tests rather than the shell harnesses a Go project needs: the suite and its Postgres fixture are already here. The checks run in `make test`, so `make validate` covers them; `deploy/ansible/e2e.sh` is `make test-e2e`, outside it.

### 2. Inputs

`meta/argument_specs.yml` declares each with its type. Forms a type cannot say are checked by `tasks/inputs.yml` before anything changes.

| Input | Type | Required | Secret |
|---|---|---|---|
| `itgraph_namespace` | DNS label | yes | |
| `itgraph_storage_class` | str, must have `reclaimPolicy: Retain` | yes | |
| `itgraph_postgres_access_ip` | IPv4 | yes | |
| `itgraph_scraper_namespace`, `itgraph_scraper_labels` | str, dict | yes | |
| `itgraph_image`, `itgraph_proxy_image` | `repo:tag@sha256:…` | yes | |
| `itgraph_postgres_image` | the same, tag of major 16 | yes | |
| `itgraph_telegram_collection` | bool, no default | yes | |
| `itgraph_telegram_api_id`, `itgraph_telegram_api_hash` | int, str | yes | hash |
| `itgraph_bot_token`, `itgraph_alert_chat_id` | str, int | yes | token |
| `itgraph_postgres_password`, `itgraph_bot_postgres_password`, `itgraph_metrics_postgres_password`, `itgraph_proxy_password` | `[A-Za-z0-9]{20,}` | yes | yes |
| `itgraph_proxy_outbound` | dict; `protocol: blackhole` exactly when collection is off | yes | yes |
| `itgraph_youtube_api_key` | str, may be empty; empty means no YouTube job; must be empty where collection is off | yes | yes |
| `itgraph_backup_offsite` | `none` or `b2`, no default; must be `none` where collection is off | yes | |
| `itgraph_b2_key_id`, `itgraph_b2_application_key`, `itgraph_b2_bucket_name`, `itgraph_backup_age_recipient` | str, with `b2` | conditional | key |
| `itgraph_kubectl` | list; default k3s's on the host | no | |
| `itgraph_telegram_api` | URL; default the Bot API's own, a stub in e2e | no | |
| `itgraph_rollout_timeout` | seconds; default 300 | no | |
| `itgraph_restore_from` | path | `restore` | |
| `itgraph_restore_allowed` | bool; default false | `restore` | |

**Tied to the code, in `defaults/main.yml`:** the schedules, `alert_spike_z` (measured against this code's scoring, moved after a replay), `youtube_backfill_enabled`, the database's name and user, the time zone, resources, Postgres's flags, the claims' sizes, ports, the session's path.

**Gone:** `itgraph_image_repository`, `itgraph_commit`, `itgraph_session_src`, `itgraph_dump_src`, `force_restore`, `session_previous_host`, `retire_host`.

### 3. Rendering, applying, order

- **Applying** is `kubectl apply --server-side --field-manager=itgraph -f -` on stdin, with the same manager as today, so the first run on production is an update. Change detection is `kubectl diff --server-side` first: exit 0 is no change, 1 is changed, anything else fails. In check mode only the diff runs. Secrets are rendered with `no_log` and reach `kubectl` on stdin, relying on pipelining.
- **Order of `main`:**
  1. inputs; the namespace, without the Pod Security label; the lock;
  2. refuse a collector scaled to zero by hand where collection is on;
  3. storage class check; coherence checks; decide whether writers must stop (Decision 11); stop them. Before anything is applied: on a first run the new network policies cut pods without the new labels off the database, so what writes has to have stopped by then;
  4. claims, network policies, ConfigMap, Secrets, proxy; the exit check;
  5. Postgres; the owner's password; schema gate and migration; the other roles' passwords;
  6. bot, exporter; the session gate; collector;
  7. CronJobs, and put back the schedules; deletions by name;
  8. settle and verify (Decision 5); the collector-spec pod where collection is off;
  9. the Pod Security label (Decision 11); release the lock; the report.
- **No pruning.** An object that leaves the templates is deleted by name, as today: a label mistake must never be able to reach a claim.

### 4. What a test environment runs

| Piece | In test | Proves | Cannot prove |
|---|---|---|---|
| Postgres | restored data | `restricted`, read-only, fresh volume, restore, migrations | |
| bot | a test bot and chat of its own | read-only root, role grants, delivery, a real write | |
| detect, baselines | real, on the copy | real data under the limits, a stale copy not failing | new data |
| backup | local only | dump, read-back, prune | a real upload |
| exporter | real | series, grants, policy | |
| proxy | real xray, outbound `blackhole` | xray under `restricted`, SOCKS auth, no way out | the VPN outbound |
| watch | zero replicas, and one pod with its spec, `watch --cycles 1` | admission, read-only root, lease, session path, route line, non-zero exit | MTProto, the session, the entity cache, payloads |
| `yt-*` | absent: no key | nothing | everything |

- **Blackhole, not no proxy.** Xray's `blackhole` closes the connection, and the spec's "The VPN exit is down" is then what happens, with no new code. Leaving `PROXY_*` unset instead would mean a hand-run `login` in test goes direct, with the operator's number.
- **The pod's proof.** `connected_with_lease` takes the lease, then logs the route, then connects. So the route line proves the database path, the policy to it, the lease and the settings; the non-zero exit proves failing closed. The pod is bare, not a Job, so that its expected failure raises no shared alert, and the role deletes it. Its credentials are synthetic and non-zero: Telethon refuses an `api_id` of 0 before connecting, which would prove nothing.
- **Telegram's test servers** (Telethon's `set_dc`, `99966…` numbers) were rejected. They need an api id and hash, which are production's, and a new app means a second account. They would also hold none of these channels.
- **Rejected: a stub MTProto server.** Writing one would test the stub.

### 5. The first real trial, and noticing a broken collector

The first real trial is production's first role run. It is kept small on purpose: `tg/watch.py` is untouched, and `tg/client.py` gains one refusal, covered by unit tests. The collector's template changes only by its image, its Secret's split and its labels.

**What the role checks, in production:**

- the route line, and the two addresses differing;
- the session's file present;
- no restart for 60 seconds;
- a poll newer than the collector's start, within five minutes. Inside quiet hours the report says this could not be checked and until when, and the operator reads it at 07:00 (Migration Plan, step 10).

**Afterwards:** `ItgraphCollectionStalled`, and the shared alerts. A refused session exits non-zero, which is a crash loop plus that alert, with the reason in the log. A distinct exit code is left for later, because it would touch the collector's path.

### 6. What the review keeps

- **Workloads:** `itgraph-postgres`, `itgraph-watch`, `itgraph-bot` and `itgraph-proxy` stay. `itgraph-console` goes (Decision 7). `itgraph-metrics` is new (Decision 14). The proxy stays a Deployment: session commands and the collector share one exit, and a sidecar would mean one per consumer.
- **CronJobs:** `itgraph-alerts` and `itgraph-score` become `itgraph-detect`, at `2-59/15 * * * *`.
  - **Sibling containers, not an init container.** `alerts` runs `sh -c 'itgraph derive && itgraph alerts'` and `score` runs beside it. `score` reads no edge, so a failed `derive` must not stop it. The Job fails if either does.
  - `baselines`, `backup` and the three YouTube jobs stay as they are, with their schedules and deadlines. The YouTube pair is not merged because neither can be tried in test.
- **`backup` stays four-hourly.** The reason changes: not a laptop's waking hours, but catching up a failed upload within hours, at 4 s a run. Its comment says so.
- **Postgres's flags** move to role defaults unchanged. Nothing argues against them, at 676 MiB of a 2 GiB limit, and changing them here would blur any regression.

### 7. A Job per command

`scripts/on-host.sh` keeps its interface. It reads the SSH destination from `ITGRAPH_SSH` and the namespace from `ITGRAPH_NAMESPACE`, default `itgraph`, and no longer reads an inventory, which leaves this repository. It reaches the cluster with `sudo -n k3s kubectl`.

- **The template.** The role writes a ConfigMap `itgraph-console` with three Job manifests, none holding a secret: plain, `--session` and `--backups`. Each has `generateName`, `ttlSecondsAfterFinished` and a container running `sleep 14400`.
- **A run:** `create -f - -o name`, `wait --for=condition=Ready`, `exec -i job/<name> -c itgraph -- itgraph <args>` with arguments quoted as today, then delete the Job from a `trap`.
- **A machine that goes away.** The `sleep` ends with exit 0, so the Job completes and is removed, raising no `JobFailed`. The same four hours bound a command: one still running then is cut off, which the runbook says, and `backfill` resumes where it stopped when run again.
- **What each holds:**

  | Variant | Holds | Reaches |
  |---|---|---|
  | plain | database, YouTube key | database, DNS, internet |
  | `--session` | database, Telegram credentials, proxy settings, session volume | database, DNS, proxy; no internet |
  | `--backups` | database, backups volume | database, DNS |

- **A Job and not a bare pod,** for the TTL. `kubectl exec` carries standard input, which a Job's own attach would need extra configuration for.
- **Rejected:** a Deployment scaled by the script (two concurrent commands collide, and two operators exist), and keeping the sleeping pod (the session and every secret mounted around the clock).
- **The role's own one-shots** (migration, schema check, session gate, exit checks, the collector-spec pod) are bare pods of the role's templates, deleted when read, each waiting three seconds in an init container: the cluster's network policies reach a new pod a moment after it starts, and until then the database and the proxy refuse it, measured at about 0.3 s on k3d. A `curl` would read that as a broken proxy. The passes on a schedule take longer than that to reach their first connection, and ran for days under the same condition without one such failure, so they do not wait. Python in a pod is safe to `exec`, but these no longer need a door, and nothing execs into xray, a Go binary, which trips the server's AppArmor check.

### 8. Postgres under `restricted`

```yaml
securityContext: {runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, fsGroup: 999, seccompProfile: {type: RuntimeDefault}}
container securityContext: {readOnlyRootFilesystem: true, allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
emptyDir at /var/run/postgresql, /tmp; memory-backed at /dev/shm, 256 MiB; the claim at /var/lib/postgresql/data
terminationGracePeriodSeconds: 120
```

- **No step as the host's root.** What the volume holds is already right, unlike the earlier projects' volumes. A wrong owner would fail readiness, which fails the run in the test rehearsal first (Migration Plan, step 7).
- **`trust` on local connections stays.** The file is on the existing volume, and leaving `POSTGRES_INITDB_ARGS` unset gives a fresh volume the same, so one path serves both. So:
  - `exec` needs no password;
  - `ALTER ROLE` needs no old password;
  - a new password does not restart the database.
  Whoever can `exec` into the pod is a cluster administrator already.
- **Readiness is asked over TCP,** on the pod's own loopback. While the image initializes a fresh volume a temporary server listens on the socket alone, and a probe of the socket called that ready: on k3d a migration then met a refused connection.
- **`/dev/shm`** stays: dynamic shared memory is POSIX, and the runtime's default of 64 MiB is what the mount was added to avoid.
- **A database that does not come up is not put back by the run.** A StatefulSet's `rollout undo` leaves a pod stuck on the bad template in place, and what is wrong with a database is for a person to read before anything else touches it. The run fails and points at the runbook. Until the database answers, the next run stops at reading its state, by "Unknown Database State Stops The Deploy". Its restarts are watched in the settling minute like any other workload's.
- **The grace period** is a proposal, not a measurement: 512 MiB of buffers under the default 30 s risks a kill and a crash recovery.
- **Passwords.** The role compares each role's password in its Secret with the input, under `no_log`, and runs `ALTER ROLE … LOGIN PASSWORD` over stdin only when they differ or the Secret is new.
- **The exporter's role and the bot's** are created by migrations, `NOLOGIN`, as the bot's already is, and given a password after them.

### 9. Each workload's secrets

| Object | Keys | Read by |
|---|---|---|
| ConfigMap `itgraph-config` | `TZ`, `WATCH_TIMEZONE`, `ALERT_SPIKE_Z`, `BACKUP_DIR`, `POSTGRES_CONTAINER`, `TELEGRAM_SESSION`, `YOUTUBE_BACKFILL_ENABLED` | all of the application's |
| Secret `itgraph-database` | `DATABASE_URL` (the owner) | watch, detect, baselines, backup, YouTube jobs, console |
| Secret `itgraph-telegram` | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `PROXY_*` | watch, `--session` |
| Secret `itgraph-bot` | `DATABASE_URL` (the bot's role), `TELEGRAM_BOT_TOKEN`, `ALERT_CHAT_ID` | bot |
| Secret `itgraph-metrics` | `DATABASE_URL` (the exporter's role) | exporter |
| Secret `itgraph-offsite` | `B2_*`, `BACKUP_AGE_RECIPIENT` | backup |
| Secret `itgraph-youtube` | `YOUTUBE_API_KEY` | YouTube jobs, plain console |
| Secret `itgraph-postgres`, `itgraph-proxy` | as today | Postgres, xray |

- **Restarts.** A Deployment's template carries a checksum over the keys it reads, and only those. A CronJob's pods read afresh.
- **Settings.** `telegram_api_id` and `telegram_api_hash` become optional, and `build_client` refuses when they are absent, naming them. This reverses `move-to-k3s-host`'s "out of scope".
- **`DATABASE_URL` stays required.** The bot's and the exporter's pods set it to their own role's URL, and `BOT_DATABASE_URL` stays for a shared `.env` on a developer's machine. This is smaller than making it optional, which the explore proposed: every process reads one, and no pod holds two.

### 10. Network policies

Everything is denied both ways first. Labels say what a pod may reach, each opened by one policy:

| Label | Egress |
|---|---|
| none | DNS |
| `itgraph/database: "true"` | the database on 5432 |
| `itgraph/telegram: "true"` | the proxy on 1080 |
| `itgraph/internet: "true"` | any public address, private ranges excepted |

| Workload | Labels |
|---|---|
| watch | database, telegram |
| bot | database, internet |
| exporter | database; ingress from the scraper on 9090 |
| detect, baselines | database |
| backup | database, internet |
| YouTube jobs | database, internet |
| proxy | internet; ingress from `telegram` on 1080 |
| Postgres | none; ingress from `database` on 5432 |

- The node is admitted whatever the policies, which is what lets a tunnel reach the fixed address. e2e proves that for k3s's controller.
- The exit check runs two pods: one with `internet` for the direct reading, one with `telegram` for the proxied one. The collector's own label cannot leave directly, and e2e proves it from that pod.

### 11. Taking over, stopping writers, the label

- **Takeover.** Names, selectors, claims and `serviceName` stay. `itgraph-postgres` stays a ClusterIP Service, since `DATABASE_URL` names it, and `itgraph-postgres-access` is new, at the fixed address.
- **Stopping writers** happens when the Postgres stream's diff shows a change, the schema is behind, or the owner's password changes, since everything that writes connects as the owner. Another role's password reaches the one workload that reads it, and its template's checksum restarts that workload alone. The role records what was suspended, suspends the CronJobs, waits up to ten minutes for Jobs to end, an operator's console Job aside, and scales `watch` and `bot` to 0, letting `watch` finish its poll and release the lease (grace 90 s). A later apply of `replicas` uses `--force-conflicts`, the run having refused a hand-stopped collector at its start. An `always` block puts the schedules back as found.
- **Putting a workload back** applies only to a Deployment whose revision this run moved. One it only stopped and scaled up again has no new template, and `rollout undo` would take it to an older one, on a schema that may be newer.
- **The label comes last,** through `kubectl label`, so that server-side apply, which owns no such field, can never remove it. Any `Warning` in its output fails the run. On the first run a failure before this point leaves the old root template able to start again.
- **A killed run** can leave the collector at 0, and an annotation, `itgraph/stopped-by-run`, tells that from an operator's `--loop stop`, so the next run starts it. The runbook covers deleting the lock, and resuming schedules by hand if they were on before.
- **The lock** is a Lease `itgraph-deploy`, life 60 minutes, polling every 10 seconds, with the clock in a variable so that a test can fix its boundary. Restore takes the same one.

### 12. Restore

`tasks/restore.yml`, given `itgraph_restore_from`:

1. Refuse unless `itgraph_restore_allowed` and collection is off, and in check mode.
2. `pg_restore --list` inside Postgres's pod, with the file on stdin; refuse otherwise, naming the path.
3. Take the lock. Note and suspend the schedules, wait for Jobs. Scale the bot to 0.
4. Set a fact that the database holds un-neutralised data. Inside the pod, on one line or a literal block, never a folded one: `dropdb --force itgraph`, `createdb -O itgraph itgraph`, `pg_restore --exit-on-error --no-owner -d itgraph` with the file on stdin.
5. Count: rows of each COPY block in `pg_restore --data-only -f -` against `count(*)`, and every table of `EXCLUDED_DATA` empty. Any difference fails, naming the table. A test holds the role's list equal to [backup.py](../../../src/itgraph/db/backup.py)'s.
6. A migration pod of the deployed image.
7. `files/neutralise.sql` in one transaction, then `VACUUM FULL` of every table, since deleting a channel or a mention cascades into tables the script never names, then `files/readback.sql`, which fails on any remaining value and prints the counts.
8. Start the bot, wait for its rollout, put the schedules back, and clear the fact.
9. Try the real writes: a Job from `itgraph-baselines`, then `backup --full`, each read back by rows; a synthetic alert on a restored post, its `delivered_at` read back, or the report saying "held" in quiet hours.
10. Release the lock and print the table counts.

A failure from step 4 until step 7 has finished drops and re-creates the database, so that nothing un-neutralised stays. Whatever the failure, the schedules are put back and the lock released. The session's claim is not read.

**The source's shape** (Decision 16, item 8). A full dump from `weekly/` in the claim, the newest by name, and the same dumps from B2 as `full/full-….dump.age`. The role knows nothing of B2: a decrypted copy is a dump file like any other. Dumps keep their layout, since moving them would touch pruning, `LATEST` and the real volume.

**Rejected:** treating `weekly` as a directory backup, which would carry 1.1 GB through the controller every time.

### 13. Neutralising

`files/neutralise.sql`, one transaction:

| Where | What |
|---|---|
| `channels` | delete rows from `own_subscriptions` whose status is not `seed`; relabel the rest `manual`; null `kind_note`, `reject_note` |
| `yt_channels` | null `kind_note`, `reject_note` |
| `affiliation_candidates` | null `decision_note`; the decision stays |
| `pending_mention_sources`, `pending_mentions` | delete: they are handles, many of them people's, and only `resolve` reads them |
| `alerts` | mark every undelivered one delivered |

- **Payloads are not neutralised here.** A list of paths missed the paid reactors, 6,341 people on production. What names a person is removed by the migration of `remove-person-ids-from-raw-layer`, which every restore runs before this file, so a dump taken before that revision arrives here cleared. One taken after it that names someone means production does too, and the restore fails rather than hide it.
- **The read-back looks by type,** for every type and field that change's rule names, anywhere in `raw_messages` and `raw_channels`, and fails naming what it found. A forward from a channel keeps its `from_id`, which is the graph; text stays, since these are public posts.
- **Proof.** `files/readback.sql` counts on the real rows and fails above zero. e2e seeds synthetic rows of every shape, and each statement removed in turn must fail the read-back: an empty table proves nothing.
- **Not cleared:** old values in WAL segments kept for reuse on the test VM. Only the two operators can reach them, and they can reach production anyway. This limit is stated in the runbook. `wal_recycle=off` would change a production flag for it, and is not taken.
- **A column named as a note** fails a test until the restore clears and reads it back, which is how the suite keeps up with the schema here. Nothing can do the same for a field naming a person, so:
- **A change that adds a field naming a person** names the restore in its design. The read-back by type catches a tagged one by itself.

### 14. The exporter

`itgraph metrics` serves `/metrics` on 9090 through `asyncio.start_server`, with no web framework and no new dependency, writing the text format by hand as a small module. Each scrape reads through the role `itgraph_metrics`; the interval is the shared Prometheus's 30 seconds.

- **Why a process of its own.** It measures a stopped collector the same as a working one, a bug in it cannot stop delivery, and it holds one low-privilege URL. Inside the bot it would widen the role that bounds a leaked token. Counters inside `watch` and `bot` would change code that cannot be tried, and one-shot jobs would have nowhere to push.
- **The role:** a migration creates it `NOLOGIN` and grants `SELECT` on `poll_state`, `flood_events`, `yt_quota_days`, `yt_quota_exhausted`, `yt_watch_passes` and `alerts`, what `in_scope()` joins, and only the fetch-time column of each `yt_raw_*` table, by column-level grant.
- **Quiet hours** come from settings. `queue_lag` is reused as it stands.
- **Rejected:** a third-party SQL exporter. Another pin, with the logic in YAML outside the test suite.

Series: `specs/collection-monitoring`.

### 15. Release and tests

- **`release.sh <tag>`:**
  1. Refuse a tag not `vX.Y.Z`, or a commit not on `origin/master`.
  2. Wait up to 10 minutes for `ghcr.io/vyhuholl/it-media-graph:<sha>`.
  3. Tag it with the version by `docker buildx imagetools create`, and check the digests are equal.
  4. Pack with `git archive <sha> itgraph`, run from `deploy/ansible`, then `gzip -n`.
  5. Write `SHA256SUMS`, and run `gh release create` from the repository, since `gh` finds it through git.
- **`release.yml`:** on `v*`, a job running the suite against a Postgres 16 service and the role's checks, then one with `contents: write` and `packages: write` running the script. Actions pinned by commit, as in `image.yml`, and no secret but `GITHUB_TOKEN`.
- **`image.yml`** still gates nothing, and a push to `master` runs no test. The root `CLAUDE.md` says what changed: a tag is gated, a push is not.
- **The harness.** `tests/test_release.py` runs the script in a throwaway repository against stubs of `docker` and `gh`, and the `gh` stub refuses outside a repository, as the real one does.
- **`tests/test_deploy_role.py`** renders the input sets through `render.yml` and keeps today's rules, adding:
  - every container read-only;
  - each workload's keys and labels (Decisions 9, 10);
  - no value of set A in set B's objects;
  - the excluded tables equal to `EXCLUDED_DATA`.
  It also runs the role against a stub `kubectl` that records its arguments: every refused input before any change, no secret in any argument or at `-vvv`, a password on stdin, the lock's boundary, the refusal of a hand-stopped collector.
- **`e2e.sh`** on k3d, at the server's k3s version, with a stub Bot API in the cluster reached through `itgraph_telegram_api` and an extra policy only the suite applies:
  - `restricted` admission and read-only on a fresh volume, and on one first written by the image started as root;
  - the takeover: today's templates, then the role;
  - the failing collector pod;
  - egress from each label, and the fixed address from a host-network pod;
  - a second run with unchanged pod uids; a new bot token restarting the bot alone;
  - two concurrent runs;
  - `restore`, on seeded rows of every shape, with mutation checks; a copy through `age` and back; a dump cut short; a stale copy's passes exiting zero.

### 16. The request to the infrastructure repository

Handed to the operator as it stands, once this design is accepted. Nothing here names that repository or holds a real value.

1. **A flag** of its own: on in test, off in production until the Migration Plan's step 9. It spares pins, vault, argument validation, checks and monitoring wherever it is off.
2. **Pins**, test first, each moved on its own:
   - the project: its tag; `itgraph-ansible.tar.gz` by the release's `SHA256SUMS`; image `ghcr.io/vyhuholl/it-media-graph`, as `repo:tag@digest`;
   - the database: `docker.io/library/postgres`, a tag of major 16, because the application image's client is 16;
   - the proxy: `ghcr.io/xtls/xray-core`, tag `26.10.21`.
3. **A public source.** The repository and the image are public, so no registry credential is needed, and the role's fetch and the pin command must treat the GitHub token as optional. Today both always send one.
4. **Running the role:** the main playbook fetches the archive, checks it, and runs `itgraph` with `tasks_from: main` under a tag of its own, before the monitoring, behind the flag.
5. **Inputs per environment** (Decision 2):

   | Input | Production | Test |
   |---|---|---|
   | `itgraph_namespace` | `itgraph` | `itgraph` |
   | `itgraph_storage_class` | the retaining class | the retaining class |
   | `itgraph_postgres_access_ip` | next free in the band for fixed addresses | the same |
   | `itgraph_scraper_namespace`, `_labels` | as for the other projects | the same |
   | `itgraph_telegram_collection` | `true` | `false` |
   | `itgraph_proxy_outbound` | vault | `{protocol: blackhole}`, in group vars |
   | `itgraph_telegram_api_id`, `_api_hash` | vault | synthetic: a non-zero id, `test-api-hash` |
   | `itgraph_bot_token`, `itgraph_alert_chat_id` | vault | a test bot from BotFather; a test group the operator alone reads |
   | `itgraph_youtube_api_key` | vault | empty |
   | `itgraph_backup_offsite` | `b2`, with the key, its id, the bucket's name and the public recipient | `none` |
   | `itgraph_restore_allowed` | absent | `true` |
   | the four passwords | seed, 64 hexadecimal characters | seed |

6. **Vault entries**, one per environment, as the table says. A read key for the restore in production's vault: `listFiles` and `readFiles` on the one bucket, with the bucket's id.
7. **The fixed address:** the next free one in the database band, read by the checks too.
8. **Restore catalog,** two entries, role `itgraph`, namespace as above, with the release:
   - `itgraph`: a claim source, claim `itgraph-backups`, with a new key naming a subdirectory, `weekly`, and pattern `^full-[0-9]{8}T[0-9]{6}Z[.]dump$`. Today a claim is read at its top level alone.
   - `itgraph-offsite`: a B2 source with pattern `^full/full-[0-9]{8}T[0-9]{6}Z[.]dump[.]age$`. A name holding a slash has no parent directory where the restore writes it today, in both the download and the copy; the restore must keep a base name, or take a prefix.
9. **The restore's five-minute watch** reads every pod that has not succeeded. So a CronJob's pod starting at its second read is a false failure, about 4% of the time at 40 s every 15 minutes, and any old Failed Job pod fails it outright. Please read only pods no Job owns.
10. **The project check** assumes a health route, which this project does not have: make it optional. Workloads: `statefulset/itgraph-postgres`, `deployment/itgraph-bot`, `deployment/itgraph-proxy`, `deployment/itgraph-metrics`, `deployment/itgraph-watch`. Since `rollout status` passes at zero replicas, assert the collector at 1 in production and 0 in test. Images: every CronJob and the application's Deployments at the project's pin. Add the scrape job and dashboard to the monitoring's lists.
11. **Scrape job:** `itgraph-metrics.itgraph.svc:9090`.
12. **Rules,** messages in Russian, each tried with `promtool test rules`, firing and silent. Thresholds are proposed; changing them changes nothing here.

    | Alert | Expression | For | Severity |
    |---|---|---|---|
    | `ItgraphCollectionStalled` | `time() - itgraph_telegram_last_poll_timestamp_seconds > 1800 and on () itgraph_telegram_quiet_hours == 0 and on () kube_deployment_spec_replicas{namespace="itgraph",deployment="itgraph-watch"} == 1` | 15m | critical |
    | `ItgraphCollectorStopped`, production only | `kube_deployment_spec_replicas{namespace="itgraph",deployment="itgraph-watch"} == 0` | 24h | warning |
    | `ItgraphQueueBehind` | `itgraph_telegram_oldest_overdue_seconds > 1800 and on () itgraph_telegram_quiet_hours == 0` | 30m | warning |
    | `ItgraphFloodWait` | `time() - itgraph_telegram_last_flood_timestamp_seconds < 3600` | | warning |
    | `ItgraphYoutubeNotPassing` | `time() - itgraph_youtube_last_complete_pass_timestamp_seconds > 3600 and on () kube_cronjob_info{namespace="itgraph",cronjob="itgraph-yt-watch"}` | 15m | critical |
    | `ItgraphYoutubeQuotaHigh` | `itgraph_youtube_quota_spent_units{pool="watch"} / itgraph_youtube_quota_cap_units{pool="watch"} > 0.9` | | warning |
    | `ItgraphYoutubeQuotaExhausted` | `itgraph_youtube_quota_exhausted == 1` | | warning |
    | `ItgraphYoutubeRefreshBehind` | `itgraph_youtube_oldest_payload_age_seconds{kind="channel"} > 2332800` (27 days: a channel's payload is fetched again before 25, so this says the refresh has fallen behind) | 1h | warning |
    | `ItgraphAlertsFailing` | `itgraph_alerts_failing > 0` | 15m | warning |
    | `ItgraphAlertsStuck` | `itgraph_alerts_oldest_undelivered_seconds > 93600` (26 hours: the 09:00 digest normally clears what is held) | | critical |

    The project's own alerts in its chat are a product, and none of these.
13. **Dashboard «itgraph»:** polls and overdue channels, failing channels, floods, quota by pool, the last complete pass, the oldest payload's age, undelivered alerts, each CronJob's last success, the namespace's restarts.
14. **Still relied on:** a storage class that retains; `k3s kubectl` on the host; pipelining; `age` on the controller; the Prometheus pods' labels; pods reaching the internet where the network policy allows; room on the test VM, about 2 GiB of memory at the limits and 3 GB of disk.
15. **Housekeeping:** an item saying a B2 503 is not retried is stale, as the fix is running. This project's item closes after the Migration Plan's step 14.

### 17. Retirement

The old path stays in `HEAD` until the soak ends: nothing can run it, as root's login is closed, and it is the last readable record of how the namespace was made. The commit removing it takes `deploy/playbook.yml`, `deploy/templates/`, `deploy/decommission.yml`, `deploy/group_vars/`, `deploy/inventory.ini.example`, `deploy/.ansible-lint-vault-pass`, `deploy/README.md`, `scripts/assert-vault-encrypted.sh`, `scripts/backup-agent.sh`, `ansible.cfg`'s `vault_password_file`, the two vault hooks and the `backup-*` targets. `docker-compose.yaml` stays, for tests and scratch migrations.

## Cost while idle

- **Money:** nothing beyond the server's fixed price. A release takes a few minutes of Actions, free on a public repository.
- **Work:**
  - one read-only scrape every 30 seconds, over small or indexed tables;
  - minus a sleeping pod, one Deployment replaced by another;
  - one CronJob's pod fewer every 15 minutes;
  - the test environment runs a second database and bot, about 2 GiB at the limits.
- **What removes it:** deleting the namespace in each environment, and the infrastructure repository removing the pins, vault entries, catalog entries, scrape job, rules and dashboard. The retained volumes are deleted by hand, on request.

## Risks / Trade-offs

- **[The first production run of the collector's new image finds something test could not]** → `tg/watch.py` is unchanged, the role puts the previous template back on a failed rollout, and the session is not touched in any path. Reverting the pin restores the old image.
- **[A session in a test environment]** would be two addresses on one authorization, and the lease cannot see across databases → no task writes one, no catalog entry names its claim, the outbound closes every connection, egress is denied, and restore refuses where collection is on. A hand-copied session is the one thing nothing can stop; the runbook forbids it.
- **[Egress rules break what they should not]** Google's and B2's addresses are public, so `internet` covers them → each is tried where it can be, and a YouTube job failing after the first production run is read at once (Migration Plan, step 10).
- **[A restore run against production]** would destroy data that cannot be rebuilt → two refusals in the role, and the infrastructure repository's own refusal.
- **[A restored copy between load and neutralise]** A run killed outright runs no rescue, and the copy holds real ids → the runbook says to run the restore again to completion. Both operators already reach production's.
- **[The dump crosses the controller and the test host in plain]** → the restore removes both copies at its end. It holds no API data.
- **[A single YouTube timeout raises a critical alert]** for up to ten minutes, about once in five days → accepted, by decision; read the noise for a week. The code that would change cannot be tried in test.
- **[`ItgraphCollectionStalled` during a session command]** → the rule looks at replicas, so a loop stopped by hand does not fire it, and `ItgraphCollectorStopped` says so after a day.
- **[Neutralising falls behind the schema]** A later change adds a field naming a person → the read-back lives beside the schema; a change adding such data names the restore in its design.
- **[The privacy page's sentence about readers stays wrong until production runs the migration]** → `remove-person-ids-from-raw-layer` changes the page once it has, since the page is published from `master`.
- **[The vault's ciphertext stays in a public history]** → the bot token, the YouTube key and the B2 key are rotated; the database and SOCKS passwords are replaced. The api hash and the VPN credential remain as they were in that ciphertext. The password protects them, and is deleted everywhere at the end.
- **[A lost age identity]** makes every off-site copy unreadable → it stays in the password manager, and the off-site restore proves it opens.

## Migration Plan

Who does what, in order. **Irreversible steps are in bold.**

1. **You:** answers recorded (proposal, Decision record).
2. **Agent:** this change implemented; `make validate` green; `make test-e2e` green.
3. **Operator:** push `master`, wait for the image, tag `v0.1.0`. **A published release cannot be changed, so a bad one spends a version.** `tests/test_release.py` tries the whole path first.
4. **Operator:** the request, behind the flag. Production's `--check --diff` shows no change.
5. **Operator:** test environment. A test bot from BotFather; pins; the main playbook, on a fresh volume.
6. **Operator:** `restore` from production's newest full dump; the same from the off-site copy, the age identity pasted at the prompt. Read the neutralised counts and the five-minute watch. Fire one rule on purpose to the test group. See the test bot deliver the synthetic alert.
7. **Operator:** rehearse the takeover in test: delete the namespace's workloads, apply today's templates with a root-started Postgres on a loaded dump, then run the role on top. Measure the gap.
8. **Operator:** on the controller, move the values to production's vault, printing none. Snapshot the namespace's non-Secret objects there.
9. **Operator:** at about 06:15 Moscow time, inside quiet hours: promote the pins, flag on, run the main playbook. The role stops writers, restarts Postgres as uid 999, migrates (which takes a full dump first, then runs this pass's revision and the one that clears people from the raw layer), brings up the bot, the exporter and the collector, deletes the console, the two merged CronJobs and the old Secret, and applies the label.
10. **Operator:** verify at about 07:05, when polls have resumed: `itgraph_telegram_last_poll_timestamp_seconds` advancing, a YouTube pass complete, the route line, the tunnel to the fixed address, the series scraped.
11. **Operator: rotate the bot token, the YouTube key and the B2 key.** **The old values are revoked for good.** Keep the session and the api hash. Reissue the VPN credential only if the provider can do it on the same server.
12. **Operator:** soak. The next backup with its off-site copy; one restore of a dump taken under the role; one off-site restore.
13. **You, then the agent:** `make backup-uninstall` on your machine, while the target exists; then the commit removing the old path (Decision 17).
14. **You and the second operator: delete from every machine** the vault password, `deploy/.vault_pass`, `deploy/inventory.ini`, the old copy of the session in the backups directory, and old dumps; delete the vault password from the password manager. **These deletions are final.** Keep the age identity: without it every off-site copy is unreadable.

**The gap:** writers stopped to the collector's first poll, estimated at five minutes, to be measured in step 7. Inside quiet hours it costs no reading. If it ran into the day, about 29 readings a minute would be dropped, and no post lost.

**Rollback:**

- Before step 9, production is untouched.
- During step 9: revert the pins and run again. If the role cannot run, apply the snapshot by hand with `sudo k3s kubectl`, after removing the namespace's label if it was applied.
- Postgres as uid 999 on these files can go back to the root-started template: nothing on the volume changes owner.
- Password changes are undone from the old vault, until step 14.
- The session is never written, so no failure involves it.
- The old playbook is not a way back: root's login is closed.

## Open Questions

- **Alert thresholds** in Decision 16, item 12 are proposed; the operators may change them on the infrastructure repository's side, and nothing here changes.
- **The YouTube key's address restriction,** if it has one, is re-applied to the new key in step 11. Whether it has one is visible in the Google console, not from here.
