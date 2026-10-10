import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'

/**
 * BV5-C end-to-end (browser side): a Chat turn whose assistant message carries
 * an artifact reference renders a real download card built from server
 * metadata only — never the file content.
 */
const DEV_AUTH_CONFIG = {
  auth_mode: 'dev',
  oidc_configured: false,
  authorization_endpoint: null,
  issuer: null,
  client_id: null,
  tenant_header: 'X-OpenJM-Tenant',
}

const artifactCard = {
  id: 'art-1',
  title: 'Weekly summary',
  filename: 'weekly-summary.md',
  mime_type: 'text/markdown',
  artifact_format: 'markdown',
  size_bytes: 24,
  state: 'active',
  is_evidence_backed: false,
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  created_at: '2026-01-01T00:00:00Z',
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

test('an assistant artifact reference renders a downloadable card without source', async () => {
  const posts: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method || 'GET'
      if (url === '/api/auth/config') return jsonResponse(DEV_AUTH_CONFIG)
      if (['/api/conversations', '/api/knowledge/documents', '/api/data/sources', '/api/reports'].includes(url)) {
        return jsonResponse([])
      }
      if (url === '/api/chat' && method === 'POST') {
        posts.push(String(init?.body))
        return jsonResponse({
          conversation_id: 'conv-1',
          message_id: 'msg-1',
          // The visible answer has the directive already stripped by the server.
          answer: 'Here is the file you asked for.',
          execution_class: 'general',
          mode: 'chat',
          evidence: [],
          artifacts: [artifactCard],
        })
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }),
  )

  render(<App />)
  const composer = await screen.findByPlaceholderText('Ask OpenJM about your work...')
  fireEvent.change(composer, { target: { value: 'make me a markdown file' } })
  fireEvent.keyDown(composer, { key: 'Enter', shiftKey: false })

  // The card is built from server metadata and offers a download action.
  expect(await screen.findByText('weekly-summary.md')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Download weekly-summary.md' })).toBeTruthy()
  expect(screen.getByText(/not a governed report/)).toBeTruthy()
  // Raw source never appears in the transcript.
  expect(screen.queryByText(/```artifact/)).toBeNull()

  await waitFor(() => expect(posts).toHaveLength(1))
})
