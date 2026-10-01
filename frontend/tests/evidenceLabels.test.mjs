import assert from 'node:assert/strict'
import { test } from 'node:test'
import { evidenceCitationLabels } from '../src/evidenceLabels.js'

test('hybrid document and data citations number independently', () => {
  const sources = [
    { source_type: 'document' },
    { source_type: 'document' },
    { source_type: 'structured_query' },
    { source_type: 'structured_query' },
    { source_type: 'document' },
  ]
  assert.deepEqual(evidenceCitationLabels(sources), [
    '[DOC 1]', '[DOC 2]', '[DATA 1]', '[DATA 2]', '[DOC 3]',
  ])
})

test('unknown evidence types remain visible and cannot steal a known prefix', () => {
  const sources = [
    { source_type: 'document' },
    { source_type: 'document_catalog' },
    { source_type: 'structured_query' },
    { source_type: 'document' },
  ]
  assert.deepEqual(evidenceCitationLabels(sources), [
    '[DOC 1]', '[EVIDENCE 2]', '[DATA 1]', '[DOC 2]',
  ])
})

test('empty evidence has no labels', () => {
  assert.deepEqual(evidenceCitationLabels([]), [])
})
