/**
 * EP-01A Prototype P1 — governed user and role administration.
 *
 * The workflow contract (same as the accepted #109 behaviour, extended):
 *  - selecting or editing a draft never issues a request;
 *  - "Review and save role" opens a reviewed-action dialog with the named
 *    target, old → new values and the access consequence;
 *  - Confirm is the only path that writes, and reports success only after the
 *    server confirms it, then reconciles from persisted state;
 *  - denied (403), conflict (409) and failure (500) states keep the draft,
 *    keep focus in the dialog and offer a recovery action.
 *
 * All requests go to the in-page mock (/api/ep01a/**). No real OpenJM
 * endpoint or authorization behaviour is touched.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ROLES,
  roleCapabilities,
  roleLabel,
  type Member,
  type MemberStatus,
  type RoleId,
} from './data'
import { ReviewedActionDialog } from './registry'
import type { DialogImplId } from './registry'

const STATUS_LABELS: Record<MemberStatus, string> = {
  active: 'Active',
  suspended: 'Suspended',
  invited: 'Invited',
}

type LoadState =
  | { phase: 'loading' }
  | { phase: 'ready' }
  | { phase: 'error'; message: string }
  | { phase: 'denied'; message: string }

export default function AdminRolesPrototype({ dialogImpl }: { dialogImpl: DialogImplId }) {
  const [members, setMembers] = useState<Member[]>([])
  const [load, setLoad] = useState<LoadState>({ phase: 'loading' })
  const [query, setQuery] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draftRole, setDraftRole] = useState<RoleId>('viewer')
  const [draftStatus, setDraftStatus] = useState<MemberStatus>('active')
  const [reviewOpen, setReviewOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [conflictCurrent, setConflictCurrent] = useState<Member | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const reviewTrigger = useRef<HTMLElement | null>(null)
  const tableScroll = useRef<HTMLDivElement | null>(null)
  const selectedRow = useRef<HTMLTableRowElement | null>(null)

  const loadMembers = useCallback(async () => {
    setLoad({ phase: 'loading' })
    try {
      const response = await fetch('/api/ep01a/admin/members')
      const body = (await response.json().catch(() => ({}))) as {
        members?: Member[]
        detail?: string
      }
      if (response.status === 403) {
        setLoad({
          phase: 'denied',
          message: body.detail || 'You do not have permission to view this directory.',
        })
        return
      }
      if (!response.ok) {
        setLoad({
          phase: 'error',
          message: body.detail || `The member directory could not be loaded (${response.status}).`,
        })
        return
      }
      setMembers(body.members || [])
      setLoad({ phase: 'ready' })
    } catch {
      setLoad({
        phase: 'error',
        message: 'The member directory could not be loaded. Check the connection and retry.',
      })
    }
  }, [])

  useEffect(() => {
    void loadMembers()
  }, [loadMembers])

  const selected = members.find((member) => member.id === selectedId) || null
  const dirty = Boolean(
    selected && (draftRole !== selected.role || draftStatus !== selected.status),
  )
  const ownerCount = members.filter((member) => member.role === 'owner').length
  const lastOwner = Boolean(selected?.role === 'owner' && ownerCount <= 1)

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  // On narrow screens the directory table scrolls horizontally (it keeps true
  // table semantics). When a member is selected, bring their identifying row
  // back into view and reset the horizontal position so the name is clearly
  // visible, not left behind the scroll.
  useEffect(() => {
    if (!selectedId) return
    if (
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(max-width: 720px)').matches
    ) {
      if (tableScroll.current) tableScroll.current.scrollLeft = 0
      selectedRow.current?.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedId])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return members.filter((member) => {
      if (roleFilter && member.role !== roleFilter) return false
      if (!needle) return true
      return `${member.name} ${member.email} ${member.department}`
        .toLowerCase()
        .includes(needle)
    })
  }, [members, query, roleFilter])

  function selectMember(member: Member) {
    setSelectedId(member.id)
    setDraftRole(member.role)
    setDraftStatus(member.status)
    setNotice(null)
    setDialogError(null)
    setConflictCurrent(null)
  }

  function discardDraft() {
    if (!selected) return
    setDraftRole(selected.role)
    setDraftStatus(selected.status)
  }

  async function confirmChange() {
    if (!selected || busy) return
    setBusy(true)
    setDialogError(null)
    setConflictCurrent(null)
    try {
      const response = await fetch(
        `/api/ep01a/admin/members/${selected.id}/role`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            role: draftRole,
            status: draftStatus,
            expected_revision: selected.revision,
          }),
        },
      )
      const body = (await response.json().catch(() => ({}))) as {
        member?: Member
        detail?: string
        current?: Member
      }
      if (response.status === 409) {
        setDialogError(
          body.detail || 'This member changed since you loaded the page.',
        )
        setConflictCurrent(body.current || null)
        return
      }
      if (!response.ok) {
        setDialogError(
          body.detail || `The change could not be saved (${response.status}).`,
        )
        return
      }
      const name = selected.name
      const roleChanged = draftRole !== selected.role
      const statusChanged = draftStatus !== selected.status
      const changes: string[] = []
      if (roleChanged) changes.push(`role changed to ${roleLabel(draftRole)}`)
      if (statusChanged)
        changes.push(`account status set to ${STATUS_LABELS[draftStatus]}`)
      setReviewOpen(false)
      setSelectedId(null)
      setNotice(`${name}'s ${changes.join(' and ')}.`)
      await loadMembers()
    } catch {
      setDialogError(
        'The change could not be saved. Your draft is intact; try again.',
      )
    } finally {
      setBusy(false)
    }
  }

  async function reloadLatest() {
    if (!selected) return
    try {
      const response = await fetch('/api/ep01a/admin/members')
      if (!response.ok) {
        setDialogError(
          `The latest version could not be loaded (${response.status}). Your review is unchanged; retry or cancel.`,
        )
        return
      }
      const body = (await response.json()) as { members: Member[] }
      setMembers(body.members)
      const fresh = body.members.find((member) => member.id === selected.id)
      if (fresh) {
        setSelectedId(fresh.id)
        setDraftRole(fresh.role)
        setDraftStatus(fresh.status)
      }
      setReviewOpen(false)
      setDialogError(null)
      setConflictCurrent(null)
      setNotice(
        `Loaded the latest version of ${selected.name}. Review your change again.`,
      )
    } catch {
      setDialogError(
        'The latest version could not be loaded. Your review is unchanged; retry or cancel.',
      )
    }
  }

  return (
    <section className="ep01a-panel" aria-label="People and roles">
      <header className="ep01a-section-head">
        <div>
          <h2>People &amp; roles</h2>
          <p className="ep01a-help">
            Change a member&apos;s role or account status through an explicit
            review. Nothing is saved until you confirm the named change.
          </p>
        </div>
        <button type="button" className="ep01a-btn" onClick={() => void loadMembers()}>
          Refresh directory
        </button>
      </header>

      {notice && (
        <p role="status" className="ep01a-notice">
          {notice}
        </p>
      )}

      <div className="ep01a-toolbar">
        <div className="ep01a-field">
          <label htmlFor="ep01a-members-search">Search members</label>
          <input
            id="ep01a-members-search"
            type="search"
            value={query}
            placeholder="Name, email or department"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className="ep01a-field">
          <label htmlFor="ep01a-members-role-filter">Filter by role</label>
          <select
            id="ep01a-members-role-filter"
            value={roleFilter}
            onChange={(event) => setRoleFilter(event.target.value)}
          >
            <option value="">All roles</option>
            {ROLES.map((role) => (
              <option key={role.id} value={role.id}>
                {role.label}
              </option>
            ))}
          </select>
        </div>
        <p className="ep01a-count" aria-live="polite">
          {load.phase === 'ready'
            ? `${filtered.length} of ${members.length} members`
            : ''}
        </p>
      </div>

      {load.phase === 'loading' && (
        <div className="ep01a-skeleton" role="status" aria-label="Loading members">
          <div className="ep01a-skeleton-row" />
          <div className="ep01a-skeleton-row" />
          <div className="ep01a-skeleton-row" />
        </div>
      )}

      {load.phase === 'error' && (
        <div className="ep01a-panel-inline" role="alert">
          <h3>Members could not be loaded</h3>
          <p>{load.message}</p>
          <button type="button" className="ep01a-btn" onClick={() => void loadMembers()}>
            Retry
          </button>
        </div>
      )}

      {load.phase === 'denied' && (
        <div className="ep01a-panel-inline" role="alert">
          <h3>Access denied</h3>
          <p>{load.message}</p>
          <p className="ep01a-help">
            Ask a workspace owner or administrator if you need the people
            directory. Your session remains active.
          </p>
        </div>
      )}

      {load.phase === 'ready' && (
        <div className="ep01a-split">
          <div
            className="ep01a-table-scroll"
            role="region"
            aria-label="Member directory"
            ref={tableScroll}
          >
            <table className="ep01a-table people">
              <thead>
                <tr>
                  <th scope="col">Member</th>
                  <th scope="col">Role</th>
                  <th scope="col">Status</th>
                  <th scope="col">Department</th>
                  <th scope="col">Last active</th>
                  <th scope="col">
                    <span className="ep01a-visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((member) => (
                  <tr
                    key={member.id}
                    className={member.id === selectedId ? 'selected' : ''}
                    ref={member.id === selectedId ? selectedRow : undefined}
                  >
                    <td>
                      <span className="ep01a-strong">{member.name}</span>
                      <small>{member.email}</small>
                    </td>
                    <td>
                      <span className={`ep01a-chip role-${member.role}`}>
                        {roleLabel(member.role)}
                      </span>
                    </td>
                    <td>
                      <span className={`ep01a-chip status-${member.status}`}>
                        {STATUS_LABELS[member.status]}
                      </span>
                    </td>
                    <td>{member.department}</td>
                    <td>{member.lastActive.slice(0, 10)}</td>
                    <td>
                      <button
                        type="button"
                        className="ep01a-btn quiet"
                        aria-label={`Manage ${member.name}`}
                        disabled={dirty && member.id !== selectedId}
                        onClick={() => selectMember(member)}
                      >
                        Manage
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!filtered.length && (
              <div className="ep01a-empty">
                <h3>No members match your search</h3>
                <p className="ep01a-help">
                  Check the spelling or clear the filters to see the full
                  directory.
                </p>
                <button
                  type="button"
                  className="ep01a-btn"
                  onClick={() => {
                    setQuery('')
                    setRoleFilter('')
                  }}
                >
                  Clear search and filters
                </button>
              </div>
            )}
          </div>

          {selected && (
            <aside className="ep01a-editor" aria-label={`Manage ${selected.name}`}>
              <h3>Manage {selected.name}</h3>
              <p className="ep01a-help">{selected.email}</p>
              {lastOwner && (
                <p className="ep01a-note">
                  {selected.name} is the workspace&apos;s last owner. Assign
                  another owner before changing this role.
                </p>
              )}
              <div className="ep01a-field">
                <label htmlFor="ep01a-draft-role">Role</label>
                <select
                  id="ep01a-draft-role"
                  value={draftRole}
                  onChange={(event) => setDraftRole(event.target.value as RoleId)}
                >
                  {ROLES.map((role) => (
                    <option key={role.id} value={role.id}>
                      {role.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="ep01a-field">
                <label htmlFor="ep01a-draft-status">Account status</label>
                <select
                  id="ep01a-draft-status"
                  value={draftStatus}
                  onChange={(event) =>
                    setDraftStatus(event.target.value as MemberStatus)
                  }
                >
                  {Object.entries(STATUS_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <p className="ep01a-help" data-role-capability={draftRole}>
                {roleCapabilities(draftRole)}
              </p>
              {dirty && (
                <p className="ep01a-pending" role="status">
                  Pending change: {roleLabel(selected.role)} → {roleLabel(draftRole)}
                  {draftStatus !== selected.status
                    ? ` · ${STATUS_LABELS[selected.status]} → ${STATUS_LABELS[draftStatus]}`
                    : ''}
                  {' '}Save or discard the change before managing another member.
                </p>
              )}
              <div className="ep01a-editor-actions">
                <button
                  type="button"
                  className="ep01a-btn primary"
                  disabled={!dirty}
                  onClick={(event) => {
                    reviewTrigger.current = event.currentTarget
                    setReviewOpen(true)
                    setDialogError(null)
                    setConflictCurrent(null)
                  }}
                >
                  Review and save role
                </button>
                <button
                  type="button"
                  className="ep01a-btn"
                  disabled={!dirty}
                  onClick={discardDraft}
                >
                  Discard changes
                </button>
              </div>
            </aside>
          )}
        </div>
      )}

      {selected && (
        <ReviewedActionDialog
          impl={dialogImpl}
          open={reviewOpen}
          title="Change user role?"
          description="Review the exact change below. Nothing is saved until you confirm it."
          rows={[
            { label: 'Member', value: `${selected.name} · ${selected.email}` },
            {
              label: 'Role',
              value: `${roleLabel(selected.role)} → ${roleLabel(draftRole)}`,
              tone: 'emphasis',
            },
            {
              label: 'Account status',
              value:
                draftStatus === selected.status
                  ? `${STATUS_LABELS[selected.status]} (unchanged)`
                  : `${STATUS_LABELS[selected.status]} → ${STATUS_LABELS[draftStatus]}`,
            },
          ]}
          impact={`This is the access ${selected.name} will have after confirmation: ${roleCapabilities(draftRole)} The change is recorded in the workspace audit trail.`}
          confirmLabel="Confirm role change"
          busy={busy}
          error={dialogError}
          returnFocusTo={reviewTrigger.current}
          extra={
            conflictCurrent ? (
              <div className="ep01a-conflict" role="region" aria-label="Conflict details">
                <p>
                  Current on the server:{' '}
                  <strong>{roleLabel(conflictCurrent.role)}</strong> · revision{' '}
                  {conflictCurrent.revision}. Your review used revision{' '}
                  {selected.revision}.
                </p>
                <button
                  type="button"
                  className="ep01a-btn"
                  onClick={() => void reloadLatest()}
                >
                  Reload the latest version
                </button>
              </div>
            ) : null
          }
          onConfirm={() => void confirmChange()}
          onCancel={() => {
            if (busy) return
            setReviewOpen(false)
            setDialogError(null)
            setConflictCurrent(null)
          }}
        />
      )}
    </section>
  )
}
