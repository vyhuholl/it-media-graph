#!/usr/bin/env bash
# The role on a throwaway k3d cluster at the server's k3s version, and what it deploys
# checked from outside. A stand-in for the Bot API runs in the cluster, so that nothing
# here depends on Telegram; nothing reaches Telegram, YouTube or B2. It fails, never
# skips, without its tools, and deletes its cluster on the way out. `make test-e2e`.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"

cluster=itgraph-e2e
k3s_image=rancher/k3s:v1.36.5-k3s1
registry_port=15113
postgres_upstream=postgres:16
proxy_upstream=ghcr.io/xtls/xray-core:26.10.21
namespace=itgraph
takeover=itgraph-takeover
class=e2e-retaining

work="$(mktemp -d "${TMPDIR:-/tmp}/itgraph-e2e.XXXXXX")"
cleanup() {
	if [ -z "${E2E_KEEP:-}" ]; then
		k3d cluster delete "$cluster" >/dev/null 2>&1 || true
		rm -rf "$work"
	fi
}
trap cleanup EXIT

pass=0
ok() {
	pass=$((pass + 1))
	printf 'e2e: ok - %s\n' "$*"
}
say() { printf 'e2e: %s\n' "$*"; }
fail() {
	printf 'e2e: FAILED - %s\n' "$*" >&2
	exit 1
}

for tool in docker k3d kubectl jq uv; do
	command -v "$tool" >/dev/null 2>&1 || fail "needs $tool"
done
docker info >/dev/null 2>&1 || fail 'the Docker daemon is not running'

# --- setup -----------------------------------------------------------------------

k3d cluster delete "$cluster" >/dev/null 2>&1 || true
say 'creating the cluster, with a registry of its own'
k3d cluster create "$cluster" \
	--image "$k3s_image" \
	--registry-create "$cluster-registry:127.0.0.1:$registry_port" \
	--k3s-arg '--disable=traefik@server:0' \
	--kubeconfig-update-default=false --kubeconfig-switch-context=false \
	--wait --timeout 5m >/dev/null
k3d kubeconfig get "$cluster" >"$work/kubeconfig"
# Never the developer's own cluster.
export KUBECONFIG="$work/kubeconfig"
registry="$(k3d registry list -o json | jq -r --arg c "$cluster" '.[] | select(.name | contains($c)) | .name'):5000"

push() { # <local image> <name:tag>: pushes, prints repo:tag@digest as the cluster pulls it
	docker tag "$1" "localhost:$registry_port/$2"
	local digest
	digest="$(docker push "localhost:$registry_port/$2" | sed -n 's/.*digest: \(sha256:[0-9a-f]\{64\}\).*/\1/p')"
	[ -n "$digest" ] || fail "pushing $2 gave no digest"
	printf '%s/%s@%s\n' "$registry" "$2" "$digest"
}

say 'building the image, and pushing the three the role is given'
docker build -q -t itgraph-e2e:local "$root" >/dev/null
docker pull -q "$postgres_upstream" >/dev/null
docker pull -q "$proxy_upstream" >/dev/null
image="$(push itgraph-e2e:local it-media-graph:e2e)"
postgres_image="$(push "$postgres_upstream" postgres:16)"
proxy_image="$(push "$proxy_upstream" xray-core:e2e)"

kubectl apply -f - >/dev/null <<YAML
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: $class
provisioner: rancher.io/local-path
reclaimPolicy: Retain
volumeBindingMode: WaitForFirstConsumer
YAML

say 'the stand-in for the Bot API'
kubectl create namespace e2e-telegram >/dev/null
kubectl -n e2e-telegram create configmap stub --from-file="$here/e2e_telegram.py" >/dev/null
kubectl -n e2e-telegram apply -f - >/dev/null <<YAML
apiVersion: apps/v1
kind: Deployment
metadata:
  name: telegram
spec:
  replicas: 1
  selector:
    matchLabels: {app: telegram}
  template:
    metadata:
      labels: {app: telegram}
    spec:
      containers:
        - name: telegram
          image: $image
          command: [python3, -u, /stub/e2e_telegram.py]
          volumeMounts: [{name: stub, mountPath: /stub}]
      volumes: [{name: stub, configMap: {name: stub}}]
---
apiVersion: v1
kind: Service
metadata:
  name: telegram
spec:
  selector: {app: telegram}
  ports: [{port: 8081, targetPort: 8081}]
YAML
kubectl -n e2e-telegram rollout status deployment/telegram --timeout=180s >/dev/null
telegram_api=http://telegram.e2e-telegram.svc:8081

telegram_calls() {
	kubectl -n e2e-telegram exec deployment/telegram -- python3 -c \
		'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8081/calls").read().decode())'
}

# The bot may reach the stand-in: a policy the suite alone applies.
allow_stub() { # <namespace>
	kubectl apply -f - >/dev/null <<YAML
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: e2e-bot-reaches-the-stub
  namespace: $1
spec:
  podSelector:
    matchLabels: {app: itgraph-bot}
  policyTypes: [Egress]
  egress:
    - to:
        - namespaceSelector:
            matchLabels: {kubernetes.io/metadata.name: e2e-telegram}
      ports: [{protocol: TCP, port: 8081}]
YAML
}

