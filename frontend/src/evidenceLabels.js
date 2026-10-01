/**
 * Pure, independently testable citation labels for ordered governed evidence.
 * Document and data numbering are separate, matching the HYBRID prompt.
 * This module has no browser dependencies and is reused by the React UI.
 * @param {ReadonlyArray<{source_type: string}>} evidence
 * @returns {string[]}
 */
export function evidenceCitationLabels(evidence) {
  let docs = 0;
  let data = 0;
  return evidence.map((item, index) => {
    if (item.source_type === 'document') return `[DOC ${++docs}]`;
    if (item.source_type === 'structured_query') return `[DATA ${++data}]`;
    return `[EVIDENCE ${index + 1}]`;
  });
}
