#!/usr/bin/env bash
# Run an `itgraph` command on the collection host, from this machine.
#
# The working database lives on the host, next to the bot and the
# collector; this machine holds only old dumps. Review is routine work at
# volume and happens here, in a local editor — so the command runs there
# and its standard input and output come back here:
#
#   scripts/on-host.sh yt review-export > ~/itgraph-review/batch.review.tsv
#   scripts/on-host.sh yt review-apply - < ~/itgraph-review/batch.review.tsv
#   scripts/on-host.sh yt add --from-file - --failures - < seeds.txt > failed.txt
#
# Not an SSH tunnel to Postgres: code here could run ahead of the host's
# schema, and DATABASE_URL would switch between the working database and
# a local one — the keystroke-identical hazard db/guard.py exists for.
#
# The host is the first one under [collector] in deploy/inventory.ini, the
# file ansible already uses. The paths are the ones the playbook installs
# (`repo_dir` and `uv` in deploy/playbook.yml). The host's login shell is
# assumed to be bash, which is what `printf %q` quotes for.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
INVENTORY=${ITGRAPH_INVENTORY:-$REPO/deploy/inventory.ini}

if [ "$#" -eq 0 ]; then
	echo "usage: $0 <itgraph arguments>   e.g. $0 yt channels" >&2
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

# Every argument quoted on its own, so one with spaces or quotes arrives
# as the one argument it was here.
remote="cd ~/itgraph && ~/.local/bin/uv run itgraph"
for arg in "$@"; do
	remote+=" $(printf '%q' "$arg")"
done

# -T: no terminal, so standard input reaches the command byte for byte.
# exec: ssh's exit status, which is the remote command's, is the script's.
exec ssh -T "$user@$host" "$remote"
