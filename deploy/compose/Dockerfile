# OpenJM Enterprise AI backend image (VS8). Python 3.11 for DB-GPT compatibility.
#
# Two stages, because the production container must run the SAME artifact the
# release contract qualifies:
#
#   builder  builds the application WHEEL from the exact backend source. Its build
#            backend (Hatchling), compiler and other build-only tooling never
#            reach the runtime image.
#   runtime  installs the committed production lock with --require-hashes, then
#            installs the prebuilt application wheel with --no-deps and
#            non-editable.
#
# The previous single-stage image installed the backend source path in editable
# mode, which meant an editable application install and a runtime dependency graph
# resolved from open-ended project metadata instead of backend/requirements.lock.
# A Compose deployment could therefore run a different dependency graph from the
# artifact under qualification, which is not defensible release evidence.

# --- build stage: produce the application wheel -----------------------------
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Build-only tooling stays in this stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ /build/backend/

# Wheel only, no dependency resolution: the runtime stage owns dependencies and
# installs them from the committed lock.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels /build/backend \
    && ls -1 /wheels

# --- runtime stage: locked dependencies + prebuilt wheel --------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TOKENIZERS_PARALLELISM=false \
    PYTHONPATH=

WORKDIR /opt/openjm

# Runtime packages only. No compiler: the committed production lock is
# binary-only, proven by the REL1 lock-drift gate that builds a binary-only
# wheelhouse from this same lock.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# The committed production lock is the runtime dependency authority. Hash
# enforcement means a tampered or unlisted distribution fails the build.
COPY backend/requirements.lock /opt/openjm/backend/requirements.lock

RUN python -m venv /opt/openjm/backend/.venv \
    && /opt/openjm/backend/.venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/openjm/backend/.venv/bin/pip install --no-cache-dir --require-hashes \
        -r /opt/openjm/backend/requirements.lock

# Application wheel from the build stage: non-editable, --no-deps, so the
# installed dependency graph stays exactly the locked one. The wheel is
# self-migrating (it carries migrations/ and alembic.ini), so startup migrations
# and /api/ready work without the source tree.
COPY --from=builder /wheels /opt/openjm/wheels
RUN /opt/openjm/backend/.venv/bin/pip install --no-cache-dir --no-deps /opt/openjm/wheels/*.whl \
    && rm -rf /opt/openjm/wheels \
    && /opt/openjm/backend/.venv/bin/python -c "import app, os; print('app from', os.path.dirname(app.__file__))"

COPY scripts/ /opt/openjm/scripts/
COPY deploy/compose/entrypoint.sh /opt/openjm/entrypoint.sh

# Run as an unprivileged user.
RUN useradd --system --home /opt/openjm --shell /usr/sbin/nologin openjm \
    && chmod 0755 /opt/openjm/entrypoint.sh \
    && mkdir -p /var/lib/openjm /etc/openjm /var/backups/openjm \
    && chown -R openjm:openjm /var/lib/openjm /etc/openjm /var/backups/openjm /opt/openjm
USER openjm

EXPOSE 8000
# Assemble the DSN from the db-password secret, preflight, then serve.
ENTRYPOINT ["/opt/openjm/entrypoint.sh"]
CMD ["/bin/sh", "-c", "/opt/openjm/backend/.venv/bin/python -m app.core.preflight && exec /opt/openjm/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
