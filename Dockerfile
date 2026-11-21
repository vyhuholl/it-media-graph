# The image the collection host runs: built by CI from every push to
# master, published to ghcr.io and deployed by digest. Nothing may be
# installed on the host itself, so everything the workloads need — the
# code, the PostgreSQL clients, `age`, `curl`, the time zones — is here.

# One base image for both stages, pinned by digest: the virtual
# environment's interpreter is an absolute symlink, so the base Python
# must exist at the same path — /usr/local/bin/python3.14 — in the image
# the environment is copied into. Its version is the pin in
# .python-version, and uv (from its own image, pinned too) is told not
# to download another, so a base that has drifted from the pin fails
# the build instead of shipping it.
FROM python:3.14.3-slim-trixie@sha256:5e59aae31ff0e87511226be8e2b94d78c58f05216efda3b07dbbed938ec8583b AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock README.md .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --group embed
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --group embed

# The e5-small weights, fetched once here and pinned by revision as
# everything else in this file is pinned by digest: `model_key` names
# them, and an unpinned fetch would let upstream change the vectors
# under that name. The int8 O4 export (235 MB, within 0.0002 cosine of
# the fp32 one) and the tokenizer are all the wrapper loads — the
# runtime never downloads, which the embed job's database-only egress
# turns from a promise into a hard guarantee.
RUN /app/.venv/bin/python -c "\
    from huggingface_hub import snapshot_download; \
    snapshot_download(\
        repo_id='intfloat/multilingual-e5-small', \
        revision='614241f622f53c4eeff9890bdc4f31cfecc418b3', \
        allow_patterns=['onnx/model_O4.onnx', 'onnx/tokenizer.json'], \
        local_dir='/tmp/e5-small')" \
    && mkdir -p /app/models/multilingual-e5-small \
    && mv /tmp/e5-small/onnx/model_O4.onnx \
        /tmp/e5-small/onnx/tokenizer.json \
        /app/models/multilingual-e5-small/ \
    && rm -rf /tmp/e5-small

FROM python:3.14.3-slim-trixie@sha256:5e59aae31ff0e87511226be8e2b94d78c58f05216efda3b07dbbed938ec8583b

# The PostgreSQL 16 client from PGDG, not Debian's own 17: the server is
# 16, a newer pg_dump writes archives the matching pg_restore is not
# guaranteed to read, and an older one refuses to dump at all. Plus
# `age` for the off-site copies, `curl` for the deploy's exit check,
# and tzdata so TZ names a zone that exists.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    install -dm755 /usr/share/postgresql-common/pgdg; \
    curl -fsSLo /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        postgresql-client-16 age tzdata; \
    rm -rf /var/lib/apt/lists/*

# A fixed UID with no shell login; nothing in this image runs as root.
RUN groupadd --gid 3000 itgraph \
    && useradd --uid 3000 --gid itgraph --no-create-home \
        --shell /usr/sbin/nologin itgraph

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow
WORKDIR /app
# The migrations, which alembic.ini finds through %(here)s/src/…; the
# code itself runs from the environment copied in below.
COPY alembic.ini ./
COPY src ./src
COPY --from=builder /app/.venv ./.venv
# The model the embed commands load, at the path settings names by
# default: /app/models/multilingual-e5-small.
COPY --from=builder /app/models ./models

# The build checks itself, because the deploy has nowhere to install
# what is missing: the entry point answers, the client is a 16, the
# clock knows Moscow, and the two binaries exist.
RUN set -eux; \
    itgraph --help > /dev/null; \
    pg_dump --version | grep -q '^pg_dump (PostgreSQL) 16\.'; \
    [ "$(date +%Z)" = MSK ]; \
    [ -s /app/models/multilingual-e5-small/model_O4.onnx ]; \
    [ -s /app/models/multilingual-e5-small/tokenizer.json ]; \
    command -v age; \
    command -v curl

USER itgraph
# What the manifests' `args` are handed to: `args: ["watch"]` runs
# `itgraph watch`. Without it the base image's `python3` is the command,
# `args` replace it, and the kubelet looks for a binary called `watch`.
# The console's `command: sleep` and every `kubectl exec` name their own
# executable and are unaffected.
ENTRYPOINT ["itgraph"]
