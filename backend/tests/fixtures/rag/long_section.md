# Long Single Section Report - Boundary Fact Investigation

This fixture is intentionally a SINGLE top-level section that exceeds 2,000
characters. Its purpose (Phase D) is to test whether a unique fact placed near
the END of a long section survives into the customer-facing Evidence passage,
given the 2,000-character passage cap applied in the retrieval path.

The Markdown header strategy (CHUNK_BY_MARKDOWN_HEADER) emits one chunk per
section, and internally re-splits an oversized section with a recursive
CharacterTextSplitter at chunk_size=4000 / chunk_overlap=200. So a single
section longer than 4,000 characters is split into multiple chunks of up to
4,000 characters each. The recursive splitter preserves source order, so a
fact near the end of the section lands in the LAST chunk. That last chunk is
then re-ranked and returned with a relevance score. Because retrieval is
semantic (embedding-similarity), the last chunk does not necessarily score
high enough to beat the score threshold (0.25 by default) or to fall within
the top_k results, even when the fact itself is the exact answer.

This is the passage-limit / context-window loss failure mode Phase D must
document, and it must NOT be papered over by retries or by lowering the
retrieval threshold ad hoc. A bounded, deterministic correction is proposed
in docs/RAG_METADATA_EVALUATION.md (Phase E boundary work). The fact
identifier below is the probe target:

BOUNDARY-FACT-PHASE-D-9931

Background: QALO's Q4 2024 forecast revision incorporated three secondary
indicators derived from partner-reported telemetry: (1) the Kingston metro
kefir sell-through index, (2) the Montego Bay bulk-distribution fill-rate, and
(3) the May-to-September promotional cadence efficiency. Each indicator was
normalized to its trailing twelve-month baseline before aggregation. The
composite leading indicator was then smoothed with a 7-day moving average to
reduce daily volatility from single-store stockouts. The resulting forecast
band spans a projected revenue envelope of JMD 4,050,000 through JMD 6,980,000
for the combined Q4 beverage-operations and analytics-services portfolio, with
a point estimate of JMD 5,515,000. The margin assumptions feeding this band
hold only if the regional distributor contract rate stays fixed through the
end of week 5 of fiscal Q4; any renegotiation before the freeze date would, by
the model's sensitivity analysis, shift the point estimate by approximately
negative 2.3% per hundred-basis-point change in the landed-cost pass-through
markup. The supply-chain contingency clause in section 7.4 of the master
distribution agreement is the only contractual lever available to offset such
a shift, and it activates automatically once cumulative variance exceeds the
agreed 1.5% threshold. All figures above are expressed in current-period
Jamaican dollars and exclude the one-time FX revaluation credit booked in
week 2 of Q4. The unique probe identifier for this section is repeated here
near the end so that, if any of this content survives truncation into the
Evidence passage, the test can assert retrieval without false positives:

BOUNDARY-FACT-PHASE-D-9931
