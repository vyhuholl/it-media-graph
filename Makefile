.PHONY: format lint typecheck test validate ci

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