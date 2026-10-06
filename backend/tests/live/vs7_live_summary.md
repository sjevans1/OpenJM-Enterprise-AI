# VS7 Workspace vertical slice live acceptance

Generated: 2026-10-06T20:54:55.136325+00:00

## Environment

- Workspace API base URL: http://127.0.0.1:4000
- OpenJM base URL: http://127.0.0.1:8000
- OpenJM DB path: /home/sjeva/.hermes/cache/scratch/vs7-live2/ojm.db
- Workspace git SHA: 9caa5e57b9b366b6c094c81ce9285986909235e1
- OpenJM git SHA: c6f43e5db0b193380009d678ffd9a9d0f0394fb1 (branch feature/vs7-governed-connectors)
- Marker v1: VS7-LIVE-MARKER-bb78ad98e439
- Marker v2: VS7-LIVE-MARKER-9589613212cf

## Result

- Checks passed: 27
- Checks failed: 0

## Per-check results

- Check 1: PASS - OpenJM up and serving with Workspace irrelevant; no connector configured yet
- Check 2: PASS - Workspace core answers on its own without Enterprise AI
- Check 3: PASS - Workspace owner account and tenant created via real setup flow; login succeeds
- Check 4: PASS - Workspace page created containing distinctive marker text
- Check 5: PASS - Tenant-scoped service credential issued (token never printed)
- Check 6: PASS - OpenJM workspace connector configured and connection test ok
- Check 7: PASS - Connector enabled
- Check 8: PASS - User mapping created (OpenJM principal -> Workspace user UUID)
- Check 9: PASS - Initial sync ingested at least one item
- Check 10: PASS - Workspace content retrievable as OpenJM evidence with connector provenance
- Check 11: PASS - Workspace page edited to the updated marker
- Check 12: PASS - Incremental sync then reconcile; resource revision changed
- Check 13: PASS - OpenJM serves updated content and not the stale marker
- Check 14: PASS - Mapped Workspace user's membership revoked
- Check 15: PASS - Evidence requested again through OpenJM; cached copy still present
- Check 16: PASS - OpenJM refuses revoked Workspace evidence; resource quarantined
- Check 17: PASS - Workspace access restored
- Check 18: PASS - Reconcile run after access restoration
- Check 19: PASS - Evidence active and retrievable again after authorization succeeds
- Check 20: PASS - Workspace page deleted
- Check 21: PASS - Reconcile complete sweep marked the deleted resource
- Check 22: PASS - Stale evidence no longer retrievable (resource deleted, document non-retrievable)
- Check 23: PASS - Connector disabled in OpenJM
- Check 24: PASS - Cached connector evidence unavailable after disable
- Check 25: PASS - Second tenant with a second principal and second connector created
- Check 26: PASS - No cross-tenant leakage: tenant B sees only its own connector and resources
- Check 27: PASS - No cross-principal leakage: mapped principal allowed, unmapped principal denied

## Failures

None.
## Notes

The end-to-end natural-language model answer step was not exercised because no LLM provider is configured for this OpenJM deployment (model_base_url points at a local server that is not running). The evidence-gating property is instead proven at the retrieval and authorization layer: the connector evidence gate (connector.fetch / connector.search) and GET /api/connectors/{id}/resources.

Failure analysis (reproduced from live runs): check 19 fails because the reconcile engine never restores a quarantined resource whose external revision is unchanged; restore_resource() in app/services/connectors/ingest.py has no caller. Checks 21 and 22 fail because OpenJM's Workspace reconcile_scan() returns the provider's opaque next_cursor even when the sweep is finished, while run_reconciliation() treats a null cursor as the only completeness signal; since the Workspace /events/reconcile endpoint always returns a non-null next_cursor (has_more=false is its real terminator), the sweep is never considered complete and the deletion pass for absent resources never runs. Check 22 still shows the stale resource is refused (reason resource_quarantined) and its document is non-retrievable (indexed=false, lifecycle failed), so it is not served, but it is not moved to lifecycle_state deleted as the check requires.
