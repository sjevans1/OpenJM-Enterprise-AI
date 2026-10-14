import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type AdminDataAccess,
  type AdminDepartment,
  type AdminGroup,
  type GovernedResource,
  type GovernancePolicyInput,
} from './api'
import { useConfirmation } from './ConfirmAction'

const CLASSIFICATIONS: Record<string, string> = {
  public: 'Public',
  internal: 'Internal',
  confidential: 'Confidential',
  highly_restricted: 'Highly restricted',
}

type Selection = {
  kind: 'source' | 'document'
  resource: GovernedResource
  policy: GovernancePolicyInput
}

export default function PolicyEditor({
  departments,
  groups,
  onDirtyChange,
}: {
  departments: AdminDepartment[]
  groups: AdminGroup[]
  onDirtyChange?: (dirty: boolean) => void
}) {
  const [data, setData] = useState<AdminDataAccess | null>(null)
  const [selected, setSelected] = useState<Selection | null>(null)
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const confirmation = useConfirmation()
  useEffect(() => {
    onDirtyChange?.(selected !== null)
  }, [selected, onDirtyChange])
  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await api.admin.dataAccess())
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'Unable to load policies. Try Refresh.',
      )
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])
  useEffect(() => {
    if (!selected) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [selected])

  function edit(kind: Selection['kind'], resource: GovernedResource) {
    if (!Array.isArray(resource.allowed_group_ids)) {
      setError(
        'The full access policy could not be loaded. Refresh before editing.',
      )
      return
    }
    setNotice(null)
    setSelected({
      kind,
      resource,
      policy: {
        classification: resource.classification || 'highly_restricted',
        department_id: resource.department_id || null,
        tenant_visible: resource.tenant_visible === true,
        allowed_group_ids: [...resource.allowed_group_ids],
      },
    })
  }
  function change(values: Partial<GovernancePolicyInput>) {
    setSelected((current) =>
      current ? { ...current, policy: { ...current.policy, ...values } } : null,
    )
  }
  const title =
    selected?.resource.name || selected?.resource.title || 'Selected item'
  const restricted =
    selected &&
    ['confidential', 'highly_restricted'].includes(
      selected.policy.classification,
    )
  const rows = [
    ...(data?.sources || []).map((resource) => ({
      kind: 'source' as const,
      resource,
    })),
    ...(data?.documents || []).map((resource) => ({
      kind: 'document' as const,
      resource,
    })),
  ].filter(({ resource }) =>
    `${resource.name || resource.title} ${resource.classification}`
      .toLowerCase()
      .includes(search.toLowerCase().trim()),
  )

  return (
    <div className="admin-section">
      <h2>Data Access</h2>
      <p className="admin-help">
        Classify business data and Knowledge documents, then choose their
        audience. Every save is reviewed before it takes effect.
      </p>
      <p className="admin-note">
        Public and Internal can be shared across this workspace. Confidential
        and Highly restricted require an explicit group, department or other
        existing grant. “Public” never shares outside your company tenant.
      </p>
      {error && (
        <p role="alert" className="auth-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="admin-success">
          {notice}
        </p>
      )}
      {loading && <p role="status">Loading policies…</p>}
      <div className="admin-toolbar">
        <label>
          Search sources and documents
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <button
          type="button"
          disabled={loading || !!selected}
          onClick={() => void load()}
        >
          Refresh policies
        </button>
      </div>
      <div className="admin-table-scroll">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Classification</th>
              <th>Sharing policy</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ kind, resource }) => (
              <tr key={`${kind}-${resource.id}`}>
                <td>{resource.name || resource.title || 'Untitled'}</td>
                <td>{kind === 'source' ? 'Business data' : 'Knowledge'}</td>
                <td>
                  {CLASSIFICATIONS[resource.classification || ''] ||
                    'Unclassified'}
                </td>
                <td>
                  {resource.tenant_visible &&
                  ['public', 'internal'].includes(resource.classification || '')
                    ? 'Workspace-wide'
                    : 'Explicit grants'}
                  {resource.department_id && (
                    <small>
                      Department:{' '}
                      {departments.find(
                        (row) => row.id === resource.department_id,
                      )?.name || 'Unavailable department'}
                    </small>
                  )}
                  {!!resource.allowed_group_ids?.length && (
                    <small>
                      Groups:{' '}
                      {resource.allowed_group_ids
                        .map(
                          (id) =>
                            groups.find((row) => row.id === id)?.name ||
                            'Unavailable group',
                        )
                        .join(', ')}
                    </small>
                  )}
                </td>
                <td>
                  <button
                    type="button"
                    disabled={loading || !!selected}
                    onClick={() => edit(kind, resource)}
                  >
                    Edit policy for {resource.name || resource.title}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!loading && !rows.length && !error && (
        <p className="admin-help">
          {search
            ? 'No items match your search.'
            : 'No sources or documents yet. Add documents in Knowledge or connect a database in Data, then configure access here.'}
        </p>
      )}
      {selected && (
        <form
          className="admin-subsection policy-form"
          onSubmit={(event) => {
            event.preventDefault()
            const policy = {
              ...selected.policy,
              tenant_visible: restricted
                ? false
                : selected.policy.tenant_visible,
              clear_department: !selected.policy.department_id,
            }
            const groupNames = policy.allowed_group_ids
              .map(
                (id) =>
                  groups.find((group) => group.id === id)?.name ||
                  'Unavailable group',
              )
              .join(', ')
            const audience =
              policy.tenant_visible && !restricted
                ? 'All authorized members of this workspace'
                : 'Existing explicit grants only'
            confirmation.request({
              title: 'Save access policy?',
              confirmLabel: 'Confirm policy change',
              description: `${title}\nClassification: ${CLASSIFICATIONS[selected.resource.classification || ''] || 'Unclassified'} → ${CLASSIFICATIONS[policy.classification]}.\nSharing: ${audience}.\nDepartment: ${departments.find((row) => row.id === policy.department_id)?.name || 'None'}. Groups: ${groupNames || 'None'}.\nThis affects which evidence users can retrieve and may change access to saved reports.`,
              onConfirm: async () => {
                await api.admin.updatePolicy(
                  selected.kind,
                  selected.resource.id,
                  policy,
                )
                setSelected(null)
                setNotice(`Access policy saved for ${title}.`)
                await load()
              },
            })
          }}
        >
          <h3>Edit policy: {title}</h3>
          <fieldset
            disabled={confirmation.isOpen}
            className="admin-form-fields"
          >
            <label>
              Classification
              <select
                value={selected.policy.classification}
                required
                onChange={(event) =>
                  change({
                    classification: event.target.value,
                    tenant_visible: ['public', 'internal'].includes(
                      event.target.value,
                    )
                      ? selected.policy.tenant_visible
                      : false,
                  })
                }
              >
                {!CLASSIFICATIONS[selected.policy.classification] && (
                  <option value={selected.policy.classification} disabled>
                    Choose a known classification
                  </option>
                )}
                {Object.entries(CLASSIFICATIONS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Department
              <select
                value={selected.policy.department_id || ''}
                onChange={(event) =>
                  change({ department_id: event.target.value || null })
                }
              >
                <option value="">No department</option>
                {departments.map((row) => (
                  <option
                    key={row.id}
                    value={row.id}
                    disabled={row.status !== 'active'}
                  >
                    {row.name}
                    {row.status !== 'active' ? ' (inactive)' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="admin-checkbox">
              <input
                type="checkbox"
                checked={selected.policy.tenant_visible && !restricted}
                disabled={!!restricted}
                onChange={(event) =>
                  change({ tenant_visible: event.target.checked })
                }
              />
              Share across this workspace
            </label>
            <fieldset className="policy-groups">
              <legend>Allowed groups</legend>
              {groups.map((group) => (
                <label className="admin-checkbox" key={group.id}>
                  <input
                    type="checkbox"
                    checked={selected.policy.allowed_group_ids.includes(
                      group.id,
                    )}
                    disabled={group.status !== 'active'}
                    onChange={(event) =>
                      change({
                        allowed_group_ids: event.target.checked
                          ? [...selected.policy.allowed_group_ids, group.id]
                          : selected.policy.allowed_group_ids.filter(
                              (id) => id !== group.id,
                            ),
                      })
                    }
                  />
                  {group.name}
                  {group.status !== 'active' ? ' (inactive)' : ''}
                </label>
              ))}
              {!groups.length && (
                <p>
                  Create a group in Administration to share with a specific
                  team.
                </p>
              )}
            </fieldset>
            <p className="admin-help">
              This is a sharing-policy summary, not a list of effective
              permissions. Ownership, existing explicit grants, role permissions
              and source availability still apply.
            </p>
            <div className="admin-actions">
              <button
                type="submit"
                className="primary-action"
                disabled={!CLASSIFICATIONS[selected.policy.classification]}
              >
                Review and save policy
              </button>
              <button type="button" onClick={() => setSelected(null)}>
                Cancel policy changes
              </button>
            </div>
          </fieldset>
        </form>
      )}
      {confirmation.dialog}
    </div>
  )
}
