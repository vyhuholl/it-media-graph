#!/usr/bin/env bash
# Run an `itgraph` command in the collection namespace's console, from
# this machine.
#
# The working database lives on the collection host, in the namespace;
# this machine holds only old dumps. Review is routine work at volume
# and happens here, in a local editor — so the command runs there, in
# the console pod, and its standard input and output come back here.
# `kubectl exec` keeps the two output streams apart and returns the
# command's exit status:
#
#   scripts/on-host.sh yt review-export > ~/itgraph-review/batch.review.tsv
#   scripts/on-host.sh yt review-apply - < ~/itgraph-review/batch.review.tsv
#   scripts/on-host.sh yt add --from-file - --failures - < seeds.txt > failed.txt
#
# A command that needs the Telegram session — `resolve`, `backfill`,
# `metadata` — cannot run while the loop holds it: stop the loop, run
# the command, start the loop. The lease inside the namespace refuses
# the second holder, which is the safety net if this is forgotten:
#
#   scripts/on-host.sh --loop stop
#   scripts/on-host.sh resolve
#   scripts/on-host.sh --loop start
#
# Not an SSH tunnel to Postgres: code here could run ahead of the host's
# schema, and DATABASE_URL would switch between the working database and
# a local one — the keystroke-identical hazard db/guard.py exists for.
#
# The host is the first one under [collector] in deploy/inventory.ini,
# the file ansible already uses; it is reached as root, which is how the
# playbook drives it. The host's login shell is assumed to be bash,
# which is what `printf %q` quotes for.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
INVENTORY=${ITGRAPH_INVENTORY:-$REPO/deploy/inventory.ini}

usage() {
	echo "usage: $0 <itgraph arguments>   e.g. $0 yt channels" >&2
	echo "       $0 --loop stop|start" >&2
}

if [ "$#" -eq 0 ]; then
	usage
	exit 64
fi

if [ ! -f "$INVENTORY" ]; then
	echo "no deploy inventory at $INVENTORY: copy" \
		"deploy/inventory.ini.example and fill in the host" >&2
	exit 1
fi

# The first host line under [collector], skipping blanks and comments.
line=$(awk '
	/^\[collector\]/ { inside = 1; next }
	/^\[/ { inside = 0 }
	inside && NF && $1 !~ /^[#;]/ { print; exit }
' "$INVENTORY")

host=""
user=""
for field in $line; do
	case $field in
	ansible_host=*) host=${field#ansible_host=} ;;
	ansible_user=*) user=${field#ansible_user=} ;;
	esac
done

if [ -z "$host" ] || [ -z "$user" ]; then
	echo "no ansible_host and ansible_user under [collector] in" \
		"$INVENTORY" >&2
	exit 1
fi

if [ "$1" = "--loop" ]; then
	# Scale, then wait for what was scaled to be true: a stopped loop is
	# a pod that is gone, a started one a rollout that finished. Without
	# the wait, the session-needing command right after `--loop stop`
	# races the collector's shutdown — and loses its lease.
	case "${2-}" in
	stop)
		exec ssh -T "$user@$host" \
			"k3s kubectl -n itgraph scale deploy/itgraph-watch --to=0 \
			&& k3s kubectl -n itgraph wait --for=delete pod \
			-l app=itgraph-watch --timeout=180s"
		;;
	start)
		exec ssh -T "$user@$host" \
			"k3s kubectl -n itgraph scale deploy/itgraph-watch --to=1 \
			&& k3s kubectl -n itgraph rollout status \
			deploy/itgraph-watch -n itgraph --timeout=180s"
		;;
	*)
		usage
		exit 64
		;;
	esac
fi

# Every argument quoted on its own, so one with spaces or quotes arrives
# as the one argument it was here.
remote="k3s kubectl -n itgraph exec -i deploy/itgraph-console -c itgraph -- itgraph"
for arg in "$@"; do
	remote+=" $(printf '%q' "$arg")"
done

# -T: no terminal, so standard input reaches the command byte for byte.
# exec: ssh's exit status, which is the remote command's, is the script's.
exec ssh -T "$user@$host" "$remote"
