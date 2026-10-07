# OpenJM Enterprise AI backend image (VS8). Python 3.11 for DB-GPT compatibility.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TOKENIZERS_PARALLELISM=false \
    PYTHONPATH=

WORKDIR /opt/openjm

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ /opt/openjm/backend/
COPY scripts/ /opt/openjm/scripts/
COPY deploy/compose/entrypoint.sh /opt/openjm/entrypoint.sh

RUN python -m venv /opt/openjm/backend/.venv \
    && /opt/openjm/backend/.venv/bin/pip install --no-cache-dir --prefer-binary -e "/opt/openjm/backend"

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