role() { # <entry> [ansible-playbook arguments]
	local entry=$1
	shift
	(
		cd "$here" &&
			ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH="$here" ANSIBLE_LOCALHOST_WARNING=False \
				ANSIBLE_INVENTORY_UNPARSED_WARNING=False ANSIBLE_CONFIG="$work/none.cfg" \
				uv run --project "$root" ansible-playbook "$here/e2e.yml" \
				-e entry="$entry" \
				-e "{\"itgraph_kubectl\": [\"kubectl\"]}" \
				-e itgraph_image="$image" \
				-e itgraph_postgres_image="$postgres_image" \
				-e itgraph_proxy_image="$proxy_image" \
				-e itgraph_telegram_api="$telegram_api" \
				"$@"
	)
}

psql_in() { # <namespace> <sql>: one value
	kubectl -n "$1" exec -i statefulset/itgraph-postgres -c postgres -- \
		psql -X -U itgraph -d itgraph -tA -v ON_ERROR_STOP=1 -c "$2"
}

# A pod in the namespace with these labels, under what restricted asks, running a
# command of the application's image to its end. Prints its logs; returns its outcome.
probe() { # <namespace> <name> <labels json> <command as json array> [extra spec json]
	local extra='{}'
	[ "$#" -lt 5 ] || extra=$5
	kubectl -n "$1" delete pod "$2" --ignore-not-found >/dev/null
	jq -n --arg ns "$1" --arg name "$2" --arg image "$image" \
		--argjson labels "$3" --argjson command "$4" --argjson extra "$extra" '
		{apiVersion: "v1", kind: "Pod",
		 metadata: {name: $name, namespace: $ns, labels: $labels},
		 spec: ({restartPolicy: "Never", activeDeadlineSeconds: 120,
		   automountServiceAccountToken: false,
		   securityContext: {runAsNonRoot: true, runAsUser: 3000, runAsGroup: 3000,
		     seccompProfile: {type: "RuntimeDefault"}},
		   initContainers: [{name: "settle", image: $image, command: ["sleep", "3"],
		     securityContext: {readOnlyRootFilesystem: true, allowPrivilegeEscalation: false,
		       capabilities: {drop: ["ALL"]}}}],
		   containers: [{name: "probe", image: $image, command: $command,
		     securityContext: {readOnlyRootFilesystem: true, allowPrivilegeEscalation: false,
		       capabilities: {drop: ["ALL"]}}}]} * $extra)}' |
		kubectl create -f - >/dev/null || fail "probe $2 could not be created"
	local tries=0 phase=""
	while :; do
		phase="$(kubectl -n "$1" get pod "$2" -o jsonpath='{.status.phase}')"
		case "$phase" in Succeeded | Failed) break ;; esac
		tries=$((tries + 1))
		[ "$tries" -lt 150 ] || fail "probe $2 never ended"
		sleep 2
	done
	kubectl -n "$1" logs "pod/$2" 2>&1 || true
	kubectl -n "$1" delete pod "$2" --ignore-not-found --wait=false >/dev/null
	[ "$phase" = Succeeded ]
}

connects() { # <host> <port>, as a command for a probe
	printf '["python3", "-c", "import socket; socket.create_connection((\\"%s\\", %s), 5)"]' "$1" "$2"
}

uids() { # <namespace> the long-running pods' uids
	kubectl -n "$1" get pods -l 'app in (itgraph-bot,itgraph-metrics,itgraph-proxy,itgraph-postgres)' \
		-o jsonpath='{range .items[*]}{.metadata.name}={.metadata.uid}{"\n"}{end}' | sort
}

# --- a fresh install -------------------------------------------------------------

say 'a check on a new cluster says what it leaves out, and changes nothing'
role main --check >"$work/check-new.log" 2>&1 || {
	cat "$work/check-new.log" >&2
	fail 'a check on a new cluster failed'
}
grep -q 'does not exist yet' "$work/check-new.log" || fail 'a check on a new namespace did not say so'
[ -z "$(kubectl get namespace "$namespace" -o name --ignore-not-found)" ] || fail 'a check created the namespace'
ok 'a check on a new cluster'

kubectl create namespace "$namespace" >/dev/null
allow_stub "$namespace"

say 'the role on a fresh volume'
role main >"$work/fresh.log" 2>&1 || {
	tail -60 "$work/fresh.log" >&2
	kubectl -n "$namespace" get pods >&2 || true
	fail 'the role failed on a fresh volume'
}
ok 'the role runs on a fresh volume, with the collector pod failing closed as it must'

[ "$(kubectl get namespace "$namespace" -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}')" = restricted ] ||
	fail 'the namespace does not enforce restricted'
ok 'the namespace enforces restricted'

