"""Test for independent EvidencePanel citation labeling."""

import React from 'react';
import { render, screen } from '@testing-library/react';
import EvidencePanel from './EvidencePanel';

// Mock Evidence type
interface MockEvidence {
  source_type: 'document' | 'structured_query';
  title: string;
  passage: string;
  source_id: string;
}

test('EvidencePanel shows independent [DOC N] and [DATA N] numbering', () => {
  const mockEvidence: MockEvidence[] = [
    {
      source_type: 'document',
      title: 'Policy Document',
      passage: 'The policy states that threshold is 300.',
      source_id: 'doc-1'
    },
    {
      source_type: 'document',
      title: 'Procedure Guide',
      passage: 'Follow the procedure for review.',
      source_id: 'doc-2'
    },
    {
      source_type: 'structured_query',
      title: 'Sales Database',
      passage: '{"columns":["customer"],"rows":[["Acme Corp"],["Beta LLC"]],"row_count":2}',
      source_id: 'source-1'
    }
  ];

  render(<EvidencePanel evidence={mockEvidence} />);

  // Check that we have the right number of evidence cards
  const evidenceCards = screen.getAllByRole('group');
  expect(evidenceCards).toHaveLength(3);

  // Check first document evidence gets [DOC 1]
  const doc1Card = evidenceCards[0];
  expect(doc1Card).toHaveTextContent('[DOC 1]');
  expect(doc1Card).toHaveTextContent('Policy Document');

  // Check second document evidence gets [DOC 2]
  const doc2Card = evidenceCards[1];
  expect(doc2Card).toHaveTextContent('[DOC 2]');
  expect(doc2Card).toHaveTextContent('Procedure Guide');

  // Check structured evidence gets [DATA 1] (independent count)
  const dataCard = evidenceCards[2];
  expect(dataCard).toHaveTextContent('[DATA 1]');
  expect(dataCard).toHaveTextContent('Sales Database');
});