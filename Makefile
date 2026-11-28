.PHONY: lint typecheck test test-e2e validate ansible-lint

# --group embed on every target: the suite typechecks and tests the
# embedding code, whose dependencies (numpy, onnxruntime, tokenizers) are
# deliberately not main ones — the image installs them by group too.
RUN_CMD := uv run --group embed

lint:
	$(RUN_CMD) ruff check --fix .
	$(RUN_CMD) ruff format .

typecheck:
	$(RUN_CMD) mypy src/

# The suite waits on Postgres round-trips far more than it computes, so
# workers pay for themselves. Eight and not `auto`, which is one per
# core — past eight they queue on the one Postgres container instead of
# getting faster (12 -> 57 s, 16 -> 71 s).
#
# `--dist worksteal`: a worker that runs dry takes half of the longest
# queue. The default leaves each worker with the block it was dealt, and
# one file of slow tests then keeps one or two of the eight busy to the
# end while the rest stand idle — with coverage, back to back: 9:01
# against 3:34.
#
# `make test WORKERS=0` runs in one process, which is how a failure is
# read: xdist interleaves the output of eight.
WORKERS ?= 8

test:
	$(RUN_CMD) pytest -q -n $(WORKERS) --dist worksteal --cov=src

# The role on a throwaway k3d cluster, a quarter of an hour of it. Outside
# `validate`: it needs Docker and k3d, and reaches no one's cluster but its own.
test-e2e:
	deploy/ansible/e2e.sh

# -c: the config lives in deploy/ but this runs from the repo root, and
# ansible-lint only looks in the cwd — without it the file is linted as
# input instead of read as config, and `profile: production` is not
# enforced at all.
ansible-lint:
	$(RUN_CMD) ansible-lint -c deploy/.ansible-lint

validate: lint typecheck test ansible-lint
