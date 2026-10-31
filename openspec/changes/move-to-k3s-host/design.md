## Context

See [proposal.md](proposal.md), "Why". The requirements are in [specs/collection-deploy/spec.md](specs/collection-deploy/spec.md), [specs/database-backups/spec.md](specs/database-backups/spec.md) and [specs/youtube-inventory/spec.md](specs/youtube-inventory/spec.md). What shapes the approach:

- **The new host is a single-node k3s that itgraph does not own.** Its firewall, packages, time zone and swap are managed elsewhere and must not be touched, which is why the current playbook's ufw, Docker and swap steps cannot run there. Workloads live in namespaces; volumes come from k3s's local-path provisioner, and a storage class that retains a volume after its claim is deleted exists for data that cannot be rebuilt. The k3s API is not exposed: everything that drives the cluster logs in over SSH as root and runs `k3s kubectl`. The host's time zone is not Moscow and stays that way.
- **What runs on the laptop today** (measured 2026-10-26): `itgraph-watch` 105 MB resident, 37 s of CPU in 16 hours; `itgraph-bot` 212 MB, 22 s; Postgres 615 MB, of which 512 MB is `shared_buffers`; the database 1.26 GB; `derive && alerts` about 18 s of CPU per 15-minute run, `baselines` about 15 s a day. Dumps: an inventory dump is about 160 KB, a full dump about 275 MB; four full and thirty inventory dumps take 1.1 GB.
- **Dumps run through Docker.** [backup.py](../../../src/itgraph/db/backup.py) builds every `pg_dump`, `pg_restore --list` and `psql` as `docker exec -i itgraph-postgres …`, and [env.py](../../../src/itgraph/db/migrations/env.py) takes a full dump through the same code before every `upgrade`. The operator's Mac still runs Postgres in Compose and uses this path.
- **The collector reaches Telegram through `PROXY_*` or not at all.** [client.py](../../../src/itgraph/tg/client.py) builds every MTProto connection; a configured proxy that cannot be reached fails the command, and the route is logged once per connection as `Telegram route: …`. The laptop sets no proxy because the whole machine is behind the VPN. `docs/PLAN.md` ("Collection") requires a shared exit on a rented machine, and the operator confirmed it: the commercial VPN issues a v2ray-family client configuration, static, with up to ten devices.
- **The session lease is per database.** [session_lease.py](../../../src/itgraph/db/session_lease.py) is a Postgres advisory lock. The laptop and the new host have different databases, so the lease cannot stop both from using the session at once — and two addresses using one authorization key at the same moment is what makes Telegram revoke it.
- **The operator works from the Mac, behind a VPN in TUN mode.** Manual work goes through `scripts/on-host.sh`; direct work on a host is slow and error-prone.

## Goals / Non-Goals

**Goals:**

- The full playbook remains the one command for a fresh host and for a live one, with the safety properties `collection-deploy` already states, now in terms of workloads.
- Nothing outside the namespace changes, and nothing in the public repository names the host, its address or anything else running on it. Host-specific values live in the git-ignored inventory.
- The cutover costs one short collection gap and cannot run the session from two places.

**Non-Goals:**

- Keeping the systemd deploy as an alternative target. It is removed, together with the laptop-only pieces (sleep masking, the logind drop-in, swap).
- Helm, kustomize or the `kubernetes.core` collection. See Decision 3.
- Alerting when collection stops. A crash-looping collector is caught by the deploy (Decision 12) and by `watch-status`; an unattended alert on it is a change of its own.
- Narrowing what the bot's pod can see. Settings require the Telegram API credentials at load (proposal, out of scope).

## Decisions

### 1. One namespace, and what becomes what