[ "$(kubectl -n "$namespace" get deployment itgraph-watch -o jsonpath='{.spec.replicas}')" = 0 ] ||
	fail 'the collector is not at zero replicas'
ok 'the collector is at zero replicas'

[ "$(psql_in "$namespace" 'SELECT count(*) FROM alembic_version')" = 1 ] || fail 'no schema'
kubectl -n "$namespace" exec statefulset/itgraph-postgres -c postgres -- sh -c 'id -u; touch /should-not-be-written 2>/dev/null && echo writable' >"$work/pg.txt" || true
[ "$(cat "$work/pg.txt")" = 999 ] || fail "the database's container: $(cat "$work/pg.txt")"
ok 'Postgres runs as uid 999 with a read-only root, on a fresh volume'

kubectl -n "$namespace" get events --field-selector reason=FailedCreate -o name 2>/dev/null | grep -q . &&
	fail 'a pod was not admitted'
ok 'every pod admitted'

say 'a second run changes nothing'
before="$(uids "$namespace")"
role main >"$work/second.log" 2>&1 || {
	tail -40 "$work/second.log" >&2
	fail 'a second run failed'
}
[ "$(uids "$namespace")" = "$before" ] || fail 'a second run restarted something'
grep -q 'restarted: none' "$work/second.log" || fail 'a second run did not say it restarted nothing'
grep -qE 'TASK \[itgraph : Apply ' "$work/second.log" || fail 'a second run did not reach the applies'
ok 'a second run keeps the pods'

say 'a new bot token restarts the bot alone'
bot_before="$(kubectl -n "$namespace" get pods -l app=itgraph-bot -o jsonpath='{.items[0].metadata.uid}')"
others_before="$(kubectl -n "$namespace" get pods -l 'app in (itgraph-metrics,itgraph-proxy,itgraph-postgres)' -o jsonpath='{range .items[*]}{.metadata.uid}{"\n"}{end}' | sort)"
role main -e itgraph_bot_token=333:fakeNewBotTokenSetE >"$work/token.log" 2>&1 || {
	tail -40 "$work/token.log" >&2
	fail 'a run with a new token failed'
}
[ "$(kubectl -n "$namespace" get pods -l app=itgraph-bot -o jsonpath='{.items[0].metadata.uid}')" != "$bot_before" ] || fail 'the bot did not restart'
[ "$(kubectl -n "$namespace" get pods -l 'app in (itgraph-metrics,itgraph-proxy,itgraph-postgres)' -o jsonpath='{range .items[*]}{.metadata.uid}{"\n"}{end}' | sort)" = "$others_before" ] ||
	fail 'something else restarted'
ok 'a new bot token restarts the bot alone'
role main >"$work/token-back.log" 2>&1 || fail 'putting the token back failed'

say 'a check on a namespace with a change to show'
role main --check --diff -e itgraph_alert_chat_id=100000009 >"$work/check.log" 2>&1 || {
	tail -30 "$work/check.log" >&2
	fail 'a check with a change failed'
}
[ "$(kubectl -n "$namespace" get secret itgraph-bot -o jsonpath='{.data.ALERT_CHAT_ID}' | base64 -d)" = 100000003 ] ||
	fail 'a check changed something'
ok 'a check changes nothing'

say 'two runs at once'
role main >"$work/one.log" 2>&1 &
first=$!
role main >"$work/two.log" 2>&1 &
second=$!
first_status=0
second_status=0
wait "$first" || first_status=$?
wait "$second" || second_status=$?
if [ "$first_status" -ne 0 ] && [ "$second_status" -ne 0 ]; then
	tail -20 "$work/one.log" "$work/two.log" >&2
	fail 'of two runs at once, neither finished'
fi
[ -z "$(kubectl -n "$namespace" get lease itgraph-deploy -o name --ignore-not-found)" ] || fail 'the lock was left held'
ok 'of two runs at once, one finishes and the lock is released'

# --- the network -----------------------------------------------------------------

say 'what each label may reach'
database="$(connects itgraph-postgres 5432)"
proxy="$(connects itgraph-proxy 1080)"
public="$(connects 1.1.1.1 443)"
reach_database='{"itgraph/database": "true"}'
reach_proxy='{"itgraph/telegram": "true"}'
reach_internet='{"itgraph/internet": "true"}'
collector='{"itgraph/database": "true", "itgraph/telegram": "true"}'

