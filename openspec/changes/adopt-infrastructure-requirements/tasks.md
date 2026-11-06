# Tasks

Tasks marked **(operator)** reach the server, the controller, Telegram, B2 or the Google console, and are the operator's to run; nothing else here does. No task opens the Telegram session. Every task that changes code ends on a green `make validate`. Nothing in this repository may name a host, an address, an id, another project or the infrastructure repository.

## 1. Settings

- [x] 1.1 `config.py`: `telegram_api_id` and `telegram_api_hash` become optional; `tg/client.py`'s `build_client` raises the error `_run` already turns into exit 1, naming both, before constructing a client. Verify: tests that settings load without them, that a command building a client exits non-zero naming them with no connection made, and that `tg/watch.py` has no diff.
- [x] 1.2 A setting for the Bot API's address, default Telegram's, used where the bot's session is built in `bot/handlers.py`. Verify: a test that the default is unchanged and that a given address reaches the aiogram session.
- [x] 1.3 `src/itgraph/README.md` and `.env.example` say which settings each kind of process needs. Verify: the two agree with `config.py`, read through.

## 2. The exporter

- [x] 2.1 An Alembic revision creating the role `itgraph_metrics`, `NOLOGIN`, with the grants of design.md, Decision 14: `SELECT` on the tables it reads and only the fetch-time column of each `yt_raw_*` table. Verify on a scratch database (`_test`): upgrade and downgrade; a test that the role cannot select `payload` from any `yt_raw_*` table and can write nothing.
- [x] 2.2 `src/itgraph/metrics/`: the readers, one per series of `specs/collection-monitoring`, returning values, with no stored state. Verify: tests through the real Postgres fixture with seeded rows, through that role, including no row, a quiet moment, a stopped collector, and absent `kind`s.
- [x] 2.3 The text format and the listener, with `asyncio.start_server`, and the command `itgraph metrics`. Verify: tests for 200 with the content type and every `# TYPE`, 404 and 405 with no query made, 503 when the database refuses and the process surviving, and no row changed by a scrape.
- [x] 2.4 A test that no label value comes from a table's data, and that no series reads `raw_messages` or `message_metrics` without an index, by `EXPLAIN` on the fixture. Verify: the test fails when a seq-scan series is added on purpose, then remove that series.
- [x] 2.5 `src/itgraph/CLAUDE.md` gets the module's row, and `src/itgraph/README.md` a section on the series and the role. Verify: every series in the spec appears in the section.

## 3. The role: inputs, rendering, applying

- [x] 3.1 `deploy/ansible/itgraph/` with `meta/argument_specs.yml`, `defaults/main.yml`, `vars/main.yml` and `tasks/inputs.yml` (design.md, Decision 2), and `deploy/ansible/render.yml` and `inputs/a.yml`, `b.yml`, `e2e.yml`, all synthetic. Verify: `tests/test_deploy_role.py` refuses each bad input before any recorded call that changes something, among them an image with no digest, a major other than 16, the wrong outbound for the environment, and half a B2 set.
- [x] 3.2 `tasks/apply.yml`: `kubectl diff --server-side`, then `apply --server-side --field-manager=itgraph`, on stdin, under `no_log` for Secrets; check mode diffs alone. A stub `kubectl` in `tests/` records arguments and answers from a directory. Verify: tests on a new namespace, an unchanged stream (no apply), a changed one, a diff failing, a check, and no secret in any recorded argument or at `-vvv`.
- [x] 3.3 `tasks/lock.yml`, `tasks/unlock.yml`: the Lease of design.md, Decision 11, its clock a variable. Verify: tests of a held lock, takeover at exactly 60 minutes and not a second before, a lost creation race changing nothing.
- [x] 3.4 Templates: namespace, ConfigMap, the six Secrets of Decision 9, claims, with today's names. `tests/test_deploy_role.py` renders through `render.yml` and carries over from `tests/test_deploy_manifests.py` whatever still applies. Verify: tests that each workload's keys are its own, no value of set A in set B's objects, claims and sizes unchanged; `tests/test_deploy_manifests.py` removed in the same commit as its rules have moved.
- [x] 3.5 `make ansible-lint` covers `deploy/ansible` under `profile: production`. Verify: `make validate`.

