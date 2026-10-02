# VS4-C1 — Manual report controls and execution history

Tracker: [#25](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/25).
Branch: `milestone/vs4-c1-report-run-ui`. Depends on accepted B2C2.

## Phases and acceptance

1. **Definition and intentional action.** Extend existing Reports view and api.ts.
   Users can create/view the server-derived immutable definition, see mode/pinned
   sources and choose an explicit Run action with confirmation. Distinguish the
   original saved snapshot, fresh pinned run and exploratory B1 Chat handoff.
   Loading, opening, refreshing or selecting never submits a run. Respect server
   `runnable` state and explain disabled execution without implementation jargon.
2. **Reliable submission.** Generate a UUID once per intentional run. Keep it
   across double-click prevention and uncertain network retries for that intent;
   reconcile history/server response rather than inventing a new key. A new
   key means a newly confirmed run. A terminal failure offers an explicit retry,
   never an automatic run. Client cancellation must not claim server cancellation.
3. **Immutable history and failure UX.** Show bounded paginated history, status,
   started/completed times, definition version, as-of time, answer and citations.
   Preserve original snapshot; show an unavailable state after revocation or
   deletion, clear cached answer/evidence, and ignore late responses after user
   navigation/deletion. Handle running/interrupted/failed outcomes honestly.
   Existing save/list/open/delete and B1 preflight must keep working.
4. **Verification.** Add frontend behavior tests for no execution on view, disabled
   action, explicit confirmation, double click, uncertain-response same-key retry,
   pagination, terminal failure and stale-response races. Backend regression must
   prove UI request shape cannot bypass scope or ownership. Verify keyboard use,
   readable errors, loading controls and citations in the real browser.

Scope: Reports UI/API client and focused tests, with only necessary compatible
backend response adjustments. No redesign of Chat, themes, navigation or other
products. No exports yet; C2 supplies them separately.

Required evidence: final-head fast + full hosted CI; controlled browser flow
against accepted B2C2 with real local model success, a failure, revoked access and
restart/history persistence. Reuse the isolated acceptance fixtures and cleanup
from B2C2. Screenshots must contain synthetic data only. Health alone is not
acceptance. Stop at awaiting_review, then wait for authorized merge + green main.