probe "$namespace" e2e-collector-db "$collector" "$database" >/dev/null || fail 'the collector cannot reach the database'
ok 'the collector reaches the database'
probe "$namespace" e2e-collector-proxy "$collector" "$proxy" >/dev/null || fail 'the collector cannot reach the proxy'
ok 'the collector reaches the proxy'
probe "$namespace" e2e-collector-out "$collector" "$public" >/dev/null && fail 'the collector reached the internet directly'
ok 'the collector cannot go direct'
probe "$namespace" e2e-analysis-out "$reach_database" "$public" >/dev/null && fail 'an analysis job reached the internet'
ok 'an analysis job cannot go out'
probe "$namespace" e2e-analysis-proxy "$reach_database" "$proxy" >/dev/null && fail 'an analysis job reached the proxy'
ok 'an analysis job cannot reach the proxy'
probe "$namespace" e2e-nothing-db '{"itgraph/none": "true"}' "$database" >/dev/null && fail 'a pod with no label reached the database'
ok 'a pod with no label reaches nothing'
probe "$namespace" e2e-internet "$reach_internet" "$public" >/dev/null || fail 'a pod with internet could not leave'
ok 'a pod labelled so reaches the internet'
probe "$namespace" e2e-internet-db "$reach_internet" "$database" >/dev/null && fail 'internet alone reached the database'
ok 'internet alone does not reach the database'

say 'the fixed address, as a tunnel reaches it'
kubectl create namespace e2e-host >/dev/null 2>&1 || true
probe e2e-host e2e-host-network '{}' "$(connects 10.43.0.250 5432)" '{"hostNetwork": true, "securityContext": {"runAsNonRoot": true, "runAsUser": 3000}}' >/dev/null ||
	fail 'the node cannot reach the fixed address'
ok 'the node reaches the fixed address'
tunnel() { # <the environment psql runs under>: as a tunnel's other end would connect
	probe e2e-host e2e-host-psql '{}' \
		"[\"sh\", \"-c\", \"$1 psql -X -w -h 10.43.0.250 -U itgraph -d itgraph -tAc 'SELECT 1' 2>&1\"]" \
		'{"hostNetwork": true, "securityContext": {"runAsNonRoot": true, "runAsUser": 3000}}'
}
tunnel PGPASSWORD=fakeOwnerPasswordSetEeeee >/dev/null || fail "the fixed address refused a role's password"
ok "the fixed address takes a role's password"
if refused="$(tunnel PGPASSWORD=fake-wrong-password)"; then
	fail 'the fixed address took a wrong password'
fi
case "$refused" in *'password authentication failed'*) ;; *) fail "not refused for its password: $refused" ;; esac
if refused="$(tunnel PGPASSWORD=)"; then
	fail 'the fixed address took no password'
fi
case "$refused" in *'no password supplied'*) ;; *) fail "not refused for lack of a password: $refused" ;; esac
ok 'the fixed address refuses a wrong password, and none'

say 'the series, for the scraper alone'
kubectl create namespace monitoring >/dev/null 2>&1 || true
scrape='["python3", "-c", "import urllib.request; r = urllib.request.urlopen(\"http://itgraph-metrics.itgraph.svc:9090/metrics\", timeout=10); print(r.read().decode())"]'
probe monitoring e2e-scraper '{"app.kubernetes.io/name": "prometheus"}' "$scrape" >"$work/series.txt" ||
	fail "the scraper cannot read the series: $(cat "$work/series.txt")"
grep -q '^itgraph_telegram_quiet_hours ' "$work/series.txt" || fail 'no series in the scrape'
grep -q '^itgraph_youtube_oldest_payload_age_seconds' "$work/series.txt" && fail 'an age series with no API data'
ok 'the scraper reads the series'
probe monitoring e2e-not-scraper '{"app": "something-else"}' "$scrape" >/dev/null && fail 'another pod read the series'
ok 'another pod in that namespace cannot'

# --- the door for operators ------------------------------------------------------

say 'scripts/on-host.sh, through a stub ssh, on the real cluster'
mkdir -p "$work/bin"
cat >"$work/bin/ssh" <<'STUB'
#!/usr/bin/env bash
exec bash -c "${@: -1}"
STUB
cat >"$work/bin/sudo" <<'STUB'
#!/usr/bin/env bash
shift
exec "$@"
STUB
cat >"$work/bin/k3s" <<'STUB'
#!/usr/bin/env bash
shift
exec kubectl "$@"
STUB
chmod +x "$work/bin/ssh" "$work/bin/sudo" "$work/bin/k3s"
on_host() {
	PATH="$work/bin:$PATH" ITGRAPH_SSH=e2e@nowhere.invalid "$root/scripts/on-host.sh" "$@"
}
on_host channels >"$work/channels.txt" || fail 'a command through the script failed'
ok 'a command runs through the script, and comes back'
on_host channels --nonsense >/dev/null 2>&1 && fail 'a failing command did not fail through the script'
ok 'a failing command fails through the script'
sleep 3
[ -z "$(kubectl -n "$namespace" get jobs -l itgraph/console=true -o name)" ] || fail 'a console Job was left behind'
ok 'nothing of it is left behind'

# --- restoring -------------------------------------------------------------------

say 'a dump of seeded rows, as a backup run makes it'
kubectl -n "$namespace" exec -i statefulset/itgraph-postgres -c postgres -- \
	psql -X -q -U itgraph -d itgraph -v ON_ERROR_STOP=1 -f - <"$here/e2e_seed.sql"