## 4. The role: the database

- [x] 4.1 `templates/postgres.yaml.j2` per Decision 8, today's Service, `itgraph-postgres-access` at the fixed address, the checks of the storage class. Verify: tests of every container's `securityContext`, the emptyDirs and `/dev/shm`, `PGDATA` below the mount, and refusal of a class that is not `Retain` before any claim.
- [x] 4.2 The coherence checks carried over from `deploy/playbook.yml`, as `exec` of `psql` in the pod: a missing channel table is empty, any other failure stops. Verify: stub tests for each answer, none leading to a migration where it must not.
- [x] 4.3 `tasks/password.yml`: compare each role's value with its Secret under `no_log`, `ALTER ROLE … LOGIN PASSWORD` on stdin only when they differ or the Secret is new. Verify: stub tests that the password is on stdin alone, and that a second run issues no `ALTER`.
- [x] 4.4 `tasks/quiesce.yml` per Decision 11: when the database's stream has a change or the schema is behind, note and suspend the schedules, wait for Jobs, scale `watch` and `bot` to 0 and wait; an `always` block puts the schedules back as found. Verify: stub tests that a run with nothing to change stops nothing, that a suspended schedule stays suspended, and that a failure midway restores them.
- [x] 4.5 `tasks/migrate.yml`: a pod of the deployed image for `alembic current --check-heads` and, when behind, `alembic upgrade head` with the backups claim; report the revisions and the dump's path; a gate after it; a schema ahead of the image fails naming both. Verify: stub tests for at head (no dump, no upgrade), behind, and failed; and one that a folded block cannot have split the command, by reading the recorded argument.

## 5. The role: the proxy, the exit, the network

- [x] 5.1 `templates/proxy.yaml.j2` and its Secret, as today, with the outbound from the input. Verify: a test of exactly one outbound and no routing section, the closing outbound where collection is off, and no `exec` into xray anywhere in the role.
- [x] 5.2 `templates/network-policies.yaml.j2` per Decision 10. Verify: tests that each workload carries exactly its labels, that the collector has neither `itgraph/internet` nor a way to it, and that the scraper's rule is on 9090 alone.
- [x] 5.3 The exit check as two bare pods (Decision 7). Where collection is on, both readings answer and differ; where off, the proxied reading fails. No address is printed. Verify: stub tests of each outcome, and that the proxy's password is never an argument.

## 6. The role: workloads and what it verifies

