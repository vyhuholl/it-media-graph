## Why

Everything itgraph runs — the Telegram collector, the alert bot, Postgres, the six timers and the Telegram session — lives on the operator's second laptop, the only machine there was when collection started. The laptop needs its sleep masked so that a closed lid does not lose snapshots, it reaches Telegram and YouTube only through an always-on VPN, and every manual task on it is slow and error-prone. A rented server is now available: a single-node k3s host that itgraph does not own, so the deploy has to fit that host rather than configure it the way it configured the laptop.

The current playbook cannot simply be pointed at it. It enables ufw with a default-deny policy, installs Docker and adds a swap file, all of which are the host's own business, and it assumes systemd units, a host-wide VPN and the host's time zone being Moscow. None of those hold on the new host.

## What Changes

- **BREAKING (deploy):** itgraph runs in a Kubernetes namespace of its own on a k3s host instead of as systemd units and a Compose project on an Ubuntu machine. Postgres, the collector, the bot and an operator console become workloads; the six timers become CronJobs. The deploy touches nothing outside that namespace: no packages, firewall, swap, sleep settings or time zone on the host.
- **The code runs as a container image built by GitHub Actions** from every push to `master`, published to `ghcr.io` and deployed by digest. The host no longer clones the repository or runs `uv sync`.
- **MTProto keeps leaving through a shared VPN exit.** A proxy inside the namespace, configured with the operator's v2ray-family client configuration, gives the collector the SOCKS5 endpoint `PROXY_*` already supports. The deploy checks both that the collector logs a proxied route and that the address seen through the proxy is not the host's own. The Bot API and YouTube stay direct.
- **Schedules, logs and the times in messages are in Moscow time**, set per workload, whatever the host's own zone is.
- **Dumps stop depending on Docker:** `pg_dump`, `pg_restore` and `psql` run from the image against the database's network address. On the operator's machine, where Postgres is still a Compose container, the existing path stays.
- **Every verified dump is also copied off-site**, encrypted before it leaves the host with a key the host cannot decrypt, into a Backblaze B2 bucket whose credential on the host cannot delete or overwrite anything. Retention is the bucket's, sized so that the copies stay inside the free tier.
- **`scripts/on-host.sh` runs commands in the namespace's console** over SSH and `k3s kubectl`, with standard input, output and exit status passed through as before, and can stop and start the collection loop for commands that need the session.
- **The move itself is guarded:** the session is copied only into an empty session volume, and only after the deploy has checked over SSH that the previous host's collector and bot are stopped and disabled. One session used from two addresses at once is a revoked authorization, and a fresh one has an empty entity cache.
- **The laptop is decommissioned** by a playbook of its own: units, session, configuration and database volume removed, sleep unmasked; its existing dumps stay where they are.
- Documentation follows: `deploy/README.md`, the backup, restore and proxy sections of `src/itgraph/README.md`, `docs/PLAN.md`, the root `CLAUDE.md` (CI now builds images; new secrets in the never-commit list), and `docs/privacy.html`, which says the database runs on "a private computer".

Out of scope:

- **A rewrite in another language.** Measured on the laptop, the whole deployment uses under 1 GB of memory and a fraction of one core on average.
- **Tests in CI.** The workflow builds the image; pre-commit stays the gate.
- **Giving the bot a narrower configuration than the collector.** Settings require the Telegram API credentials at load, so separating them is a code change of its own.
- **High availability.** One node, one replica of everything.

## Capabilities

### New Capabilities

- `database-backups`: where dumps live and what leaves the host — off-site copies encrypted before upload, a credential that cannot delete them, retention held by the store, a copy that counts only once the store confirms it, and a ceiling that keeps the copies free.

### Modified Capabilities

- `collection-deploy`: re-stated for workloads in a namespace instead of units on a host. Added: nothing touched outside the namespace, the deployed code as an image pinned by digest, the shared-exit route and its check, Moscow time, the session moving only from a stopped host, and a retired host left without collection state.
- `youtube-inventory`: "Remote Operation" runs commands in the namespace's console instead of in a checkout on the host, and gains stopping and starting the collection loop.

## Impact

- New: `Dockerfile`, `.dockerignore`, `.github/workflows/image.yml`, Kubernetes manifest templates under `deploy/templates/`, `deploy/decommission.yml`, an off-site upload module beside `db/backup.py`.
- Rewritten: `deploy/playbook.yml`, `scripts/on-host.sh`, `deploy/inventory.ini.example`, `deploy/group_vars/all/main.yml`; the systemd unit templates and the laptop-only logind template are removed.
- Changed: `db/backup.py` and `config.py` (running the PostgreSQL tools without Docker; off-site settings), `itgraph backup` (uploads after a verified dump).
- New secrets in the vault: the v2ray client configuration, the proxy's own SOCKS password, the B2 key and bucket. New public setting: the age recipient used to encrypt dumps. The age identity stays in the operator's password manager only.
- New dependencies: none in Python. The image adds the PostgreSQL 16 client, `age`, `curl` and `tzdata`.
- The operator: a one-time cutover with a short collection gap — snapshots missed during it are not recoverable — then the YouTube key restricted to the server's addresses, and a restore rehearsal from an off-site copy.