# As the previous release left it: the revision that clears people from the raw layer
# changes no table, so the version one step back is that release's database, and the
# restore has to run the revision to clear what the seed put there.
psql_in "$namespace" "UPDATE alembic_version SET version_num = 'b7d2e4f19a30'" >/dev/null
kubectl -n "$namespace" delete job --all >/dev/null
kubectl -n "$namespace" create job e2e-dump --from=cronjob/itgraph-backup >/dev/null
kubectl -n "$namespace" wait --for=condition=complete job/e2e-dump --timeout=180s >/dev/null || {
	kubectl -n "$namespace" logs job/e2e-dump >&2 || true
	fail 'the backup did not complete'
}
kubectl -n "$namespace" delete job e2e-dump >/dev/null
dump_from_claim() { # <file on this machine>
	kubectl -n "$namespace" delete pod e2e-cat --ignore-not-found >/dev/null
	jq -n --arg ns "$namespace" --arg image "$image" '
		{apiVersion: "v1", kind: "Pod", metadata: {name: "e2e-cat", namespace: $ns},
		 spec: {restartPolicy: "Never", activeDeadlineSeconds: 300, automountServiceAccountToken: false,
		   securityContext: {runAsNonRoot: true, runAsUser: 3000, runAsGroup: 3000, seccompProfile: {type: "RuntimeDefault"}},
		   containers: [{name: "cat", image: $image, command: ["sleep", "280"],
		     securityContext: {readOnlyRootFilesystem: true, allowPrivilegeEscalation: false, capabilities: {drop: ["ALL"]}},
		     volumeMounts: [{name: "backups", mountPath: "/backups", readOnly: true}]}],
		   volumes: [{name: "backups", persistentVolumeClaim: {claimName: "itgraph-backups"}}]}}' |
		kubectl create -f - >/dev/null
	kubectl -n "$namespace" wait --for=condition=Ready pod/e2e-cat --timeout=120s >/dev/null
	kubectl -n "$namespace" exec e2e-cat -- cat /backups/weekly/full-LATEST.dump >"$1"
	kubectl -n "$namespace" delete pod e2e-cat --wait=false >/dev/null
}
dump_from_claim "$work/seeded.dump"
[ -s "$work/seeded.dump" ] || fail 'no dump came out of the claim'
ok 'a backup made a dump of the seeded rows'

say 'dumps with API data in them are refused, and bad files touch nothing'
counts_before="$(psql_in "$namespace" 'SELECT count(*) FROM channels')"
role restore -e itgraph_restore_from="$work/no-such-file.dump" >"$work/missing.log" 2>&1 && fail 'a missing file was restored'
printf 'not a dump\n' >"$work/text.dump"
role restore -e itgraph_restore_from="$work/text.dump" >"$work/text.log" 2>&1 && fail 'a text file was restored'
grep -q 'is not a dump pg_restore can read' "$work/text.log" || fail 'a text file was not named as such'
head -c 400 "$work/seeded.dump" >"$work/cut-toc.dump"
role restore -e itgraph_restore_from="$work/cut-toc.dump" >"$work/cut-toc.log" 2>&1 && fail 'a dump cut in its table of contents was restored'
[ "$(psql_in "$namespace" 'SELECT count(*) FROM channels')" = "$counts_before" ] || fail 'a refused file changed the database'
ok 'a missing file, a text file and a dump cut in its table of contents touch nothing'

say 'a dump cut short past its table of contents reads as a dump, and fails in the load'
# Halfway: past the table of contents `pg_restore --list` reads, so that only the load
# can find it out, and into the rows. A dump written to a file ends in a second copy
# of its table of contents, so a little off the end costs nothing, as it should.
size="$(wc -c <"$work/seeded.dump" | tr -d ' ')"
head -c $((size / 2)) "$work/seeded.dump" >"$work/cut-data.dump"
role restore -e itgraph_restore_from="$work/cut-data.dump" >"$work/cut-data.log" 2>&1 && fail 'a dump cut in its data was restored'
grep -q 'emptied' "$work/cut-data.log" || fail 'the failure did not say the database was emptied'
[ "$(psql_in "$namespace" "SELECT to_regclass('public.channels') IS NULL")" = t ] || fail 'a partial load was kept'
[ "$(kubectl -n "$namespace" get cronjob itgraph-detect -o jsonpath='{.spec.suspend}')" = false ] || fail 'a failed restore left a schedule suspended'
[ -z "$(kubectl -n "$namespace" get lease itgraph-deploy -o name --ignore-not-found)" ] || fail 'a failed restore left the lock held'
ok 'a dump cut past its table of contents leaves the database empty, the schedules back and the lock free'

say 'a suspended schedule stays so'
kubectl -n "$namespace" patch cronjob itgraph-baselines --type=merge -p '{"spec": {"suspend": true}}' >/dev/null

say 'the restore'
role restore -e itgraph_restore_from="$work/seeded.dump" >"$work/restore.log" 2>&1 || {
	tail -40 "$work/restore.log" >&2
	kubectl -n "$namespace" get pods >&2 || true
	fail 'the restore failed'
}
grep -q 'every table.s rows matching it' "$work/restore.log" || fail 'the restore did not say it compared the rows'
ok 'the restore completes'

