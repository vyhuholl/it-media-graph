.PHONY: lint typecheck test validate ansible-lint \
	backup backup-full backup-install backup-uninstall backup-status backup-log

RUN_CMD := uv run

lint:
	$(RUN_CMD) ruff check --fix .
	$(RUN_CMD) ruff format .

# --all-groups: `bot` and `data` are not default groups, so a bare
# `uv run` leaves aiogram, pandas and networkx uninstalled and mypy
# reports the whole bot package as unresolved imports.
typecheck:
	$(RUN_CMD) --all-groups mypy src/

test:
	$(RUN_CMD) pytest -q --cov=src

# -c: the config lives in deploy/ but this runs from the repo root, and
# ansible-lint only looks in the cwd — without it the file is linted as
# input instead of read as config, and `profile: production` is not
# enforced at all.
ansible-lint:
	$(RUN_CMD) ansible-lint -c deploy/.ansible-lint

validate: lint typecheck test ansible-lint

backup:
	$(RUN_CMD) itgraph backup

backup-full:
	$(RUN_CMD) itgraph backup --full

backup-install:
	./scripts/backup-agent.sh install

backup-uninstall:
	./scripts/backup-agent.sh uninstall

backup-status:
	./scripts/backup-agent.sh status

backup-log:
	./scripts/backup-agent.sh log
