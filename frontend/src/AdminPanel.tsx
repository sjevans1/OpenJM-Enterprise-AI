/**
 * BV3-B client administration surface.
 *
 * Business-oriented by design: Users, Departments, Groups, Data Stewards,
 * Data Access, Usage / Plan. It deliberately exposes no operation ids, scheduler
 * internals, provider credentials or raw platform capabilities, and the backend
 * remains the authority for every action it offers.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Building2,
  Database,
  Gauge,
  ShieldCheck,
  UserPlus,
  Users,
  UsersRound,
} from 'lucide-react'

import {
  api,
  type AdminDepartment,
  type AdminGroup,
  type AdminMember,
  type AdminSteward,
  type AdminUsageSummary,
} from './api'

type AdminTab = 'users' | 'departments' | 'groups' | 'stewards' | 'data-access' | 'usage'

const TABS: { id: AdminTab; label: string; icon: typeof Users }[] = [
  { id: 'users', label: 'Users', icon: Users },
  { id: 'departments', label: 'Departments', icon: Building2 },
  { id: 'groups', label: 'Groups', icon: UsersRound },
  { id: 'stewards', label: 'Data Stewards', icon: ShieldCheck },
  { id: 'data-access', label: 'Data Access', icon: Database },
  { id: 'usage', label: 'Usage / Plan', icon: Gauge },
]

const ROLE_LABELS: Record<string, string> = {
  viewer: 'Viewer',
  editor: 'Editor',
  admin: 'Administrator',
  owner: 'Owner',
}

function displayName(member: AdminMember): string {
  return member.display_name || member.email || member.subject
}

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message
  return fallback
}

export default function AdminPanel() {
  const [tab, setTab] = useState<AdminTab>('users')
  const [members, setMembers] = useState<AdminMember[]>([])
  const [departments, setDepartments] = useState<AdminDepartment[]>([])
  const [groups, setGroups] = useState<AdminGroup[]>([])
  const [stewards, setStewards] = useState<AdminSteward[]>([])
  const [usage, setUsage] = useState<AdminUsageSummary | null>(null)
  const [sources, setSources] = useState<
    {
      id: string
      name?: string | null
      classification?: string | null
      department_id?: string | null
      tenant_visible?: boolean | null
    }[]
  >([])
  const [documents, setDocuments] = useState<
    { id: string; title?: string | null; classification?: string | null }[]
  >([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [inviteSubject, setInviteSubject] = useState('')
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('editor')
  const [departmentName, setDepartmentName] = useState('')
  const [groupName, setGroupName] = useState('')
  const [groupDepartment, setGroupDepartment] = useState('')
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null)
  const [stewardPrincipal, setStewardPrincipal] = useState('')
  const [stewardScopeType, setStewardScopeType] = useState('department')
  const [stewardScopeId, setStewardScopeId] = useState('')

  const refresh = useCallback(async () => {
    setError(null)
    try {
      const [memberList, departmentList, groupList] = await Promise.all([
        api.admin.members(),
        api.admin.departments(),
        api.admin.groups(),
      ])
      setMembers(memberList)
      setDepartments(departmentList)
      setGroups(groupList)
    } catch (caught) {
      setError(errorText(caught, 'Unable to load administration data'))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const loadStewards = useCallback(async () => {
    setError(null)
    try {
      setStewards(await api.admin.stewards())
    } catch (caught) {
      setError(errorText(caught, 'Unable to load data stewards'))
    }
  }, [])

  const loadDataAccess = useCallback(async () => {
    setError(null)
    try {
      const result = await api.admin.dataAccess()
      setSources(result.sources)
      setDocuments(result.documents)
    } catch (caught) {
      setError(errorText(caught, 'Unable to load governed data'))
    }
  }, [])

  const loadUsage = useCallback(async () => {
    setError(null)
    try {
      setUsage(await api.admin.usage())
    } catch (caught) {
      setError(errorText(caught, 'Unable to load usage'))
    }
  }, [])

  useEffect(() => {
    if (tab === 'stewards') void loadStewards()
    if (tab === 'data-access') void loadDataAccess()
    if (tab === 'usage') void loadUsage()
  }, [tab, loadStewards, loadDataAccess, loadUsage])

  const scopeOptions = useMemo(() => {
    if (stewardScopeType === 'department') {
      return departments.map((row) => ({ id: row.id, label: row.name }))
    }
    if (stewardScopeType === 'group') {
      return groups.map((row) => ({ id: row.id, label: row.name }))
    }
    return []
  }, [stewardScopeType, departments, groups])

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (caught) {
      setError(errorText(caught, fallback))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="admin-surface">
      <header className="workspace-header">
        <div>
          <div className="eyebrow">Client administration</div>
          <h1>Administration</h1>
        </div>
        <span className="system-badge">
          <ShieldCheck size={14} />
          Governed by your permissions
        </span>
      </header>

      <nav className="admin-tabs" aria-label="Administration sections">
        {TABS.map((item) => (
          <button
            key={item.id}
            className={tab === item.id ? 'nav-item active' : 'nav-item'}
            onClick={() => setTab(item.id)}
          >
            <item.icon size={16} />
            {item.label}
          </button>
        ))}
      </nav>

      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      {tab === 'users' && (
        <div className="admin-section">
          <h2>Users</h2>
          <p className="admin-help">
            People who can sign in to this workspace, and the role that decides what they can do.
          </p>

          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault()
              void run(async () => {
                await api.admin.provisionMember({
                  subject: inviteSubject.trim(),
                  role: inviteRole,
                  email: inviteEmail.trim() || null,
                })
                setInviteSubject('')
                setInviteEmail('')
                await refresh()
              }, 'Unable to add that user')
            }}
          >
            <label>
              Email or sign-in address
              <input
                value={inviteSubject}
                onChange={(event) => setInviteSubject(event.target.value)}
                placeholder="person@example.com"
                required
              />
            </label>
            <label>
              Role
              <select value={inviteRole} onChange={(event) => setInviteRole(event.target.value)}>
                {Object.entries(ROLE_LABELS)
                  .filter(([value]) => value !== 'owner')
                  .map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
              </select>
            </label>
            <button type="submit" disabled={busy || !inviteSubject.trim()}>
              <UserPlus size={15} />
              Add user
            </button>
          </form>

          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">User</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Access</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.principal_id}>
                  <td>{displayName(member)}</td>
                  <td>
                    <select
                      aria-label={`Role for ${displayName(member)}`}
                      value={member.role}
                      onChange={(event) => {
                        const role = event.target.value
                        void run(async () => {
                          await api.admin.changeMemberRole(member.principal_id, role)
                          await refresh()
                        }, 'Unable to change that role')
                      }}
                    >
                      {Object.entries(ROLE_LABELS).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{member.status === 'active' ? 'Active' : 'Revoked'}</td>
                  <td>
                    {member.department_ids.length} department(s), {member.group_ids.length} group(s)
                  </td>
                  <td className="admin-actions">
                    {member.status === 'active' ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            await api.admin.revokeMember(member.principal_id)
                            await refresh()
                          }, 'Unable to revoke that user')
                        }
                      >
                        Revoke access
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            await api.admin.reactivateMember(member.principal_id)
                            await refresh()
                          }, 'Unable to restore that user')
                        }
                      >
                        Restore access
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          await api.admin.revokeMemberSessions(member.principal_id)
                        }, 'Unable to end those sessions')
                      }
                    >
                      End sessions
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'departments' && (
        <div className="admin-section">
          <h2>Departments</h2>
          <p className="admin-help">
            Business units that group people and decide how governed information is scoped.
          </p>
          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault()
              const name = departmentName.trim()
              void run(async () => {
                await api.admin.createDepartment({ slug: slugify(name), name })
                setDepartmentName('')
                await refresh()
              }, 'Unable to create that department')
            }}
          >
            <label>
              Department name
              <input
                value={departmentName}
                onChange={(event) => setDepartmentName(event.target.value)}
                placeholder="Human Resources"
                required
              />
            </label>
            <button type="submit" disabled={busy || !departmentName.trim()}>
              Add department
            </button>
          </form>
          <ul className="admin-list">
            {departments.map((row) => (
              <li key={row.id}>
                <strong>{row.name}</strong>
                <span>{row.status === 'active' ? 'Active' : row.status}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tab === 'groups' && (
        <div className="admin-section">
          <h2>Groups</h2>
          <p className="admin-help">
            Named groups of people, usually inside a department, used to share governed access.
          </p>
          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault()
              const name = groupName.trim()
              void run(async () => {
                await api.admin.createGroup({
                  slug: slugify(name),
                  name,
                  department_id: groupDepartment || null,
                })
                setGroupName('')
                await refresh()
              }, 'Unable to create that group')
            }}
          >
            <label>
              Group name
              <input
                value={groupName}
                onChange={(event) => setGroupName(event.target.value)}
                placeholder="HR Team"
                required
              />
            </label>
            <label>
              Department
              <select
                value={groupDepartment}
                onChange={(event) => setGroupDepartment(event.target.value)}
              >
                <option value="">No department</option>
                {departments.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={busy || !groupName.trim()}>
              Add group
            </button>
          </form>

          <ul className="admin-list">
            {groups.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => setSelectedGroup(row.id === selectedGroup ? null : row.id)}
                >
                  {row.name}
                </button>
                <span>{row.status === 'active' ? 'Active' : row.status}</span>
              </li>
            ))}
          </ul>

          {selectedGroup && (
            <div className="admin-subsection">
              <h3>Members</h3>
              <form
                className="admin-form"
                onSubmit={(event) => {
                  event.preventDefault()
                  const principalId = String(
                    new FormData(event.currentTarget).get('principal') || '',
                  )
                  if (!principalId) return
                  void run(async () => {
                    await api.admin.addGroupMember(selectedGroup, principalId)
                    await refresh()
                  }, 'Unable to add that member')
                }}
              >
                <label>
                  Add a user to this group
                  <select name="principal" defaultValue="">
                    <option value="">Choose a user</option>
                    {members
                      .filter((member) => !member.group_ids.includes(selectedGroup))
                      .map((member) => (
                        <option key={member.principal_id} value={member.principal_id}>
                          {displayName(member)}
                        </option>
                      ))}
                  </select>
                </label>
                <button type="submit" disabled={busy}>
                  Add to group
                </button>
              </form>

              <ul className="admin-list">
                {members
                  .filter((member) => member.group_ids.includes(selectedGroup))
                  .map((member) => (
                    <li key={member.principal_id}>
                      <strong>{displayName(member)}</strong>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void run(async () => {
                            await api.admin.removeGroupMember(selectedGroup, member.principal_id)
                            await refresh()
                          }, 'Unable to remove that member')
                        }
                      >
                        Remove
                      </button>
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {tab === 'stewards' && (
        <div className="admin-section">
          <h2>Data Stewards</h2>
          <p className="admin-help">
            People trusted to manage how information is classified inside one department or group.
            A steward manages governed sources without becoming a workspace administrator.
          </p>
          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault()
              void run(async () => {
                await api.admin.grantSteward({
                  principal_id: stewardPrincipal,
                  scope_type: stewardScopeType,
                  scope_id: stewardScopeId,
                })
                await loadStewards()
              }, 'Unable to grant that stewardship')
            }}
          >
            <label>
              Person
              <select
                value={stewardPrincipal}
                onChange={(event) => setStewardPrincipal(event.target.value)}
                required
              >
                <option value="">Choose a user</option>
                {members.map((member) => (
                  <option key={member.principal_id} value={member.principal_id}>
                    {displayName(member)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Scope
              <select
                value={stewardScopeType}
                onChange={(event) => {
                  setStewardScopeType(event.target.value)
                  setStewardScopeId('')
                }}
              >
                <option value="department">Department</option>
                <option value="group">Group</option>
              </select>
            </label>
            <label>
              Which one
              <select
                value={stewardScopeId}
                onChange={(event) => setStewardScopeId(event.target.value)}
                required
              >
                <option value="">Choose</option>
                {scopeOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={busy || !stewardPrincipal || !stewardScopeId}>
              Grant stewardship
            </button>
          </form>

          <ul className="admin-list">
            {stewards.map((row) => {
              const owner =
                members.find((member) => member.principal_id === row.principal_id) || null
              const scope =
                departments.find((item) => item.id === row.scope_id)?.name ||
                groups.find((item) => item.id === row.scope_id)?.name ||
                row.scope_id
              return (
                <li key={`${row.principal_id}-${row.scope_type}-${row.scope_id}`}>
                  <strong>{owner ? displayName(owner) : row.principal_id}</strong>
                  <span>
                    {row.scope_type === 'department' ? 'Department' : 'Group'}: {scope}
                  </span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await api.admin.revokeSteward(row.principal_id, row.scope_type, row.scope_id)
                        await loadStewards()
                      }, 'Unable to revoke that stewardship')
                    }
                  >
                    Remove
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {tab === 'data-access' && (
        <div className="admin-section">
          <h2>Data Access</h2>
          <p className="admin-help">
            How each governed source and document is classified, and who it is shared with.
          </p>
          <h3>Sources</h3>
          <ul className="admin-list">
            {sources.map((row) => (
              <li key={row.id}>
                <strong>{row.name || row.id}</strong>
                <span>{row.classification || 'Unclassified'}</span>
                <span>{row.department_id ? 'Department scoped' : 'Tenant wide'}</span>
              </li>
            ))}
          </ul>
          <h3>Documents</h3>
          <ul className="admin-list">
            {documents.map((row) => (
              <li key={row.id}>
                <strong>{row.title || row.id}</strong>
                <span>{row.classification || 'Unclassified'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tab === 'usage' && (
        <div className="admin-section">
          <h2>Usage / Plan</h2>
          <p className="admin-help">
            Model usage recorded for this workspace. Plan and entitlement details are not available
            yet.
          </p>
          {usage && (
            <>
              <dl className="admin-facts">
                {Object.entries(usage.usage).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key.replace(/_/g, ' ')}</dt>
                    <dd>{String(value)}</dd>
                  </div>
                ))}
              </dl>
              <p className="admin-note">{usage.note}</p>
            </>
          )}
        </div>
      )}
    </section>
  )
}