[ "$(kubectl -n "$namespace" get cronjob itgraph-baselines -o jsonpath='{.spec.suspend}')" = true ] || fail 'a suspended schedule was resumed'
[ "$(kubectl -n "$namespace" get cronjob itgraph-detect -o jsonpath='{.spec.suspend}')" = false ] || fail 'a schedule was left suspended'
kubectl -n "$namespace" patch cronjob itgraph-baselines --type=merge -p '{"spec": {"suspend": false}}' >/dev/null
ok 'the schedules are as they were found'

read_rows() { psql_in "$namespace" "$1"; }
[ "$(read_rows "SELECT count(*) FROM channels WHERE discovered_via = 'own_subscriptions'")" = 0 ] || fail 'subscriptions in the copy'
[ "$(read_rows 'SELECT count(*) FROM channels')" = 3 ] || fail "channels in the copy: $(read_rows 'SELECT count(*) FROM channels')"
[ "$(read_rows 'SELECT count(*) FROM pending_mentions')" = 0 ] || fail 'pending mentions in the copy'
[ "$(read_rows "SELECT count(*) FROM yt_channels WHERE reject_note IS NOT NULL OR kind_note IS NOT NULL")" = 0 ] || fail 'notes in the copy'
[ "$(read_rows 'SELECT count(*) FROM affiliation_candidates WHERE decision_note IS NOT NULL')" = 0 ] || fail 'a note on a pair in the copy'
[ "$(read_rows 'SELECT count(*) FROM affiliation_candidates')" = 1 ] || fail 'the decision on a pair did not come across'
[ "$(read_rows 'SELECT count(*) FROM yt_channels')" = 1 ] || fail 'the YouTube records did not come across'
[ "$(read_rows 'SELECT count(*) FROM yt_raw_channels')" = 0 ] || fail 'API data in the copy'
payload="$(read_rows "SELECT payload FROM raw_messages WHERE msg_id = 10")"
case "$payload" in *777000111* | *"A Fake"* | *"Another Fake"*) fail "a person in the copy: $payload" ;; esac
case "$payload" in *"a public post"*) ;; *) fail 'the text did not come across' ;; esac
[ "$(read_rows "SELECT payload->'fwd_from'->'from_id'->>'channel_id' FROM raw_messages WHERE msg_id = 11")" = 4 ] || fail 'the graph did not come across'
[ "$(read_rows "SELECT payload->'reactions'->'top_reactors'->0->'peer_id'->>'channel_id' FROM raw_messages WHERE msg_id = 10")" = 4 ] ||
	fail 'the paid reactors did not come across as the channel alone'
[ "$(read_rows "SELECT jsonb_array_length(payload->'reactions'->'top_reactors') FROM raw_messages WHERE msg_id = 10")" = 1 ] ||
	fail 'a paid reactor who is a person came across'
ok 'on the real rows, the copy names nobody and holds no API data, and the graph is there'

trial_alerts="$(read_rows 'SELECT count(*) FROM alerts WHERE band = 2147483647')"
if grep -q 'Delivered through the bot: could not be proven' "$work/restore.log"; then
	[ "$trial_alerts" = 0 ] || fail 'an alert raised in quiet hours'
	ok 'inside quiet hours, the alert is reported as not proven'
else
	[ "$(read_rows 'SELECT count(*) FROM alerts WHERE band = 2147483647 AND delivered_at IS NOT NULL')" = 1 ] ||
		fail 'the bot did not deliver the synthetic alert'
	telegram_calls >"$work/telegram.txt"
	grep -q '"method": "sendMessage"' "$work/telegram.txt" || fail 'the stand-in saw no message'
	[ "$(grep -c '"method": "sendMessage"' "$work/telegram.txt")" = 1 ] || fail 'the undelivered alert of the dump was sent'
	ok 'the bot delivers one alert, the synthetic one, and none of the dump'
fi
[ "$(read_rows 'SELECT count(*) FROM baseline_runs WHERE completed_at IS NOT NULL')" -ge 1 ] || fail 'no baseline run on the copy'
ok 'a baseline run was written on the copy'

say 'the passes on a copy that receives nothing new'
kubectl -n "$namespace" create job e2e-detect --from=cronjob/itgraph-detect >/dev/null
kubectl -n "$namespace" wait --for=condition=complete job/e2e-detect --timeout=180s >/dev/null || fail 'detection failed on the copy'
kubectl -n "$namespace" delete job e2e-detect >/dev/null
ok 'detection runs on the copy'

say 'a second restore, from a dump of the neutralised copy'
dump_from_claim "$work/neutralised.dump"
role restore -e itgraph_restore_from="$work/neutralised.dump" >"$work/restore2.log" 2>&1 || {
	tail -40 "$work/restore2.log" >&2
	fail 'a second restore failed'
}
[ "$(read_rows 'SELECT count(*) FROM channels')" = 3 ] || fail 'the second restore lost channels'
ok 'a dump of the neutralised copy restores'

