# VS4-A ACCEPTANCE REPORT
Saved Report Snapshots — OpenJM Enterprise AI
Acceptance branch: feature/vs4-a-saved-report-snapshots (integration history recorded in PR #14)
Verified implementation checkpoint: 6f445d0e47362495108dc2a6f2108f6b30057abb (GitHub-confirmed PR #14 head when reviewed; later documentation commits do not alter the tested application code)
Base checkpoint: b6fe45af79781172487de99205429fb63cc5cd82

## Summary of corrections
After review, defects were found and fixed (two verified commits, committed incrementally on the existing branch):

1. Backend — structured table-level authorization gap (HIGH):
   - reports.py now requires bounded metadata.tables on every structured_query Evidence item and checks each table against DataSource.authorized_objects_json on create, list, and detail. Fail-closed: missing/malformed table provenance is rejected (422); unauthorized tables make the snapshot unavailable (409) without returning stale rows/SQL.
   - Equivalent-source revocation is now enforced after save (post-save revocation test).
   - SaveReportRequest hardened with extra="forbid" (rejected client-supplied answer/evidence/sql/model/tool fields).
   - SQLite foreign keys enabled per connection via engine.connect listener (PRAGMA foreign_keys=ON); exported for test reuse. Previously declared cascades were inert under SQLite.
2. Backend — cascade test loosened (MEDIUM): test_deleted_owning_conversation_denies_access now asserts 404 and direct absence of the SavedReport row rather than accepting 409-and-orphan fallback.
3. Frontend — stale-content + race fixes (HIGH/MEDIUM):
   - loadReports reconciles activeReport against the refreshed summaries; a pending in-flight open for a now-unavailable report is discarded (sequence + pending-id guards).
   - deleteReport uses ID-scoped state updates so deleting one report cannot cancel an unrelated in-flight open.
   - Save upserts the returned report before reload; delete removes the item locally before reload. Functional state updaters used in the changed paths.

All edits reviewed by two independent reviewer subagents (backend security: passed; frontend: passed). Static scan on added backend lines: 0 secrets, 0 shell-injection, 0 eval/exec, 0 pickle, 0 SQL string-formatting. Protected untracked files scripts/serve_frontend.py and test-documents/ untouched.

## Step 1 — Code review findings
- SQLAlchemy model / lifecycle: SavedReport model correct; foreign keys + unique constraint declared. Defect found and fixed (SQLite FKs were inert). Now enforced.
- SQLite bootstrap: additive Base.metadata.create_all() only adds saved_reports. Confirmed no rewrite of legacy tables (see Gate C).
- Pydantic bounds: message_id/title/pagination/answer-bytes/evidence-bytes count all bounded. Added table-provenance bounds. extra="forbid" added.
- HTTP 204/409: 204 returns no body (frontend verified no .json() parse); 409 raises with detail; frontend masks title to "Unavailable saved report".
- Source authorization on every read: documents, equivalent_sources, grounded_parameter policy doc, and structured authorized_objects_json now all re-checked.
- Provenance/citations preserved: report stores server-side answer_text + evidence_json only; no client-supplied content honored.
- Duplicate-save: idempotent via unique owner/message constraint + IntegrityError refetch. Test asserts same ID and original title.
- Deletion boundaries: report-only; message/document/source untouched. Test-verified.
- No stale/revoked content leakage: fixed table-level leak; document/source revocation deny (409) without content. Live-checked no "325"/"threshold" leaks.
- No unauthorized model/SQL/tool execution: reports router performs only metadata queries. Live trace-count check confirmed zero knowledge.search/structured.query during save/reopen/list.

## Step 2 — Targeted backend tests
Command: cd backend && python -m pytest -q tests/test_reports.py
Result: 23 passed, 0 warnings.
Expanded coverage (7 new tests + 2 hardened):
- Cross-user message and report IDs (test_another_users_report_id_is_not_readable_or_deletable)
- Non-assistant messages (test_non_assistant_message_cannot_be_saved — pre-existing, retained)
- Missing/malformed evidence (test_malformed_evidence_rejected, test_no_evidence_cannot_be_saved)
- Disabled/deleted structured sources (test_structured_report_requires_enabled_source_on_every_read)
- Deleted/unindexed documents (test_deleted_document_blocks_stale_answer)
- Equivalent-source references, save-then-revoke (test_equivalent_source_revocation_denies_access, test_equivalent_source_revoked_after_save_denies_detail)
- Nested grounded-policy provenance revocation (test_nested_policy_provenance_revocation_denies_report — updated with metadata.tables)
- Duplicate saves (test_save_reopen_snapshot_and_duplicate_is_idempotent)
- Idempotent deletes (test_delete_snapshot_leaves_source_and_message)
- Immutable historical snapshot (test_snapshot_is_immutable_when_source_message_changes)
- Oversized content (test_oversized_answer_rejected) and invalid titles (test_blank_report_title_rejected)
- Pagination boundaries (test_pagination_and_limits)
- Table-level revocation (test_structured_table_revocation_denies_snapshot_content)
- Missing/bounded structured provenance (test_structured_evidence_without_bounded_tables_fails_closed)
- Client-supplied fields rejected (test_save_request_rejects_client_supplied_snapshot_or_execution_fields)
Security-critical: revoked structured table content verified not returned through report detail API (409, no rows/SQL/table names).

## Step 3 — Frontend validation
Commands and results:
- npm run test -- --run -> 2 files, 4 tests passed.
- npm run build -> tsc --noEmit + Vite (1578 modules) built, 0 errors.
- npm audit -> 0 vulnerabilities (prod 7, dev 181, optional 53, peer 10).
Full journey verified by tests + live run: Chat -> Evidence-backed answer -> Save as Report -> Reports list -> Open -> citations render -> Delete. Stale reports cannot display cached content after 409: openReport clears activeReport on error; loadReports clears activeReport when its refresh is unavailable. HTTP 204 handled without JSON parsing; 409 masked to "Unavailable saved report". UI clearly labels "Saved evidence snapshot", "As of <timestamp>", and "Not live: No queries executed when viewing".

## Step 4 — Database upgrade acceptance
Method: read-only backup of the real pre-VS4 SQLite DB into scratch, second startup in an isolated engine pointed at the copy (production DB never opened for write by the test).
Source DB data/openjm.db: 134 conversations, 377 messages, 76 execution_traces, 0 documents, 0 data_sources; saved_reports absent; PRAGMA integrity_check = ok; sha256 3c24322dac724511f5c39ad3b8feb3c076fc058e0a3ebdcbf91d874a177cafea.
Results: saved_reports table created with all 12 columns, unique (user_id, message_id), and cascading FKs to conversations(id) and messages(id). Legacy rows unchanged across two startup cycles (identical counts and logical hashes; physical SHA unchanged). Real DB sha256 unchanged after the test.

## Step 5 — Real local acceptance
Isolated runtime (separate app DB, vector path, dev_user_id "vs4a-acceptance") over local Gemma 4 12B Q4_0, DB-GPT/Chroma, governed SQLite fixture revenue DB (6 rows incl. Blue Mountain Cafe 325).
1. Knowledge: policy doc "FY2025 annual revenue exceeds USD 300" uploaded, indexed, grounded answer saved/reopened (source_count=1, is_live=false).
2. Structured: answer "325" saved/reopened (source_count=1).
3. Hybrid: doc+data evidence, grounded_parameter value=300 source_id=policy_doc, [DOC]+[DATA] citations, Blue Mountain/Kingston/Montego/Portland in answer, Harbour Shop excluded (source_count=2), saved/reopened.
4. Revoke source (disable): structured & hybrid -> 409, no "325" leak; knowledge -> 200. Listing masks unavailable titles/source_count.
5. Delete policy doc: knowledge & hybrid -> 409, no "threshold" leak.
6. No-execution: after save/reopen/listing, trace counts for knowledge.search and structured.query unchanged (0 new traces); no Gemma/Chroma/SQL invoked by report endpoints.
7. Cleanup: 3 reports -> 204 idempotent (re-delete 204) -> 404; source catalog empty; document catalog empty.
8. Real data/openjm.db untouched: saved_reports absent, 134/377/76 counts, integrity ok, sha256 unchanged.

## Step 6 — Full regression
Backend: cd backend && python -m pytest -q -> 269 passed, 1 warning.
Warning: tests/test_upload_source_name_integration.py — dbgpt_ext PydanticDeprecatedSince20 (.dict()) — third-party, pre-existing, unrelated to VS4-A.
Frontend (after changes): 4 tests passed; production build passed; audit 0 vulnerabilities.

## Security and architecture boundaries
- No scheduled reports, no autonomous agents, no automatic source inference, no SQL reruns, no uncontrolled exports, no fabricated evidence, no production multi-tenant claims, no source deletion on report deletion.
- No shared databases, runtime dependencies, ORM models, repositories, or deployment requirements between OpenJM Workspace and Enterprise AI.
- Protected untracked files scripts/serve_frontend.py and test-documents/ untouched (git status shows only these two as pre-existing untracked).

## Outstanding limitations
1. Single dev identity (settings.dev_user_id="local-admin" / "vs4a-acceptance"); no real auth or multi-tenant isolation (VS5).
2. SQLite-only bootstrap; versioned Alembic migrations and PostgreSQL support remain issue #7.
3. Document lifecycle concurrency issue #6 remains separate (no cross-process revocation guarantee claim).
4. Reports are historical snapshots only; no rerun/export/automation in VS4-A (VS4-B is separate).
5. Gemma worker lacks metrics endpoint; no-execution proven via trace-count delta and dependency-denial (unavailable model/vector path) rather than log absence.
6. Knowledge has no disable/deindex API; deletion is the only supported doc revocation in this slice.

## Merge-readiness conclusion
All five gates (A-E) executed with actual outputs and recorded above. Backend 269 passed; frontend 4 tests + build + audit clean; DB upgrade verified on a temporary copy with real DB unmodified; live local acceptance fully passed. Two verified correction commits added to the existing branch. Draft PR #14 updated with evidence.

Acceptance status: Gates A–E reported passing by Hermes on the implementation checkpoint above; GitHub Actions did not run for that commit. The maintainer explicitly authorized correcting this record and merging PR #14 on October 2, 2026. For the authoritative merge status, commit and timestamp, see https://github.com/sjevans1/OpenJM-Enterprise-AI/pull/14. This report does not claim an independent rerun of local tests after this documentation-only correction.