```
 laptop (systemd + Compose)            namespace itgraph (k3s)
 ──────────────────────────            ───────────────────────────────────────────────
 postgres container  ───────────────▶  StatefulSet itgraph-postgres  + Service + PVC (retain)
 itgraph-watch.service ─────────────▶  Deployment itgraph-watch      (Recreate)  ─┐ socks5
 itgraph-bot.service  ──────────────▶  Deployment itgraph-bot        (Recreate)   │
 (ssh + uv run, by hand) ───────────▶  Deployment itgraph-console    (idle)      ─┤
 host-wide VPN ─────────────────────▶  Deployment itgraph-proxy + Service  ◀─────┘ ──▶ VPN exit ──▶ Telegram
 6 timers ──────────────────────────▶  6 CronJobs, timeZone Europe/Moscow
 ~/itgraph-backups ─────────────────▶  PVC itgraph-backups (retain)  ──age──▶ B2 bucket
 ~/itgraph/itgraph.session ─────────▶  PVC itgraph-session (retain)
 .env ──────────────────────────────▶  Secret itgraph-env (+ itgraph-proxy, itgraph-postgres)
```

The namespace is `itgraph`. The storage class for the three volumes is `itgraph_storage_class`, set in the git-ignored inventory, because its name is part of the host's configuration rather than of this project. All three volumes need a class that retains data: the database and the session cannot be rebuilt, and the dumps are what rebuilds the database. `prepare` asks the cluster for the class's `reclaimPolicy` before making any claim and refuses anything but `Retain` — the first deploy was nearly made on k3s's own `local-path`, which deletes a released volume, and nothing else would have said so.

### 2. The image, built by GitHub Actions

A multi-stage `Dockerfile`: a builder stage on uv's image runs `uv sync --locked --no-dev --no-editable` with `UV_COMPILE_BYTECODE=1` and `UV_PYTHON_DOWNLOADS=0`; the runtime stage is `python:<.python-version>-slim-trixie` with the virtual environment copied in, plus the PostgreSQL 16 client from the PGDG repository, `age`, `curl` and `tzdata`. Both base images are pinned to the patch version in `.python-version` and by digest: uv with downloads disabled refuses an interpreter whose version differs from the pin, and the builder's and runtime's interpreter paths must be the same.

- **Client 16, not Debian's own.** The server is `postgres:16`; a `pg_dump` older than the server refuses, and a newer one writes archives the matching `pg_restore` is not guaranteed to read. Matching majors removes the question.
- **`.dockerignore` is an allowlist** — `pyproject.toml`, `uv.lock`, `.python-version`, `alembic.ini`, `src/` and whatever the build backend needs — so a build from a working tree with `.env`, a session or `deploy/.vault_pass` in it cannot pick them up either.
- **The process runs the environment's `itgraph` entry point**, declared as the image's `ENTRYPOINT` so that a manifest's `args: ["watch"]` means `itgraph watch` — not `uv run`: the runtime image has no uv, and the filesystem is read-only. Dependencies still come only from `uv.lock` through `uv sync`, which is what the "always `uv run`, never pip" rule protects. *Not covered by the requirements* — the rule is written for the development environment, and this reading needs the operator's confirmation.
- **The build checks itself** before pushing: `itgraph --help` runs, `pg_dump --version` says 16, `TZ=Europe/Moscow date +%Z` says MSK, `age` and `curl` exist.
- **Non-root** (a fixed UID with no shell login), `PYTHONUNBUFFERED=1`, working directory holding `alembic.ini` so `alembic` finds its scripts.

The workflow, `.github/workflows/image.yml`, runs on every push to `master`: build for `linux/amd64`, push `ghcr.io/vyhuholl/it-media-graph:<full commit sha>`. Actions are pinned by commit SHA; the job has `contents: read` and `packages: write` and nothing else. It runs no tests: pre-commit stays the gate, and the root `CLAUDE.md` says so instead of "There is no CI". The package is made public once, by hand, so the host pulls without a credential.