say 'a copy through age and back, restored as any other dump'
docker run --rm -i --entrypoint sh itgraph-e2e:local -c '
	cd /tmp || exit 1
	age-keygen -o identity 2>recipient
	recipient=$(sed -n "s/^Public key: //p" recipient)
	cat >dump
	age -r "$recipient" -o dump.age dump
	age -d -i identity -o back dump.age
	cat back' <"$work/seeded.dump" >"$work/through-age.dump" 2>"$work/age.log" || {
	cat "$work/age.log" >&2
	fail 'age could not take the dump through and back'
}
cmp -s "$work/seeded.dump" "$work/through-age.dump" || fail 'the dump came back from age changed'
role restore -e itgraph_restore_from="$work/through-age.dump" >"$work/restore3.log" 2>&1 || {
	tail -40 "$work/restore3.log" >&2
	fail 'a restore of a copy through age failed'
}
ok 'a copy through age restores as any other dump'

say 'a dump with API data in it, or a person at head, empties the database rather than keeping it'
# A person at head means production holds one: the collector's rule missed a shape,
# at a path no statement ever named. Dumped while the copy is whole, then taken out.
psql_in "$namespace" "INSERT INTO raw_messages (channel_id, msg_id, payload) VALUES (4, 12, '{\"_\": \"Message\", \"id\": 12, \"reply_to\": {\"_\": \"MessageReplyHeader\", \"reply_to_peer_id\": {\"_\": \"PeerUser\", \"user_id\": 777000111}}}')" >/dev/null
kubectl -n "$namespace" exec statefulset/itgraph-postgres -c postgres -- \
	pg_dump -U itgraph -d itgraph --format=custom >"$work/with-a-person.dump"
psql_in "$namespace" "DELETE FROM raw_messages WHERE msg_id = 12" >/dev/null
psql_in "$namespace" "INSERT INTO yt_raw_channels (channel_id, payload, fetched_at) VALUES ('UCfake000000000000000000', '{}', now())" >/dev/null
kubectl -n "$namespace" exec statefulset/itgraph-postgres -c postgres -- \
	pg_dump -U itgraph -d itgraph --format=custom >"$work/with-api-data.dump"
role restore -e itgraph_restore_from="$work/with-api-data.dump" >"$work/restore4.log" 2>&1 && fail 'a dump with API data was restored'
grep -q 'emptied' "$work/restore4.log" || fail 'the failure did not say the database was emptied'
[ "$(read_rows "SELECT to_regclass('public.channels') IS NULL")" = t ] || fail 'the database was not emptied'
ok 'a dump with API data is refused, and leaves the database empty'
role restore -e itgraph_restore_from="$work/with-a-person.dump" >"$work/restore5.log" 2>&1 && fail 'a dump naming a person at head was restored'
grep -q 'users in posts: 1' "$work/restore5.log" || fail 'the failure did not name the person it found'
grep -q 'emptied' "$work/restore5.log" || fail 'the failure did not say the database was emptied'
[ "$(read_rows "SELECT to_regclass('public.channels') IS NULL")" = t ] || fail 'the database was not emptied'
ok 'a dump at head naming a person is refused, and leaves the database empty'
role main >"$work/after.log" 2>&1 || {
	tail -40 "$work/after.log" >&2
	fail 'the role could not bring the emptied environment back'
}
ok 'the next run brings an emptied environment back'

# --- taking over what the previous path made ---------------------------------------

say 'a namespace as the previous path left it'
sed -e "s|NAMESPACE|$takeover|g" -e "s|POSTGRES_IMAGE|$postgres_image|g" -e "s|IMAGE|$image|g" -e "s|CLASS|$class|g" \
	"$here/e2e_previous.yaml" |
	kubectl apply --server-side --field-manager=itgraph -f - >/dev/null
kubectl -n "$takeover" rollout status statefulset/itgraph-postgres --timeout=300s >/dev/null
kubectl -n "$takeover" rollout status deployment/itgraph-console --timeout=180s >/dev/null
# Its probe, as the previous path wrote it, reads the socket, which answers before
# the image's first initialization is over.
tries=0
until kubectl -n "$takeover" exec statefulset/itgraph-postgres -c postgres -- pg_isready -q -h 127.0.0.1 2>/dev/null; do
	tries=$((tries + 1))
	[ "$tries" -lt 60 ] || fail 'the previous database never listened'
	sleep 2
done
root_owner="$(kubectl -n "$takeover" exec statefulset/itgraph-postgres -c postgres -- sh -c 'stat -c %u /var/lib/postgresql/data/pgdata')"
[ "$root_owner" = 999 ] || fail "a root-started image left pgdata to $root_owner"
kubectl -n "$takeover" exec deployment/itgraph-console -c itgraph -- alembic upgrade a89cb273f885 >"$work/old-schema.log" 2>&1 || {
	cat "$work/old-schema.log" >&2
	fail 'the old schema could not be made'
}
kubectl -n "$takeover" exec -i statefulset/itgraph-postgres -c postgres -- \
	psql -X -q -U itgraph -d itgraph -v ON_ERROR_STOP=1 -f - <"$here/e2e_seed.sql"
