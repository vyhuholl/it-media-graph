#!/usr/bin/env bash
# Run an `itgraph` command in the collection namespace, from this machine.
#
# The working database lives on the collection host; this machine holds nothing
# of it. Review is routine work at volume and happens here, in a local editor —
# so the command runs there, in a Job of its own, and its standard input and
# output come back here. `kubectl exec` keeps the two output streams apart and
# returns the command's exit status:
#
#   scripts/on-host.sh yt review-export > ~/itgraph-review/batch.review.tsv
#   scripts/on-host.sh yt review-apply - < ~/itgraph-review/batch.review.tsv
#   scripts/on-host.sh yt add --from-file - --failures - < seeds.txt > failed.txt
#
# A command that needs the Telegram session — `resolve`, `backfill`, `metadata` —
# asks for it, and cannot run while the loop holds it: stop the loop, run the
# command, start the loop. The lease inside the namespace refuses the second
# holder, which is the safety net if this is forgotten. A pod with the session
# holds the Telegram credentials too, and can reach only the proxy:
#
#   scripts/on-host.sh --loop stop
#   scripts/on-host.sh --session resolve
#   scripts/on-host.sh --loop start
#
# `--backups` mounts the dumps instead, for reading one by hand.
#
# Each command gets a Job of its own, made from what the deploy left in the
# namespace, so it runs the deployed image and two commands cannot collide. The
# Job is deleted when the command ends. A machine that goes away leaves it to
# sleep out its time and go by itself. That time is four hours, and it bounds the
# command too: one still running then is cut off.
#
# Not an SSH tunnel to Postgres: code here could run ahead of the host's schema,
# and DATABASE_URL would switch between the working database and a local one —
# the keystroke-identical hazard db/guard.py exists for.
#
# The host is ITGRAPH_SSH, you@host, your own account, which has sudo. The host's
# login shell is assumed to be bash, which is what `printf %q` quotes for.
set -euo pipefail

NAMESPACE=${ITGRAPH_NAMESPACE:-itgraph}

usage() {
	echo "usage: $0 [--session | --backups] <itgraph arguments>   e.g. $0 yt channels" >&2
	echo "       $0 --loop stop|start" >&2
}

if [ "$#" -eq 0 ]; then
	usage
	exit 64
fi

if [ -z "${ITGRAPH_SSH:-}" ]; then
	echo "ITGRAPH_SSH is not set: set it to your own account on the" \
		"collection host, you@host" >&2
	exit 1
fi

KUBECTL="sudo -n k3s kubectl -n $(printf '%q' "$NAMESPACE")"

if [ "$1" = "--loop" ]; then
	# Scale, then wait for what was scaled to be true: a stopped loop is a pod
	# that is gone, a started one a rollout that finished. Without the wait,
	# the session-needing command right after `--loop stop` races the
	# collector's shutdown — and loses its lease.
	#
	# `wait --for=delete` by label fails when nothing matches, which is also
	# what a loop already stopped looks like; so a failed wait is asked once
	# more, plainly: is any collector pod still there?
	case "${2-}" in
	stop)
		exec ssh -T "$ITGRAPH_SSH" \
			"$KUBECTL scale deploy/itgraph-watch --replicas=0 \
			&& { $KUBECTL wait --for=delete pod \
			-l app=itgraph-watch --timeout=180s \
			|| [ -z \"\$($KUBECTL get pods \
			-l app=itgraph-watch -o name)\" ]; }"
		;;
	start)
		exec ssh -T "$ITGRAPH_SSH" \
			"$KUBECTL scale deploy/itgraph-watch --replicas=1 \
			&& $KUBECTL rollout status \
			deploy/itgraph-watch --timeout=180s"
		;;
	*)
		usage
		exit 64
		;;
	esac
fi

VARIANT=plain
case "$1" in
--session)
	VARIANT=session
	shift
	;;
--backups)
	VARIANT=backups
	shift
	;;
esac

if [ "$#" -eq 0 ]; then
	usage
	exit 64
fi

# Every argument quoted on its own, so one with spaces or quotes arrives as the
# one argument it was here.
COMMAND=""
for arg in "$@"; do
	COMMAND+=" $(printf '%q' "$arg")"
done

# One connection for all of it. Only `exec` reads standard input, which is the
# operator's; everything before it is told to leave it alone. The exit status of
# `exec` is the last one here, and a trap does not change it. The pod takes a
# moment to exist, and `wait` fails until then.
REMOTE=$(
	cat <<EOF
manifest=\$($KUBECTL get configmap itgraph-console -o 'jsonpath={.data.$VARIANT\.json}' </dev/null) || exit 1
job=\$(printf '%s' "\$manifest" | $KUBECTL create -f - -o name) || exit 1
trap '$KUBECTL delete "\$job" --ignore-not-found --wait=false </dev/null >/dev/null 2>&1 || :' EXIT HUP INT TERM
tries=0
until $KUBECTL wait --for=condition=Ready pod -l "batch.kubernetes.io/job-name=\${job#*/}" --timeout=180s </dev/null >/dev/null 2>&1; do
	tries=\$((tries + 1))
	if [ "\$tries" -ge 30 ]; then
		echo "the command's pod did not start: \$job" >&2
		exit 1
	fi
	sleep 2
done
$KUBECTL exec -i "\$job" -c itgraph -- itgraph$COMMAND
EOF
)

# -T: no terminal, so standard input reaches the command byte for byte. exec: ssh's
# exit status, which is the remote command's, is the script's.
exec ssh -T "$ITGRAPH_SSH" "$REMOTE"
