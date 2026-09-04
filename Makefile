.PHONY: lint typecheck test validate ansible-lint \
	backup backup-full backup-install backup-uninstall backup-status backup-log

RUN_CMD := uv run

lint:
	$(RUN_CMD) ruff check --fix .
	$(RUN_CMD) ruff format .

typecheck:
	$(RUN_CMD) mypy src/

# The suite waits on Postgres round-trips far more than it computes, so
# workers pay for themselves: 322 s serially, 49 s at eight. Eight and
# not `auto`, which is one per core — past eight they queue on the one
# Postgres container instead of getting faster (12 -> 57 s, 16 -> 71 s).
# `make test WORKERS=0` runs in one process, which is how a failure is
# read: xdist interleaves the output of eight.
WORKERS ?= 8

test:
	$(RUN_CMD) pytest -q -n $(WORKERS) --cov=src

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