psql_in "$takeover" 'CREATE ROLE itgraph_bot NOLOGIN' >/dev/null 2>&1 || true
kubectl -n "$takeover" exec deployment/itgraph-console -c itgraph -- sh -c \
	'printf "a session-shaped file" > /session/itgraph.session; chmod 600 /session/itgraph.session; mkdir -p /backups/weekly; printf "an old dump" > /backups/weekly/full-20260101T000000Z.dump'
checksum_of() { # <namespace>: the session's checksum, read by a pod of its own
	kubectl -n "$1" delete pod e2e-sum --ignore-not-found >/dev/null
	jq -n --arg ns "$1" --arg image "$image" '
		{apiVersion: "v1", kind: "Pod", metadata: {name: "e2e-sum", namespace: $ns},
		 spec: {restartPolicy: "Never", activeDeadlineSeconds: 120, automountServiceAccountToken: false,
		   securityContext: {runAsNonRoot: true, runAsUser: 3000, runAsGroup: 3000, seccompProfile: {type: "RuntimeDefault"}},
		   containers: [{name: "sum", image: $image, command: ["sha256sum", "/session/itgraph.session"],
		     securityContext: {readOnlyRootFilesystem: true, allowPrivilegeEscalation: false, capabilities: {drop: ["ALL"]}},
		     volumeMounts: [{name: "session", mountPath: "/session", readOnly: true}]}],
		   volumes: [{name: "session", persistentVolumeClaim: {claimName: "itgraph-session"}}]}}' |
		kubectl create -f - >/dev/null
	local tries=0
	until [ "$(kubectl -n "$1" get pod e2e-sum -o jsonpath='{.status.phase}')" = Succeeded ]; do
		tries=$((tries + 1))
		[ "$tries" -lt 60 ] || fail 'the checksum pod never ended'
		sleep 2
	done
	kubectl -n "$1" logs pod/e2e-sum | awk '{print $1}'
	kubectl -n "$1" delete pod e2e-sum --wait=false >/dev/null
}
session_before="$(checksum_of "$takeover")"
volumes_before="$(kubectl -n "$takeover" get pvc -o jsonpath='{range .items[*]}{.metadata.name}={.spec.volumeName}{"\n"}{end}' | sort)"
rows_before="$(psql_in "$takeover" 'SELECT count(*) FROM raw_messages')"
allow_stub "$takeover"

say 'the role takes it over'
role main -e itgraph_namespace="$takeover" -e itgraph_postgres_access_ip=10.43.0.251 >"$work/takeover.log" 2>&1 || {
	tail -60 "$work/takeover.log" >&2
	kubectl -n "$takeover" get pods >&2 || true
	fail 'the role could not take over'
}
ok 'the role takes over a namespace the previous path made'

[ "$(kubectl -n "$takeover" exec statefulset/itgraph-postgres -c postgres -- id -u)" = 999 ] || fail 'the database is not uid 999'
ok 'Postgres runs as uid 999 on files a root-started image wrote'
[ "$(psql_in "$takeover" 'SELECT count(*) FROM raw_messages')" = "$rows_before" ] || fail 'rows were lost in the takeover'
ok 'every row is still there'
[ "$(checksum_of "$takeover")" = "$session_before" ] || fail 'the session changed'
ok "the session's checksum is unchanged"
[ "$(kubectl -n "$takeover" get pvc -o jsonpath='{range .items[*]}{.metadata.name}={.spec.volumeName}{"\n"}{end}' | sort)" = "$volumes_before" ] ||
	fail 'a claim moved'
ok 'every claim is bound as it was'
for gone in deployment/itgraph-console cronjob/itgraph-alerts cronjob/itgraph-score secret/itgraph-env networkpolicy/deny-all-ingress; do
	[ -z "$(kubectl -n "$takeover" get "$gone" -o name --ignore-not-found)" ] || fail "$gone is still there"
done
ok 'the old objects are gone, each by name'
grep -q 'dump at /backups/weekly/full-' "$work/takeover.log" || fail 'the migration did not report its dump'
ok 'the migration took a dump first, and said where'
[ "$(kubectl -n "$takeover" get namespace "$takeover" -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}')" = restricted ] ||
	fail 'the taken-over namespace does not enforce restricted'
ok 'the taken-over namespace enforces restricted'
[ "$(psql_in "$takeover" 'SELECT rolcanlogin FROM pg_roles WHERE rolname = '"'"'itgraph_metrics'"'"'')" = t ] || fail 'the exporter has no login'
kubectl -n "$takeover" rollout status deployment/itgraph-metrics --timeout=60s >/dev/null || fail 'the exporter is not up'
ok 'the exporter is up, through its own role'

printf 'e2e: %s checks passed\n' "$pass"