Alternatives: building on the Mac and importing over SSH (every deploy pushes hundreds of megabytes through the VPN, the build context is the working tree, a garbage-collected image cannot be rolled back to); building on the host (needs a builder installed there, which is the host's business).

### 3. The playbook drives `k3s kubectl` over SSH as root

Manifests are Jinja templates under `deploy/templates/k8s/`, rendered by Ansible and piped to `k3s kubectl apply --server-side --field-manager=itgraph -f -`. Reads go through `k3s kubectl get/exec/logs/rollout`. Nothing is pruned automatically: an object that leaves the templates is deleted by an explicit task, because pruning is one label mistake away from deleting a volume claim.

Alternatives: `kubernetes.core` needs the Python Kubernetes client on the host and a kubeconfig; Helm adds a second templating language and release state; kustomize does not template secrets. `k3s kubectl` is already on the host and is how the host is driven anyway.

The inventory changes shape: `[collector]` is the k3s host with `ansible_user=root` and `itgraph_storage_class`; a new `[previous]` group names the host the session comes from (Decision 10). `deploy/inventory.ini.example` uses RFC 5737 addresses.

### 4. The deployed code is an image digest

The playbook resolves the commit (`itgraph_commit`, default the tip of `master` from `git ls-remote` on the controller), polls ghcr anonymously for `:<sha>` for up to 20 minutes, and resolves the tag to a digest. Every itgraph workload is rendered with `image: ghcr.io/vyhuholl/it-media-graph@sha256:…` and an annotation naming the commit. The resolution is tagged `always`, so every tag subset agrees on the image, and the report names both.

### 5. Order of a full run

```
prepare   namespace, NetworkPolicies, PVCs, Secrets ─ proxy ─ console (new image) ─ exit check
data      postgres ─ wait ready ─ probes ─ [restore ─ coherence] ─ bot role ─ schema check ─ [migrate] ─ [first baselines]
session   volume probe ─ [previous host stopped? ─ copy ─ checksum]
services  gate: schema at head ─ watch, bot, CronJobs (new image) ─ rollout ─ settle
verify    seeds ─ route line ─ exit check ─ watch-status ─ report
```

Probes, the restore and the bot role run in the Postgres pod through `kubectl exec … psql` over its local socket, as they did through `docker exec`: the same SQL, the same failure handling, the same conditions as today's tasks. The migration and the schema checks run in the console (Decision 7), which is rolled to the new image under every tag that changes something through it — `prepare`, `data`, `migrate`, `session`, and `services`, whose schema gate must ask the deployed image's head and not the previous one's — but not under `verify`, which must not move it: it is a tool, not a collector, and nothing it does unattended touches collection state.

**The collector, the bot and the CronJobs get the new image only in `services`, after the gate.** Under systemd, timers fired new code on an old schema between the checkout and the migration (the accepted window in `deploy/README.md`). Here a CronJob keeps its old template until it is re-applied after the migration, so the window is gone, and the spec says so ("Scheduled jobs wait for the schema").

Tags keep their meaning: `prepare` (new), `data`, `migrate`, `session`, `services`, `config` (the Secrets and the proxy), `verify`; `repo` and `base` go away, `laptop` with them. `--tags prepare` is the part `collection-deploy` asks for to set up a host without collecting.

The dump and the session travel through root-only temporary files on the host (`/root/.itgraph-transfer`, 0700) — not through Ansible's `stdin`, which carries text and would corrupt either. They are sent with `copy` over the SSH connection, not `synchronize`: rsync would have to be on the host, and the deploy installs nothing there. The directory is created under every tag and removed by the last task of every run; the dump, the session and the rendered Secrets are removed in `always` sections, so a run that fails midway does not leave them in `/root`.

### 6. Restarts follow from the pod template

The collector's and the bot's pod templates carry the image digest and an annotation with the SHA-256 of the rendered `itgraph-env` Secret. Re-applying an identical template does nothing; a new digest or a changed configuration changes the template, and the Deployment controller replaces the pod. That is "Services Run The Deployed Code" stated declaratively, including the re-run after a failed deploy: the workloads still carry the old digest, so applying the new one restarts them. Both Deployments use `strategy: Recreate`, so the old pod is gone before the new one starts — one poller per bot token, one holder per session. `terminationGracePeriodSeconds: 90` for the collector matches today's `TimeoutStopSec`: it finishes the poll in flight and releases the lease on SIGTERM.

### 7. A console instead of a checkout

`itgraph-console` is a Deployment of the same image running `sleep infinity`, with the full configuration, the backups volume and the session volume mounted, and the label that lets it reach the proxy. Everything the operator runs goes through it:

- `scripts/on-host.sh <args>` becomes `ssh -T root@host k3s kubectl -n itgraph exec -i deploy/itgraph-console -c itgraph -- itgraph <args>`. `kubectl exec` keeps standard output and error apart and returns the remote exit status; arguments are quoted one by one as today.
- `scripts/on-host.sh --loop stop|start` scales `itgraph-watch` to 0 (`--replicas=0`) and waits until its pod is gone, or back to 1 and waits for the rollout. A command needing the session then runs in the console, through the proxy, and the lease still refuses it while the loop holds the session. A deploy refuses while the collector or the bot is scaled to zero by hand, and says why: starting the collector behind a running session command would only crash-loop it on the lease, and the server-side apply of `replicas: 1` over a `kubectl scale` would otherwise stop on a field conflict that names neither.
- The playbook runs `alembic`, `itgraph baselines`, `itgraph watch-status`, the session probe and copy, and the exit check in it.

Alternatives: one-off pods per command (`kubectl run --rm -i`) start slower, print their own messages into the output stream, and need the whole pod spec passed as JSON; a Job cannot take standard input.

### 8. The proxy is a Deployment of its own

`itgraph-proxy` runs xray-core's official image, pinned by digest, with a configuration rendered into a Secret: one SOCKS inbound on port 1080 with password authentication and UDP off, and exactly one outbound — the operator's VPN outbound, taken from the client configuration and stored in the vault as `proxy_outbound`. There is no `freedom` outbound and no routing rule, so there is no way out except through the VPN server, and when it is unreachable a connection fails. A TCP readiness probe on 1080 gates its Service.

`itgraph-env` sets `PROXY_TYPE=socks5`, `PROXY_HOST=itgraph-proxy`, `PROXY_PORT=1080`, `PROXY_USERNAME=itgraph` and `PROXY_PASSWORD` from the vault, so [client.py](../../../src/itgraph/tg/client.py) needs no change. The bot and the YouTube client never read `PROXY_*` for their own connections ("Outbound Route", "Only Telegram's own protocol is proxied").

Why not a sidecar in the collector: the console needs the same exit for session commands, so a sidecar would be two proxies to configure and check, and its start order against the main container is one more thing to get right. The operator chose the same VPN server the laptop uses, so at the cutover Telegram sees no change of address at all.

### 9. Two checks for the exit

1. **The route line.** `kubectl logs deploy/itgraph-watch -c itgraph` covers the current container since it started — the counterpart of reading the unit's current invocation in the journal. Retried for up to 30 seconds; it must contain `Telegram route: socks5 proxy at itgraph-proxy:1080`. A `direct` line and no line at all fail with different messages. The kubelet rotates container logs, so a collector that has run for months may no longer have the line; only then — no line, and a container started over an hour ago — the route is read from the running container's `PROXY_*` environment instead, where the line's values come from, and the playbook says so.
2. **The address.** From the console, `curl` to `https://api.ipify.org` once directly and once through the proxy (credentials from the environment, never on a command line). Both must answer, and the answers must differ. This is what catches a proxy that accepts connections and sends them out from the host's own address — a state in which the route line is correct and the exit is not. `api.ipify.org` answers over IPv4 only, so both readings are comparable.

`--tags prepare` runs only the second; the first needs a collector.

### 10. The session moves from a stopped host, once

The session check runs only when the session volume holds no session, which is the cutover and nothing else. Before copying, a task delegated to the `[previous]` host asserts that `itgraph-watch` and `itgraph-bot` are inactive and not enabled; anything else fails before the copy, naming the unit. `-e session_previous_host=none` states there is no such host (a laptop that is lost), and the report then says the check was skipped on request.

The copy goes into the volume through the console with mode 0600, and its SHA-256 is compared with the source before the collector is started. The source is `itgraph_session_src` on the Mac, where the operator puts the laptop's session for the cutover and deletes it afterwards (Migration Plan).

### 11. Dumps without Docker, and the off-site copy

**Running the tools.** `postgres_container` becomes optional. Set — the default, the Mac — the commands stay `docker exec -i …`. Empty — what `itgraph-env` sets — `pg_dump`, `pg_restore --list` and `psql` run as local processes against the host and port from `DATABASE_URL`, with the password in `PGPASSWORD` in the child's environment, never in its arguments. `BACKUP_DIR` points at the backups volume, so the backup CronJob and the pre-migration dump in the console write to the same place.

**The off-site pass** runs at the end of `itgraph backup`, after `run_backup`:

1. Authorize with the bucket-restricted key over B2's native API, through the `httpx` the project already uses.
2. List the names under `inventory/` and `full/`.
3. For every local, verified dump younger than seven days with no copy yet: encrypt with `age -r <recipient>` to a temporary file on the backups volume, take its SHA-1 a chunk at a time, get an upload URL, upload streamed from the file with its `Content-Length` stated and `X-Bz-Content-Sha1`, and check the response's `contentSha1`. Nothing reads the dump or its ciphertext whole. Remove the temporary file whatever happened.
4. Any failure fails the command, naming the dump; the local dumps and their pruning are untouched.

The seven-day window is what stops a loop: a local dump older than the store's retention would otherwise look missing and be uploaded again for as long as it is kept locally. The dump taken before a migration is in `weekly/` like any other full dump, so the next scheduled run copies it.

**The key** has `listFiles` and `writeFiles` on the one bucket and nothing else: no `deleteFiles`, no `readFiles`. Names carry the dump's UTC timestamp and are never reused. **Retention** is two lifecycle rules: `inventory/` hidden after 30 days, `full/` after 28, both deleted a day after hiding. **Cost:** at today's sizes the store holds about 5 MB of inventory and five full copies, about 1.4 GB, under a 3 GB ceiling for itgraph's copies; the account's data caps are set to $0, so going past the free tier fails uploads (`cap_exceeded`) instead of billing.

**Encryption** is to an age recipient that is public and lives in `deploy/group_vars/all/main.yml`. The identity is generated on the Mac and kept only in the password manager. The `age` CLI streams, where Python bindings encrypt in memory — 275 MB twice over, in a job with a memory limit — and it adds no Python dependency. Tests mock the subprocess, plus one round trip that runs only where `age` is installed, the way the database tests run only where Postgres is.

Alternatives: `b2sdk` (a large dependency for three calls), B2's S3 API through `boto3` (adds SigV4 and a large dependency; the native API verifies a SHA-1 header directly), rclone (another binary and configuration language).

### 12. A rollout is not proof

`kubectl rollout status` reports a Deployment available whenever its pod happens to be up, crash-looping or not. After the rollout the playbook reads each pod's `restartCount` before and after a 60-second settle, and fails the deploy — with the counts, readiness and the last exit code and reason — if a count moved during the settle, if a pod is not ready at its end (one in CrashLoopBackOff can sit out the minute between restarts), or if a pod this run created has restarted at all. A restart from before the deploy is not a failure: the collector exits on purpose when it stops making progress, and one such restart last week must not fail every deploy after it.

### 13. Moscow time without touching the host

- Every CronJob has `timeZone: Europe/Moscow`. The controller uses the system time zone database and falls back to the one compiled into k3s.
- Every itgraph container has `TZ=Europe/Moscow`. With `tzdata` in the image, `logging`'s `asctime`, `describe_reset`'s "here" and the lease holder's "since" are Moscow time.
- `WATCH_TIMEZONE` and the bot's `MOSCOW_TZ` already fix the zone for quiet hours, the digest and times in messages; they do not change.

### 14. The schedules

| CronJob | schedule (Moscow) | was | `startingDeadlineSeconds` |
|---|---|---|---|
| `itgraph-alerts` | `2-59/15 * * * *` | every 15 min | 600 |
| `itgraph-score` | `8-59/15 * * * *` | every 15 min | 600 |
| `itgraph-yt-watch` | `*/15 * * * *` | `*:0/15` | 600 |
| `itgraph-backup` | `40 */4 * * *` | every 4 h | 10800 |
| `itgraph-baselines` | `30 4 * * *` | 04:30 | 82800 |
| `itgraph-yt-refresh` | `0 5 * * *` | 05:00 | 82800 |

All have `concurrencyPolicy: Forbid`, `backoffLimit: 0` and `restartPolicy: Never` — a failed run is retried by the next firing, as a failed oneshot was. History is three successful and three failed Jobs, which is how long their logs stay readable. `activeDeadlineSeconds` bounds a stuck run, so that `Forbid` cannot block every later one: 10 minutes for `yt-watch`, as its systemd unit's `TimeoutStartSec` had it, 50 minutes for `alerts` and `score`, 2 hours for backup, 3 hours for the daily ones. The minutes are staggered, as the boot offsets staggered the timers. The daily deadlines are the counterpart of `Persistent=true`: a run missed while the node was down starts when it is back, the same day. `itgraph-alerts` runs `derive` as an init container and `alerts` as the container, so `alerts` never runs on edges that failed to derive — the reason it shared a unit with `derive` in the first place. The two YouTube CronJobs exist only when `youtube_api_key` is set, and are deleted when it is not.

### 15. Containment

- **NetworkPolicies:** deny all ingress in the namespace; allow the namespace's pods to Postgres on 5432; allow pods labelled `itgraph/telegram: "true"` (the collector and the console) to the proxy on 1080. Egress stays open: Telegram, YouTube, B2 and ghcr have no fixed addresses. No Service is of type NodePort or LoadBalancer, and there is no Ingress.
- **Pods:** `runAsNonRoot`, `readOnlyRootFilesystem`, all capabilities dropped, `seccompProfile: RuntimeDefault`, `automountServiceAccountToken: false`, an `emptyDir` on `/tmp`, `fsGroup` for the volumes. Postgres keeps the official image's own user handling.
- **Resources**, from the measurements in Context; memory limits only, so a busy minute is never throttled:

| workload | request | memory limit |
|---|---|---|
| postgres | 100m, 1 Gi | 2 Gi |
| watch | 50m, 256 Mi | 512 Mi |
| bot | 20m, 256 Mi | 512 Mi |
| console | 10m, 64 Mi | 2 Gi — it runs a fresh host's first baselines |
| jobs | 100m, 256 Mi | 1 Gi; baselines 2 Gi |
| proxy | 10m, 32 Mi | 128 Mi |

Postgres keeps the Compose flags (`shared_buffers=512MB`, `work_mem=64MB`, `maintenance_work_mem=512MB`, `max_wal_size=4GB`) and a 256 Mi memory-backed `/dev/shm`. `PGDATA` is a subdirectory of the volume.

### 16. Retiring the laptop

`deploy/decommission.yml` targets `[previous]` and refuses unless `-e retire_host=<that inventory name>` matches. It stops and disables every `itgraph-*` unit and timer, removes their unit files and reloads systemd, deletes the session and its journal, deletes `.env`, runs `docker compose down -v` in the checkout (the database volume holds `yt_raw_*` rows, which may not outlive 30 days), removes the checkout, unmasks the sleep targets and removes the logind drop-in. It keeps `~/itgraph-backups`. Docker, ufw, uv and the swap file stay: they are the laptop's now, not itgraph's. logind is not restarted — on a machine with a graphical session that can end the session — and reads its defaults at the next boot. It ends by asserting that no `itgraph-*` unit is loaded, no session file remains, and Docker holds no volume of the `itgraph` Compose project; it runs with `-K`, since sudo on the laptop asks for a password.

## Risks / Trade-offs

- **[Two addresses on one session]** → the `[previous]` check before the copy, `Recreate` on the collector, and the lease inside the namespace. The check confirms the laptop is stopped and disabled; it cannot stop the operator from starting it again by hand, which is why decommissioning follows within days.
- **[Snapshots missed during the cutover]** → accepted. The window runs from stopping the laptop to the collector's first poll on the server: a dump, two copies and a restore, expected well under an hour. `--tags prepare` beforehand takes the image pull, the proxy and its check out of the window.
- **[The VPN server or its configuration changes]** → collection stops, by design: there is no direct fallback. The deploy's restart check catches it at deploy time; between deploys, `watch-status` and the bot's own status show stale data. An unattended alert is out of scope.
- **[Logs are shorter-lived]** → the kubelet rotates container logs, and finished Jobs keep three of each. Long-lived facts are already in the database (`flood_events`, pass records). Accepted.
- **[Standard input through `kubectl exec`]** → a review round trip depends on end-of-input reaching the command. Current kubectl and k3s carry exec over WebSockets, which can close standard input; the round trip is checked on the host before the cutover (tasks).
- **[No image means no deploy]** → if CI is broken, nothing new can be deployed until it is fixed. The running deployment is unaffected, and an image pinned by digest is still in the registry for a re-run.
- **[A lost age identity makes every off-site copy useless]** → it lives in the password manager, and a restore from an off-site copy is rehearsed once now and after any change to the off-site pass.
- **[A single disk set]** → the database, the session and the local dumps share the host's disks. The off-site copies are the answer to losing the host; the session is not among them on purpose — it is a full key to the account, and losing it costs days of resolving, not the account.
- **[Pods egress over IPv4 through the node]** → the YouTube key gets one IPv4 restriction after the cutover. The direct reading in the exit check prints the address to restrict to.

## Verification

- `make validate` after every code change; unit tests for the tools' direct mode and for the off-site pass (B2 over `httpx.MockTransport`, `age` mocked, plus a real round trip where `age` is installed). No test touches the network.
- `make ansible-lint` over the rewritten playbook and the decommission playbook.
- The image's own checks in CI (Decision 2), and `docker run` of the built image on the Mac before the first deploy.
- On the host, in order: `--tags prepare` (namespace, proxy, both exit readings), then a review round trip through `scripts/on-host.sh` against an empty database, then the cutover. Every check that needs Telegram is the operator's to run.

## Facts checked against the documentation, 2026-10-26

- CronJob `.spec.timeZone` is stable since Kubernetes 1.27; the controller uses the system time zone database and falls back to a bundled one. With `startingDeadlineSeconds` set, missed schedules are counted only within that window, so a long outage does not hit the 100-missed-schedules limit.
- B2 application keys can be restricted to a list of buckets, with capabilities chosen individually from a list that includes `listFiles`, `writeFiles`, `readFiles` and `deleteFiles`.
- `b2_upload_file` requires `X-Bz-Content-Sha1` and checks the content against it; a reached storage cap answers `cap_exceeded`. Data caps are per day, in money; usage within the free allowance is not charged.
- Lifecycle rules take `fileNamePrefix`, `daysFromUploadingToHiding` and `daysFromHidingToDeleting`.
- xray's SOCKS inbound supports `auth: "password"` with a list of users, and UDP is off by default.
- uv's documented multi-stage pattern: `UV_PYTHON_DOWNLOADS=0` with matching builder and runtime images, because the virtual environment's interpreter path must exist in both.

## Migration Plan

**Before (no gap):**

1. The operator makes the ghcr package public after the first CI build.
2. The operator creates the B2 bucket with both lifecycle rules, a key restricted to it with `listFiles` and `writeFiles`, and sets the account's data caps to $0.
3. The operator generates the age identity on the Mac and stores it in the password manager; the recipient goes into `main.yml`.
4. The operator extracts the VPN outbound from the client configuration for the server the laptop uses, and adds to the vault: `proxy_outbound`, a random `proxy_socks_password` of 20 or more letters and digits (the exit check puts it in a proxy URL), `b2_key_id`, `b2_application_key`, `b2_bucket_name`. The old `proxy_*` endpoint keys are removed.
5. The inventory: the k3s host under `[collector]` as root with `itgraph_storage_class`; the laptop under `[previous]`.
6. `ansible-playbook deploy/playbook.yml --tags prepare`. Then a review round trip through `scripts/on-host.sh` against the empty database.

**Cutover (the gap):**

7. On the laptop: stop and disable every `itgraph-*` unit and timer.
8. On the laptop: `uv run itgraph backup --full` for a fresh full dump, after the collector stopped. Copy it and `~/itgraph/itgraph.session` to a 0700 directory on the Mac; point `itgraph_dump_src` and `itgraph_session_src` at them.
9. `ansible-playbook deploy/playbook.yml`: restore, migrate, the previous-host check, the session copy, services, verify.
10. Delete the session and dump copies from the Mac.

**After:**

11. A day of watching: snapshots, alerts and the bot, a backup and its off-site copies, `watch-status`.
12. The YouTube key restricted to the server's IPv4 address.
13. `ansible-playbook deploy/decommission.yml -K -e retire_host=<name>`, within days, not weeks; then remove `[previous]` from the inventory.
14. A restore rehearsal from an off-site copy on the Mac: download, `age -d`, `pg_restore` into a scratch database whose name ends in `_test`.

**Rollback.** Before step 9 starts the collector, the laptop's session has not been used anywhere else: re-enable its units. After it, scale `itgraph-watch` and `itgraph-bot` to 0 first, copy the session from the volume back to the laptop (it has the newer entity cache), then re-enable the laptop's units. Data collected on the server in between is in its dumps.

## Assumptions

Not covered by the requirements — chosen while writing this, not yet confirmed by the operator:

- **Running the image's `itgraph` entry point instead of `uv run`** (Decision 2).
- **A separate proxy Deployment**, not a sidecar; xray-core as its core; SOCKS with a password inside the namespace (Decision 8).
- **An always-on console Deployment** as the target of `on-host.sh` and of the playbook's commands (Decision 7).
- **Root over SSH** for the deploy and for `on-host.sh`, as the host is already driven, rather than an account confined to the namespace.
- **The namespace name `itgraph`**, the storage class as an inventory variable, and the volume sizes (database 20 Gi, dumps 20 Gi, session 1 Gi).
- **The resources** in Decision 15, the schedules, deadlines and history limits in Decision 14, and the 60-second settle in Decision 12.
- **Off-site retention** of 30 days for inventory copies and 28 for full ones, the seven-day catch-up window, the 3 GB ceiling, and copying every verified dump rather than one a day.
- **The B2 native API over `httpx` and the `age` CLI**, rather than SDKs (Decision 11).
- **The retirement's scope:** what it removes, and that Docker, ufw, uv and the swap file stay on the laptop (Decision 16).
- **The waits:** 20 minutes for CI's image, 30 seconds for the route line.

## Open Questions

- **The wording in `docs/privacy.html`.** "The database runs on a private computer reachable only by the developer" stops being true. A proposal is "a server rented by the developer, reachable only by the developer". It is a public statement the YouTube application points at, so the operator words it; the change waits for that before archiving, not before implementing.
