#!/bin/sh
# Refuses to let an unencrypted vault reach a commit.
#
# secrets.vault.yml is committed, and the repository is public: a
# plaintext copy in one commit publishes the bot token, the api hash and
# the proxy endpoint for good, because history is not taken back.
# `ansible-vault encrypt` is a separate step from editing, so forgetting
# it is easy — and the resulting file looks entirely ordinary in a diff.
#
# gitleaks covers part of it: on a plaintext vault it flags the bot
# token and the passwords, and passes the api hash and the proxy host.
set -eu

status=0

for file in "$@"; do
    [ -f "$file" ] || continue
    if [ "$(head -c 14 "$file")" != '$ANSIBLE_VAULT' ]; then
        echo "$file is not encrypted." >&2
        echo "  Encrypt it in place:  ansible-vault encrypt $file" >&2
        status=1
    fi
done

exit "$status"
