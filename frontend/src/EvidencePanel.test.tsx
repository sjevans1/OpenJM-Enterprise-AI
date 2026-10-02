import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { EvidencePanel } from './App';
import type { Evidence } from './api';

test('EvidencePanel shows independent [DOC N] and [DATA N] numbering', () => {
  const mockEvidence: Evidence[] = [
    {
      source_type: 'document',
      title: 'Policy Document',
      passage: 'The policy states that threshold is 300.',
      source_id: 'doc-1',
      metadata: {},
    },
    {
      source_type: 'document',
      title: 'Procedure Guide',
      passage: 'Follow the procedure for review.',
      source_id: 'doc-2',
      metadata: {},
    },
    {
      source_type: 'structured_query',
      title: 'Sales Database',
      passage: '{"columns":["customer"],"rows":[["Acme Corp"],["Beta LLC"]],"row_count":2}',
      source_id: 'source-1',
      metadata: {},
    }
  ];

  render(<EvidencePanel evidence={mockEvidence} />);

  expect(screen.getByText('[DOC 1]')).toBeTruthy();
  expect(screen.getByText('[DOC 2]')).toBeTruthy();
  expect(screen.getByText('[DATA 1]')).toBeTruthy();
  expect(screen.queryByText('[DATA 3]')).toBeNull();
});