- [x] 6.1 Templates for `watch` (`replicas` by the input), `bot`, `metrics`, each with a checksum of its own keys. Verify: tests that a new bot token changes the bot's template alone, and `Recreate` on `watch` and `bot`.
- [x] 6.2 `templates/cronjobs.yaml.j2`: `itgraph-detect` per Decision 6, and `baselines`, `backup`, the YouTube jobs unchanged in schedule and deadline, the YouTube jobs following the key and the walk's switch. Verify: tests carrying the schedule table, sibling containers in `detect` with `derive` before `alerts`, and every existing "no job runs…" rule kept.
- [x] 6.3 The session gate: a bare pod testing the file, regular and non-empty, reading nothing of it, only where collection is on; its message says not to create one. Verify: stub tests of present, absent, empty and a directory, the collector not started on any but the first; and that no task in the role writes to the session's volume, by search.
- [x] 6.4 The collector-spec pod where collection is off (Decision 4): rendered from the same template as `watch`, `watch --cycles 1`, requires the route line and a non-zero exit, and is deleted. Verify: stub tests that a zero exit fails, a missing line fails, and the pod is deleted either way.
- [x] 6.5 `tasks/rollout.yml`: rollout status, undo on failure with what it printed, the settling minute carried over from `deploy/playbook.yml`, and polls advancing where collection is on and outside quiet hours; inside them the report says so. Verify: stub tests of a pod that restarted, one in a crash loop, an old restart not failing, a poll that never comes, and quiet hours.
- [x] 6.6 Refuse a collector scaled to zero by hand where collection is on; deletions by name (`itgraph-console`, `itgraph-alerts`, `itgraph-score`, `itgraph-env`, YouTube jobs per the key, the walk's job per its switch), each `--ignore-not-found`. Verify: stub tests of each, and that no recorded call deletes a claim, in any path.
- [x] 6.7 The Pod Security label last, with `kubectl label`, failing on any `Warning`; the report of `specs/collection-deploy`. Verify: stub tests that a warning fails the run, that the namespace's manifest holds no such label, and that the report says what a check could not prove.
- [x] 6.8 `deploy/runbook.md`, in Russian, through the operator's own account: a deploy, a killed run (the lock, the schedules, the collector), the fixed address through a tunnel, moving the session by hand, restoring production by hand, what must never reach a test environment. Verify: every command in it is one the role or the script accepts, read through.

## 7. The role: restore

- [x] 7.1 `tasks/restore.yml`, steps 1–5 and 8 of Decision 12. Verify: stub tests that it refuses where collection is on and without the input, and that a text file touches nothing; that the command is one line or a literal block; that the excluded tables equal `EXCLUDED_DATA` in `db/backup.py`.
- [x] 7.2 `files/neutralise.sql` and `files/readback.sql` per Decision 13, written against the real schema. Verify: `tests/test_neutralise.py` through the Postgres fixture, on seeded rows of every shape in the table (a `_test` database), with each statement removed in turn failing the read-back; a forward from a channel, a text and the counters unchanged; run twice with no change the second time.
- [x] 7.3 The rescue: a failure before neutralising has finished drops and re-creates the database; schedules and lock put back whatever happened. Verify: stub tests of a failure at each step.
- [x] 7.4 Step 9 of Decision 12: a Job from `itgraph-baselines`, a `backup --full`, a synthetic alert, each read back by rows; quiet hours reported. Verify: stub tests of each outcome, and that the alert references a restored post.
- [x] 7.5 A test that passes on a copy 60 days old exit zero: `derive`, `alerts`, `score`, `baselines` and `backup` through the fixture on seeded old rows. Verify: the test itself; a failure here changes the passes, not the test.

## 8. `scripts/on-host.sh`

- [x] 8.1 The script per Decision 7: `ITGRAPH_SSH`, `--session`, `--backups`, `--loop`, `sudo -n k3s kubectl`, creating and deleting the Job. The ConfigMap's three templates in the role. Verify: `tests/test_on_host.py` with a stub `ssh`, carrying over arguments intact, exit status, output and errors apart, adding no destination, deletion on failure, and the session absent unless asked for.
- [x] 8.2 `src/itgraph/README.md`'s "Работа с хоста" and the review cycle follow the script. Verify: every example in them matches the script's usage.

## 9. The release

- [x] 9.1 `deploy/release.sh` and `tests/test_release.py`, per Decision 15, with stubs of `docker` and `gh`, and `gh` refusing outside a repository. Verify: tests of a bad tag, a commit off `master`, an image that appears late, one that never does, unequal digests, two packs byte-identical, and `gh` called from the repository.
- [x] 9.2 `.github/workflows/release.yml`: the suite against a Postgres 16 service and the role's checks, then the script, actions pinned by commit, no secret but `GITHUB_TOKEN`. `image.yml`'s comment says what CI gates now. Verify: `make validate`; a test that no workflow names another secret, and that `release.yml` runs on `v*` alone. The workflow itself first runs at step 13.3.

## 10. End to end, on k3d

- [x] 10.1 `deploy/ansible/e2e.sh` and `make test-e2e`: a k3d cluster at the server's k3s version, the role with `inputs/e2e.yml`, a stub Bot API in the cluster. Verify: read-only and `restricted` on a fresh volume, with Postgres answering.
- [x] 10.2 The takeover: today's templates, a root-started Postgres with seeded rows, a session-shaped file, dumps; then the role. Verify: the same uid-999 Postgres up on those files, row counts, the file's checksum and the dumps unchanged, the deletions made, claims bound as before.
- [x] 10.3 The network and the collector's spec: egress from each label, the collector's pod refused going direct and let through to the proxy; the failing pod's route line and exit; the fixed address from a host-network pod; the scraper's labels against others. Verify: each in the suite's output.
- [x] 10.4 Repeats and concurrency: a second run with pods' uids unchanged, a new bot token restarting the bot alone, two runs at once, a check on a new cluster and on one with a change to show. Verify: each in the suite's output.
- [x] 10.5 `restore`: a dump made by a backup run on seeded rows of every shape; the counts; neutralising read through the database and the stub's record of calls; a copy through `age` and back; a missing file, a text file, a dump cut in its table of contents and one cut past it; a suspended schedule kept; a second restore from a dump of the neutralised copy. Verify: each in the suite's output.

## 11. Documents and rules

- [x] 11.1 **(operator)** Word the sentence in `docs/privacy.html`. Proposed: "The database runs on a private server reachable only by the developer and by the one other person who administers that server." Verify: the user's words are recorded here.

  The operator on 2026-11-06, of that sentence: «С формулировкой согласна.»
- [x] 11.2 `docs/privacy.html` takes that sentence and a new "Last updated" date; the Telegram sentence about readers stays for the later change, as the proposal says. Verify: `git diff` shows those two lines.
- [x] 11.3 Root `CLAUDE.md`: commands; `make test-e2e`; "the inventory is backed up" and the restore; "CI builds the image and gates nothing", now a tag is gated; the vault lines in "Never commit", replaced by what the operators hold and where; the session rule stating it never reaches a test environment. Verify: no line describes the old path, by search for `playbook`, `vault_pass`, `inventory.ini`, `console`.
- [x] 11.4 `README.md`, `src/itgraph/README.md`'s restore and off-site sections, and `docs/PLAN.md` where it describes the server. Verify: the same search; the plan's quota figures updated to those measured on 2026-11-06.
- [x] 11.5 `openspec validate adopt-infrastructure-requirements --strict`. Verify: it passes.

## 12. The rules, and the request

- [x] 12.1 `promtool test rules`, in the scratchpad and not the repository, for each rule of design.md, Decision 16, item 12, firing and silent, at each boundary: 07:00's end of quiet hours inside the `for`, a collector at zero replicas, no YouTube job. Verify: red with no rules, green with them, and the `on ()` removed from `ItgraphCollectionStalled` failing its firing case.
- [x] 12.2 Hand the user Decision 16 with the rules and their tests, naming that repository's files in chat alone, the thresholds marked as proposed. Verify: the date the user took it, recorded here.

  Taken by the operator on 2026-11-06. The rules hold no messages yet: item 12's Russian text is written where they land.
- [x] 12.3 What `/opsx:verify` found on 2026-11-06 is fixed, the operator's answer being «Правь всё», with the database put back by hand and not by a run: a note on an affiliation decision left in restored copies, a YouTube key and an off-site store accepted where collection is off, another role's password stopping the collector, the database neither watched nor covered in the runbook, `VACUUM FULL` missing what a deletion cascades into, the fixed address's password untried, `rollout undo` of a workload with no new template, and the artifacts where they disagreed with the code. Verify: `make validate`; `make test-e2e`.

- [ ] 12.4 The restore's side of `remove-person-ids-from-raw-layer`, as amended above on 2026-11-06: no payload statements in `neutralise.sql`, a read-back by type. The code lands with that change's group 3. Verify: its 3.1 and 3.2.

## 13. The test environment

- [ ] 13.1 **(operator)** A test bot from BotFather; a test group; values into the test vault. Verify: `/status` answers in that group after 13.5.
- [ ] 13.2 **(operator)** The request implemented behind the flag; production's `--check --diff` shows no change. Verify: its output.
- [ ] 13.3 **(operator)** Push `master`, wait for the image, tag `v0.1.0`. Verify: the release holds the archive and its sum, and the image's tag has the commit's digest.
- [ ] 13.4 **(operator)** Pin in test. Verify: the commit in the test inventory.
- [ ] 13.5 **(operator)** The main playbook on a fresh volume, twice. Verify: green, the second run restarting nothing, the namespace enforcing `restricted`, and the failing collector pod's report.
- [ ] 13.6 **(operator)** `restore` from production's newest full dump. Verify: counts match, the read-back's zeroes, the synthetic alert in the test group, a new baseline run, no container restarting in five minutes; the duration of the revision clearing people, recorded in `remove-person-ids-from-raw-layer`, 5.3.
- [ ] 13.7 **(operator)** `restore` from the off-site copy, the age identity pasted at the prompt. Verify: as 13.6, and no copy left on the controller or the test host.
- [ ] 13.8 **(operator)** One rule fired on purpose to the test group. Verify: seen there, and resolved.
- [ ] 13.9 **(operator)** The takeover rehearsed in test (Migration Plan, step 7). Verify: Postgres up as uid 999 on files a root-started image wrote; the gap measured, and recorded here.

## 14. Production

- [ ] 14.1 **(operator)** Values into production's vault on the controller, printing none; the restore's read key issued; the namespace's non-Secret objects snapshotted there. Verify: `--check --diff` shows only what the design says.
- [ ] 14.2 **(operator)** At about 06:15 Moscow time: promote the pins, flag on, run the main playbook. Verify: the report; the session's checksum unchanged, compared on the host with no copy made; channels' count unchanged; the old objects gone.
- [ ] 14.3 **(operator)** At about 07:05: polls advancing, a YouTube pass complete, the route line, the exit check, the tunnel to the fixed address, taking a role's password and refusing none, every series scraped, the rules silent. Verify: each read and recorded here.
- [ ] 14.4 **(operator)** Rotate the bot token, the YouTube key and the B2 key. **The old values are revoked for good.** Reissue the VPN credential only if the server stays the same. Verify: the bot answers, the next YouTube pass completes, the next backup's upload succeeds; the collector not restarted by any but the role's own changes to it.
- [ ] 14.5 **(operator)** Soak: one backup under the role with its off-site copy, one restore of a dump taken under the role, one off-site restore. A week's reading of the YouTube timeout alert's noise. Verify: each recorded here.

## 15. Retirement

- [ ] 15.1 **(operator)** `make backup-uninstall` on the operator's machine. Verify: `launchctl` says the agent is not loaded.
- [ ] 15.2 The commit removing the old path (design.md, Decision 17), its hooks, targets and tests, and the launchd agent's part of `src/itgraph/README.md`. Verify: `make validate`; `git ls-files` lists no `vault`, `inventory` or `playbook` under `deploy/`.
- [ ] 15.3 **(operator)** **Final, and not to be undone:** delete `deploy/.vault_pass`, `deploy/inventory.ini`, the vault password in the password manager and on every other machine, the old copy of the session in the operator's backups directory and any like it on the retired host, and the old dumps. The age identity stays. Verify: a search of each machine for `*.session*` and `.vault_pass` finds nothing.

## Workflow follow-up

- `/opsx:verify`, then `/opsx:archive`.
- `remove-person-ids-from-raw-layer` removes people from what is collected and from production's raw layer, before the first tag, and corrects the privacy page's sentence about readers after production runs it.
- A possible later change gives comments a model of their own, if alerts on them are wanted. The present score cannot do it: on 14 days at 3.7 it would raise 2.
