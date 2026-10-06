# Live acceptance harnesses

These scripts are not collected by pytest (no `test_` prefix). They drive real
running products over real HTTP and exist so a live acceptance can be re-run and
re-checked rather than taken on trust.

## vs7_live_acceptance.py

The VS7 Workspace dual-product vertical slice: 27 ordered checks covering
product independence, Workspace integration, content update, authorization
revocation, restoration, deletion, disconnect and cross-tenant/cross-principal
isolation.

Run it with both products up:

    # Workspace (it starts and migrates its own PostgreSQL)
    cd /path/to/Workspace-Platform && node --import tsx scripts/dev.ts

    # OpenJM against a scratch database
    cd backend && OPENJM_DATABASE_URL="sqlite+aiosqlite:///<scratch>/ojm.db" \
      OPENJM_UPLOAD_DIR=<scratch>/uploads OPENJM_AUTH_MODE=dev \
      ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

    # then
    cd backend && OPENJM_DATABASE_URL=... OPENJM_UPLOAD_DIR=... \
      OJM_BASE=http://127.0.0.1:8000 WS_BASE=http://127.0.0.1:4000 \
      ./.venv/bin/python tests/live/vs7_live_acceptance.py

It writes raw per-check evidence to `vs7_live_evidence.json` next to itself.
No token, password or cookie value is ever printed; only sha256 fingerprints.

The end-to-end natural-language answer step is not exercised by this harness: it
requires a configured model provider. The evidence-gating property is instead
proven at the retrieval and authorization layer, which is where the security
decision actually lives.
