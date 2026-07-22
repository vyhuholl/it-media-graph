.PHONY: format lint typecheck test validate ci \
	backup backup-full backup-install backup-uninstall backup-status backup-log

RUN_CMD := uv run

lint:
	$(RUN_CMD) ruff check --fix .
	$(RUN_CMD) ruff format .

lint-check:
	$(RUN_CMD) ruff check .
	$(RUN_CMD) ruff format --check .

typecheck:
	$(RUN_CMD) mypy src/

test:
	$(RUN_CMD) pytest -q --cov=src

validate: lint typecheck test
ci: lint-check typecheck test

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
