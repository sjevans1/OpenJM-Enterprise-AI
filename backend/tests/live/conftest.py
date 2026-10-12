"""Collection gate for the live harnesses.

Files here are runnable acceptance harnesses, not CI tests. `vs7_live_acceptance.py` is not
collected because it has no `test_` prefix; this module's `test_`-prefixed file WOULD be collected
by an ordinary `pytest tests/`, so it is ignored unless CA2_LIVE=1 is set on purpose.

    CA2_LIVE=1 ./.venv/bin/python -m pytest tests/live/test_ca2_live_retrieval.py -q -s
"""

import os

collect_ignore: list[str] = []

if os.environ.get("CA2_LIVE") != "1":
    # Requires the real embedding model, a persistent local Chroma and synthetic ingestion, so it
    # must never run as part of an ordinary suite.
    collect_ignore.append("test_ca2_live_retrieval.py")
