#!/bin/sh
# Install, remove or inspect the launchd agent that takes backups.
#
# The agent runs `itgraph backup` every few hours rather than once at a
# fixed time: on a laptop the fixed time is whenever the lid happens to
# be shut. What is actually dumped is decided by the interval recorded
# against the files on disk, so extra runs cost a directory listing and
# nothing else.
set -eu

LABEL=dev.itgraph.backup
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/itgraph-backup.log"
REPO=$(cd "$(dirname "$0")/.." && pwd)
TARGET="gui/$(id -u)"

# Four hours: frequent enough that a laptop awake for part of the day
# still gets its daily dump, rare enough to stay invisible.
INTERVAL=14400

usage() {
	echo "usage: $0 {install|uninstall|status|log}" >&2
	exit 64
}

find_uv() {
	command -v uv || {
		echo "uv is not on PATH; install it first" >&2
		exit 1
	}
}

install_agent() {
	uv_path=$(find_uv)
	mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"

	# launchd starts jobs with a bare environment, so every path here is
	# absolute and the working directory is set explicitly — the CLI
	# reads .env relative to it.
	cat >"$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$uv_path</string>
		<string>run</string>
		<string>itgraph</string>
		<string>backup</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$REPO</string>
	<key>StartInterval</key>
	<integer>$INTERVAL</integer>
	<key>RunAtLoad</key>
	<true/>
	<key>StandardOutPath</key>
	<string>$LOG</string>
	<key>StandardErrorPath</key>
	<string>$LOG</string>
	<key>ProcessType</key>
	<string>Background</string>
</dict>
</plist>
PLIST_END

	launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
	launchctl bootstrap "$TARGET" "$PLIST"
	echo "installed $LABEL"
	echo "  plist : $PLIST"
	echo "  log   : $LOG"
	echo "  every : $((INTERVAL / 3600))h, and once now"
}

uninstall_agent() {
	launchctl bootout "$TARGET/$LABEL" 2>/dev/null || true
	rm -f "$PLIST"
	echo "removed $LABEL (backups already taken are left alone)"
}

case "${1:-}" in
install) install_agent ;;
uninstall) uninstall_agent ;;
status)
	launchctl print "$TARGET/$LABEL" 2>/dev/null |
		grep -E 'state|last exit|program|run interval' ||
		echo "$LABEL is not loaded"
	;;
log) tail -n 40 "$LOG" 2>/dev/null || echo "no log at $LOG yet" ;;
*) usage ;;
esac
