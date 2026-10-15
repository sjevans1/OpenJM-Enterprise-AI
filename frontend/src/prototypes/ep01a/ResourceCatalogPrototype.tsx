/**
 * EP-01A Prototype P2 — enterprise resource catalog.
 *
 * A dense, searchable, server-paginated catalog of synthetic business
 * resources (Knowledge documents and PostgreSQL data sources), with:
 *  - truthful totals and coverage ("showing 1–25 of 168") from the response,
 *  - stable row identity across pages and re-sorts,
 *  - loading / refreshing / empty / stale / failed / denied states with
 *    recovery,
 *  - a detail view with paginated metadata entries,
 *  - keyboard operation and internal table overflow on narrow screens.
 *
 * Read-only: the catalog issues GET requests only, against the in-page mock.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CLASSIFICATION_LABELS,
  STATUS_LABELS,
  type MetadataEntry,
  type Resource,
} from './data'

type LoadPhase = 'initial' | 'ready' | 'refreshing' | 'error' | 'denied'

type ListResponse = {
  items: Resource[]
  total: number
  page: number
  page_size: number
  revision: number
  stale: boolean
  last_refreshed: string
}

type DetailState =
  | { phase: 'closed' }
  | { phase: 'loading'; resource: Resource }
  | { phase: 'ready'; resource: Resource; metadata: MetadataEntry[] }
  | { phase: 'failed'; resource: Resource; message: string }

const PAGE_SIZES = [10, 25, 50]

function formatTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'
}

export default function ResourceCatalogPrototype() {
  const [phase, setPhase] = useState<LoadPhase>('initial')
  const [items, setItems] = useState<Resource[]>([])
  const [total, setTotal] = useState(0)
  const [revision, setRevision] = useState(0)
  const [stale, setStale] = useState(false)
  const [lastRefreshed, setLastRefreshed] = useState('')
  const [errorMessage, setErrorMessage] = useState('')

  const [query, setQuery] = useState('')
  const [kind, setKind] = useState('')
  const [classification, setClassification] = useState('')
  const [status, setStatus] = useState('')
  const [sort, setSort] = useState('updated')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)

  const [detail, setDetail] = useState<DetailState>({ phase: 'closed' })
  const detailTrigger = useRef<HTMLElement | null>(null)
  const detailSeq = useRef(0)
  const requestSeq = useRef(0)
  const [debouncedQuery, setDebouncedQuery] = useState('')

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedQuery(query), 120)
    return () => window.clearTimeout(handle)
  }, [query])

  const loadList = useCallback(
    async (opts?: { refresh?: boolean; keepRows?: boolean; pageOverride?: number }) => {
      const seq = ++requestSeq.current
      const targetPage = opts?.pageOverride ?? page
      setPhase((current) =>
        opts?.keepRows || current === 'refreshing' ? 'refreshing' : 'initial',
      )
      setErrorMessage('')
      const params = new URLSearchParams({
        page: String(targetPage),
        page_size: String(pageSize),
        sort,
      })
      if (debouncedQuery.trim()) params.set('query', debouncedQuery.trim())
      if (kind) params.set('kind', kind)
      if (classification) params.set('classification', classification)
      if (status) params.set('status', status)
      if (opts?.refresh) params.set('refresh', '1')
      try {
        const response = await fetch(`/api/ep01a/catalog?${params.toString()}`)
        if (seq !== requestSeq.current) return // a newer request superseded this one
        const body = (await response.json().catch(() => ({}))) as
          | ListResponse
          | { detail?: string }
        if (response.status === 403) {
          setPhase('denied')
          setErrorMessage(
            ('detail' in body && body.detail) ||
              'You do not have permission to view these sources.',
          )
          return
        }
        if (!response.ok) {
          setPhase('error')
          setErrorMessage(
            ('detail' in body && body.detail) ||
              `The catalog could not be loaded (${response.status}).`,
          )
          return
        }
        const data = body as ListResponse
        setItems(data.items)
        setTotal(data.total)
        setRevision(data.revision)
        setStale(data.stale)
        setLastRefreshed(data.last_refreshed)
        setPage(data.page)
        setPhase('ready')
      } catch {
        if (seq !== requestSeq.current) return
        setPhase('error')
        setErrorMessage(
          'The catalog could not be loaded. Check the connection and retry.',
        )
      }
    },
    [classification, debouncedQuery, kind, page, pageSize, sort, status],
  )

  useEffect(() => {
    void loadList()
  }, [loadList])

  useEffect(() => {
    setPage(1)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery, kind, classification, status, sort, pageSize])

  async function openDetail(resource: Resource, trigger: HTMLElement) {
    detailTrigger.current = trigger
    const seq = ++detailSeq.current
    setDetail({ phase: 'loading', resource })
    try {
      const response = await fetch(`/api/ep01a/catalog/${resource.id}`)
      if (seq !== detailSeq.current) return // superseded by a newer view/close
      const body = (await response.json().catch(() => ({}))) as {
        resource: Resource
        metadata: MetadataEntry[]
        detail?: string
      }
      if (!response.ok) {
        setDetail({
          phase: 'failed',
          resource,
          message:
            body.detail || `The resource details could not be loaded (${response.status}).`,
        })
        return
      }
      setDetail({ phase: 'ready', resource: body.resource, metadata: body.metadata })
    } catch {
      if (seq !== detailSeq.current) return
      setDetail({
        phase: 'failed',
        resource,
        message: 'The resource details could not be loaded. Retry.',
      })
    }
  }

  function closeDetail() {
    detailSeq.current += 1 // invalidate any in-flight detail request
    setDetail({ phase: 'closed' })
    const trigger = detailTrigger.current
    if (trigger?.isConnected) trigger.focus()
    detailTrigger.current = null
  }

  const firstRow = total === 0 ? 0 : (page - 1) * pageSize + 1
  const lastRow = Math.min(page * pageSize, total)
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const filtersActive = Boolean(
    query.trim() || kind || classification || status,
  )

  return (
    <section className="ep01a-panel" aria-label="Resource catalog">
      <header className="ep01a-section-head">
        <div>
          <h2>Sources &amp; documents</h2>
          <p className="ep01a-help">
            Every source and document in this workspace, with classification,
            ownership and freshness. Totals reflect the server-side catalog.
          </p>
        </div>
        <button
          type="button"
          className="ep01a-btn"
          onClick={() => void loadList({ refresh: true, keepRows: true })}
        >
          Refresh catalog
        </button>
      </header>

      {stale && phase !== 'initial' && (
        <div className="ep01a-stale" role="status">
          <p>
            This list is showing data from {formatTime(lastRefreshed)}. An
            upstream source has refreshed since; refresh to reconcile.
          </p>
          <button
            type="button"
            className="ep01a-btn"
            onClick={() => void loadList({ refresh: true, keepRows: true })}
          >
            Refresh now
          </button>
        </div>
      )}

      <div className="ep01a-toolbar">
        <div className="ep01a-field">
          <label htmlFor="ep01a-catalog-search">Search</label>
          <input
            id="ep01a-catalog-search"
            type="search"
            value={query}
            placeholder="Name, owner, tag or source"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className="ep01a-field">
          <label htmlFor="ep01a-catalog-kind">Type</label>
          <select
            id="ep01a-catalog-kind"
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            <option value="">All types</option>
            <option value="document">Knowledge document</option>
            <option value="database">Business data source</option>
          </select>
        </div>
        <div className="ep01a-field">
          <label htmlFor="ep01a-catalog-classification">Classification</label>
          <select
            id="ep01a-catalog-classification"
            value={classification}
            onChange={(event) => setClassification(event.target.value)}
          >
            <option value="">All classifications</option>
            {Object.entries(CLASSIFICATION_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="ep01a-field">
          <label htmlFor="ep01a-catalog-status">Status</label>
          <select
            id="ep01a-catalog-status"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="ep01a-field">
          <label htmlFor="ep01a-catalog-sort">Sort</label>
          <select
            id="ep01a-catalog-sort"
            value={sort}
            onChange={(event) => setSort(event.target.value)}
          >
            <option value="updated">Recently updated</option>
            <option value="name">Name (A–Z)</option>
            <option value="records">Largest first</option>
          </select>
        </div>
      </div>

      {phase === 'initial' && (
        <div className="ep01a-skeleton" role="status" aria-label="Loading catalog">
          <div className="ep01a-skeleton-row" />
          <div className="ep01a-skeleton-row" />
          <div className="ep01a-skeleton-row" />
          <div className="ep01a-skeleton-row" />
        </div>
      )}

      {phase === 'error' && (
        <div className="ep01a-panel-inline" role="alert">
          <h3>Catalog could not be loaded</h3>
          <p>{errorMessage}</p>
          <button
            type="button"
            className="ep01a-btn"
            onClick={() => void loadList({ keepRows: false })}
          >
            Retry
          </button>
        </div>
      )}

      {phase === 'denied' && (
        <div className="ep01a-panel-inline" role="alert">
          <h3>Access denied</h3>
          <p>{errorMessage}</p>
          <p className="ep01a-help">
            Your role does not include this source scope. Ask a steward if you
            believe you should have access.
          </p>
        </div>
      )}

      {(phase === 'ready' || phase === 'refreshing') && (
        <>
          <p className="ep01a-count" aria-live="polite">
            {total === 0
              ? 'No resources'
              : `Showing ${firstRow}–${lastRow} of ${total} resources`}
            {phase === 'refreshing' ? ' · refreshing…' : ''}
            {revision ? ` · catalog revision ${revision}` : ''}
          </p>

          <div className="ep01a-table-scroll" role="region" aria-label="Resource table">
            {items.length > 0 && (
              <table className="ep01a-table wide">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Type</th>
                  <th scope="col">Classification</th>
                  <th scope="col">Department</th>
                  <th scope="col">Owner</th>
                  <th scope="col">Size</th>
                  <th scope="col">Updated</th>
                  <th scope="col">Status</th>
                  <th scope="col">
                    <span className="ep01a-visually-hidden">Details</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((resource) => (
                  <tr key={resource.id} data-resource-id={resource.id}>
                    <td className="ep01a-name-cell">
                      <span className="ep01a-strong">{resource.name}</span>
                      {!!resource.tags.length && (
                        <small>
                          {resource.tags.map((tag) => (
                            <span className="ep01a-tag" key={tag}>
                              {tag}
                            </span>
                          ))}
                        </small>
                      )}
                    </td>
                    <td>{resource.kind === 'database' ? 'Business data' : 'Knowledge'}</td>
                    <td>
                      <span className={`ep01a-chip class-${resource.classification}`}>
                        {CLASSIFICATION_LABELS[resource.classification]}
                      </span>
                    </td>
                    <td>{resource.department}</td>
                    <td>{resource.owner}</td>
                    <td>{resource.sizeLabel}</td>
                    <td>{resource.updatedAt.slice(0, 10)}</td>
                    <td>
                      <span className={`ep01a-chip status-${resource.status}`}>
                        {STATUS_LABELS[resource.status]}
                      </span>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="ep01a-btn quiet"
                        aria-label={`View details for ${resource.name}`}
                        onClick={(event) => void openDetail(resource, event.currentTarget)}
                      >
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            )}

            {!items.length && (
              <div className="ep01a-empty">
                <h3>No resources match</h3>
                <p className="ep01a-help">
                  {filtersActive
                    ? 'Adjust the search or filters to widen the result set.'
                    : 'This workspace has no resources yet.'}
                </p>
                {filtersActive && (
                  <button
                    type="button"
                    className="ep01a-btn"
                    onClick={() => {
                      setQuery('')
                      setKind('')
                      setClassification('')
                      setStatus('')
                    }}
                  >
                    Clear search and filters
                  </button>
                )}
              </div>
            )}
          </div>

          <nav className="ep01a-pager" aria-label="Catalog pages">
            <button
              type="button"
              className="ep01a-btn"
              disabled={page <= 1 || phase === 'refreshing'}
              onClick={() => void loadList({ keepRows: true, pageOverride: page - 1 })}
            >
              Previous page
            </button>
            <span>
              Page {page} of {pageCount}
            </span>
            <button
              type="button"
              className="ep01a-btn"
              disabled={page >= pageCount || phase === 'refreshing'}
              onClick={() => void loadList({ keepRows: true, pageOverride: page + 1 })}
            >
              Next page
            </button>
            <div className="ep01a-field inline">
              <label htmlFor="ep01a-page-size">Rows per page</label>
              <select
                id="ep01a-page-size"
                value={pageSize}
                onChange={(event) => setPageSize(Number(event.target.value))}
              >
                {PAGE_SIZES.map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
              </select>
            </div>
          </nav>
        </>
      )}

      {detail.phase !== 'closed' && (
        <div
          className="ep01a-detail-overlay"
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation()
              closeDetail()
            }
          }}
        >
          <div
            className="ep01a-detail"
            role="dialog"
            aria-modal="true"
            aria-labelledby="ep01a-detail-title"
          >
            <div className="ep01a-detail-head">
              <h3 id="ep01a-detail-title">{detail.resource.name}</h3>
              <button
                type="button"
                className="ep01a-btn"
                onClick={closeDetail}
                aria-label="Close details"
                autoFocus
              >
                Close
              </button>
            </div>
            {detail.phase === 'loading' && (
              <div className="ep01a-skeleton" role="status" aria-label="Loading details">
                <div className="ep01a-skeleton-row" />
                <div className="ep01a-skeleton-row" />
              </div>
            )}
            {detail.phase === 'failed' && (
              <div className="ep01a-panel-inline" role="alert">
                <h4>Details unavailable</h4>
                <p>{detail.message}</p>
                <button
                  type="button"
                  className="ep01a-btn"
                  onClick={(event) =>
                    void openDetail(detail.resource, event.currentTarget)
                  }
                >
                  Retry details
                </button>
              </div>
            )}
            {detail.phase === 'ready' && (
              <>
                <dl className="ep01a-detail-meta">
                  <div>
                    <dt>Classification</dt>
                    <dd>
                      <span className={`ep01a-chip class-${detail.resource.classification}`}>
                        {CLASSIFICATION_LABELS[detail.resource.classification]}
                      </span>
                    </dd>
                  </div>
                  <div>
                    <dt>Status</dt>
                    <dd>{STATUS_LABELS[detail.resource.status]}</dd>
                  </div>
                  <div>
                    <dt>Source</dt>
                    <dd>{detail.resource.source}</dd>
                  </div>
                  <div>
                    <dt>Owner</dt>
                    <dd>{detail.resource.owner}</dd>
                  </div>
                </dl>
                <h4>Metadata ({detail.metadata.length} entries)</h4>
                <dl className="ep01a-metadata">
                  {detail.metadata.map((entry) => (
                    <div key={entry.key}>
                      <dt>{entry.label}</dt>
                      <dd>{entry.value}</dd>
                    </div>
                  ))}
                </dl>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
