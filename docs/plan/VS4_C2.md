# VS4-C2 — Governed bounded exports

Tracker: [#25](https://github.com/sjevans1/OpenJM-Enterprise-AI/issues/25).
Branch: `milestone/vs4-c2-report-exports`. Depends on accepted C1.

## Phases and acceptance

1. **Authorized persisted export.** Add explicit CSV and HTML download endpoints
   for an owned saved snapshot or terminal successful ReportRun. Each request
   reauthorizes current documents, sources and table grants before reading result
   payload. Wrong owner is 404; revoked/incomplete result is denied. No generation,
   retrieval, SQL, external fetch or refresh on export. Avoid evidence in failures.
2. **CSV fidelity and injection safety.** Use actual persisted typed columns/rows.
   If a legacy snapshot has only a narrative/preview, report CSV unavailable;
   do not query for replacement data or invent rows. Multiple structured result
   sets require explicit selection by a server-validated evidence/result index;
   never silently combine unrelated schemas. Stay within existing stored row/byte
   limits, preserve header/row ordering, Unicode, commas, quotes, line breaks,
   empty/null values and as-of/provenance association. Neutralize spreadsheet
   formulas in untrusted text cells, including leading whitespace/control
   characters before `=`, `+`, `-`, `@`; retain actual typed numeric values.
   Tests cover these cases and a round-trip with a standard CSV reader.
3. **HTML/print fidelity.** Self-contained escaped HTML with title, definition/run
   identity when applicable, historical as-of time, answer, source references and
   citations. No active scripts, remote images/fonts/resources, untrusted markup,
   unsafe links or secret/connection/SQL metadata. Use safe attachment filenames,
   correct content types, no-store responses and a restrictive document policy.
   Print styling must preserve readable citations and page breaks.
4. **UI and verification.** Add export actions to the existing details view;
   disable unavailable formats and clear stale selection after permission failure.
   Tests prove no model/retrieval/SQL on export, foreign/revoked denial, bounded
   payloads, failed/incomplete run refusal, CSV formula defenses, HTML escaping,
   safe headers/filenames and literal fidelity to stored results. Confirm CSV and
   HTML/print outputs with synthetic data in browser/spreadsheet inspection.

Required: final-head fast and full hosted CI, security review of export handlers,
and browser/export artifact evidence tied to the tested code SHA. Existing model
acceptance is reused unless execution code changes; do not rerun the model just
to export the same stored result. Do not upload private reports to GitHub. A
downloaded file cannot be recalled after later revocation; document this limit.
Stop at awaiting_review.
