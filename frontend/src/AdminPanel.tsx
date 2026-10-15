/**
 * BV3-B client administration surface.
 *
 * Business-oriented by design: Users, Departments, Groups, Data Stewards,
 * Data Access, Usage / Plan. It deliberately exposes no operation ids, scheduler
 * internals, provider credentials or raw platform capabilities, and the backend
 * remains the authority for every action it offers.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Building2,
  Database,
  Gauge,
  History,
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
  type AdminAuditEntry,
} from './api'

import { useConfirmation } from './ConfirmAction'
import PolicyEditor from './PolicyEditor'

type AdminTab =
  | 'users'
  | 'departments'
  | 'groups'
  | 'stewards'
  | 'data-access'
  | 'usage'
  | 'audit'

const TABS: { id: AdminTab; label: string; icon: typeof Users }[] = [
  { id: 'users', label: 'Users', icon: Users },
  { id: 'departments', label: 'Departments', icon: Building2 },
  { id: 'groups', label: 'Groups', icon: UsersRound },
  { id: 'stewards', label: 'Data Stewards', icon: ShieldCheck },
  { id: 'data-access', label: 'Data Access', icon: Database },
  { id: 'usage', label: 'Usage / Plan', icon: Gauge },
  { id: 'audit', label: 'Activity', icon: History },
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

export default function AdminPanel({
  principalId,
  actorRole = 'admin',
  onDirtyChange,
}: {
  principalId?: string
  actorRole?: string
  onDirtyChange?: (dirty: boolean) => void
}) {
  const confirmation = useConfirmation()
  const [roleDrafts, setRoleDrafts] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [audit, setAudit] = useState<AdminAuditEntry[]>([])
  const [policyDirty, setPolicyDirty] = useState(false)
  const dirty = policyDirty || Object.keys(roleDrafts).length > 0
  useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  const [search, setSearch] = useState('')
  const inFlight = useRef(false)
  const [tab, setTab] = useState<AdminTab>('users')
  const [members, setMembers] = useState<AdminMember[]>([])
  const [departments, setDepartments] = useState<AdminDepartment[]>([])
  const [groups, setGroups] = useState<AdminGroup[]>([])
  const [stewards, setStewards] = useState<AdminSteward[]>([])
  const [usage, setUsage] = useState<AdminUsageSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [inviteSubject, setInviteSubject] = useState('')
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('viewer')
  const [departmentName, setDepartmentName] = useState('')
  const [groupName, setGroupName] = useState('')
  const [groupDepartment, setGroupDepartment] = useState('')
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null)
  const [stewardPrincipal, setStewardPrincipal] = useState('')
  const [stewardScopeType, setStewardScopeType] = useState('department')
  const [stewardScopeId, setStewardScopeId] = useState('')

  const refresh = useCallback(async () => {
    setError(null)
    setLoading(true)
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
      setError(
        errorText(
          caught,
          'Unable to refresh administration data. Use Refresh to try again.',
        ),
      )
    } finally {
      setLoading(false)
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
    if (tab === 'audit') {
      setError(null)
      void api.admin
        .audit()
        .then((result) => setAudit(result.entries))
        .catch((caught) =>
          setError(errorText(caught, 'Unable to load activity')),
        )
    }
    if (tab === 'usage') void loadUsage()
  }, [tab, loadStewards, loadUsage])

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
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await action()
      setNotice('Changes saved.')
    } catch (caught) {
      setError(errorText(caught, fallback))
    } finally {
      setBusy(false)
      inFlight.current = false
    }
  }

  function review(
    title: string,
    description: string,
    label: string,
    action: () => Promise<void>,
  ) {
    setNotice(null)
    setError(null)
    confirmation.request({
      title,
      description,
      confirmLabel: label,
      onConfirm: async () => {
        await action()
        setNotice(`${label}: completed.`)
      },
    })
  }
  const locked = busy || loading || confirmation.isOpen
  const activeMembers = members.filter((member) => member.status === 'active')
  const visibleMembers = members.filter((member) =>
    `${displayName(member)} ${member.role} ${member.status}`
      .toLowerCase()
      .includes(search.trim().toLowerCase()),
  )

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
        <button type="button" disabled={locked} onClick={() => void refresh()}>
          Refresh users and groups
        </button>
      </header>

      <nav className="admin-tabs" aria-label="Administration sections">
        {TABS.map((item) => (
          <button
            key={item.id}
            className={tab === item.id ? 'nav-item active' : 'nav-item'}
            aria-current={tab === item.id ? 'page' : undefined}
            disabled={busy || confirmation.isOpen || (item.id !== tab && dirty)}
            onClick={() => {
              setTab(item.id)
              setNotice(null)
              setError(null)
            }}
          >
            <item.icon size={16} />
            {item.label}
          </button>
        ))}
      </nav>

      {loading && (
        <p className="admin-help" role="status">
          Loading administration…
        </p>
      )}
      {notice && (
        <p className="admin-success" role="status">
          {notice}
        </p>
      )}
      {dirty && (
        <p className="admin-note">
          You have unsaved administration changes. Save or cancel them before
          changing sections.
        </p>
      )}
      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      {tab === 'users' && (
        <div className="admin-section">
          <h2>Users</h2>
          <p className="admin-help">
            People who can sign in to this workspace, and the role that decides
            what they can do. Select a role, then save and confirm the change.
            Group membership determines department access.
          </p>

          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault()
              const input = {
                subject: inviteSubject.trim(),
                role: inviteRole,
                email: inviteEmail.trim() || null,
              }
              review(
                'Add workspace user?',
                `Grant ${ROLE_LABELS[inviteRole]} access to ${input.email || input.subject}. This adds an existing sign-in identity to this workspace; it does not send an invitation or create a password.`,
                'Confirm add user',
                async () => {
                  await api.admin.provisionMember(input)
                  setInviteSubject('')
                  setInviteEmail('')
                  await refresh()
                },
              )
            }}
          >
            <fieldset disabled={locked} className="admin-form-fields">
              <label>
                Sign-in subject
                <input
                  value={inviteSubject}
                  onChange={(event) => setInviteSubject(event.target.value)}
                  placeholder="Identity provider user ID"
                  aria-describedby="identity-help"
                  required
                />
              </label>
              <p className="admin-help" id="identity-help">
                Use the exact subject from your identity provider. An email
                address alone may not identify an SSO account.
              </p>
              <label>
                Contact email (optional)
                <input
                  type="email"
                  value={inviteEmail}
                  onChange={(event) => setInviteEmail(event.target.value)}
                  placeholder="person@example.com"
                />
              </label>
              <label>
                Role
                <select
                  value={inviteRole}
                  onChange={(event) => setInviteRole(event.target.value)}
                >
                  {Object.entries(ROLE_LABELS)
                    .filter(([value]) => value !== 'owner')
                    .map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                </select>
              </label>
              <button type="submit" disabled={locked || !inviteSubject.trim()}>
                <UserPlus size={15} />
                Add user
              </button>
            </fieldset>
          </form>

          <label className="admin-search">
            Search users
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Name, role or status"
            />
          </label>
          <div className="admin-table-scroll">
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
                {visibleMembers.map((member) => (
                  <tr key={member.principal_id}>
                    <td>{displayName(member)}</td>
                    <td>
                      <select
                        aria-label={`Role for ${displayName(member)}`}
                        value={roleDrafts[member.principal_id] ?? member.role}
                        disabled={
                          locked ||
                          member.status !== 'active' ||
                          member.principal_id === principalId
                        }
                        onChange={(event) => {
                          const role = event.target.value
                          setRoleDrafts((current) => {
                            const next = { ...current }
                            if (role === member.role)
                              delete next[member.principal_id]
                            else next[member.principal_id] = role
                            return next
                          })
                        }}
                      >
                        {Object.entries(ROLE_LABELS).map(([value, label]) => (
                          <option
                            key={value}
                            value={value}
                            disabled={
                              value === 'owner' && actorRole !== 'owner'
                            }
                          >
                            {label}
                          </option>
                        ))}
                      </select>
                      {member.principal_id === principalId && (
                        <small>Your account</small>
                      )}
                      {roleDrafts[member.principal_id] && (
                        <div className="admin-actions role-actions">
                          <button
                            type="button"
                            disabled={locked}
                            onClick={() => {
                              const role = roleDrafts[member.principal_id]
                              review(
                                'Change user role?',
                                `${displayName(member)}: ${ROLE_LABELS[member.role]} → ${ROLE_LABELS[role]}. This changes what this user can do in the workspace.`,
                                'Confirm role change',
                                async () => {
                                  const updated =
                                    await api.admin.changeMemberRole(
                                      member.principal_id,
                                      role,
                                    )
                                  setMembers((current) =>
                                    current.map((row) =>
                                      row.principal_id === member.principal_id
                                        ? updated
                                        : row,
                                    ),
                                  )
                                  setRoleDrafts((current) => {
                                    const next = { ...current }
                                    delete next[member.principal_id]
                                    return next
                                  })
                                },
                              )
                            }}
                          >
                            Save role
                          </button>
                          <button
                            type="button"
                            disabled={locked}
                            onClick={() =>
                              setRoleDrafts((current) => {
                                const next = { ...current }
                                delete next[member.principal_id]
                                return next
                              })
                            }
                          >
                            Cancel role change
                          </button>
                        </div>
                      )}
                    </td>
                    <td>{member.status === 'active' ? 'Active' : 'Revoked'}</td>
                    <td>
                      {member.group_ids
                        .map(
                          (id) =>
                            groups.find((group) => group.id === id)?.name ||
                            'Unavailable group',
                        )
                        .join(', ') || 'No groups assigned'}
                    </td>
                    <td className="admin-actions">
                      {member.status === 'active' ? (
                        <button
                          type="button"
                          disabled={
                            locked || member.principal_id === principalId
                          }
                          onClick={() =>
                            review(
                              'Revoke workspace access?',
                              `${displayName(member)} will lose access to this workspace. Their account and content will be retained.`,
                              'Confirm revoke access',
                              async () => {
                                await api.admin.revokeMember(
                                  member.principal_id,
                                )
                                await refresh()
                              },
                            )
                          }
                        >
                          Revoke access
                        </button>
                      ) : (
                        <button
                          type="button"
                          disabled={locked}
                          onClick={() =>
                            review(
                              'Restore workspace access?',
                              `${displayName(member)} will regain their ${ROLE_LABELS[member.role]} role and any still-active group grants.`,
                              'Confirm restore access',
                              async () => {
                                await api.admin.reactivateMember(
                                  member.principal_id,
                                )
                                await refresh()
                              },
                            )
                          }
                        >
                          Restore access
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={locked}
                        onClick={() =>
                          review(
                            'End user sessions?',
                            `${displayName(member)} will need to sign in again. Their workspace role will stay the same.`,
                            'Confirm end sessions',
                            async () => {
                              await api.admin.revokeMemberSessions(
                                member.principal_id,
                              )
                            },
                          )
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
          {!loading && visibleMembers.length === 0 && (
            <p className="admin-help">
              {members.length
                ? 'No users match your search.'
                : 'No workspace users found.'}
            </p>
          )}
          <p className="admin-help">
            Viewer: ask questions and read reports. Editor: also manage their
            sources and reports. Administrator: manage workspace users and
            governance. Owner: full workspace administration.
          </p>
        </div>
      )}

      {tab === 'departments' && (
        <div className="admin-section">
          <h2>Departments</h2>
          <p className="admin-help">
            Business units that group people and decide how governed information
            is scoped. To give a person department access, create a group in
            that department and add them on the Groups tab.
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
            <fieldset disabled={locked} className="admin-form-fields">
              <label>
                Department name
                <input
                  value={departmentName}
                  onChange={(event) => setDepartmentName(event.target.value)}
                  placeholder="Human Resources"
                  required
                />
              </label>
              <button type="submit" disabled={locked || !departmentName.trim()}>
                Add department
              </button>
            </fieldset>
          </form>
          {!loading && !departments.length && (
            <p className="admin-help">
              No departments yet. Add your first business unit above, then
              create its access groups.
            </p>
          )}
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
            Named groups of people, usually inside a department, used to share
            governed access.
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
            <fieldset disabled={locked} className="admin-form-fields">
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
              <button type="submit" disabled={locked || !groupName.trim()}>
                Add group
              </button>
            </fieldset>
          </form>

          {!loading && !groups.length && (
            <p className="admin-help">
              No groups yet. Create a team, choose its department, then add
              users.
            </p>
          )}
          <ul className="admin-list">
            {groups.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() =>
                    setSelectedGroup(row.id === selectedGroup ? null : row.id)
                  }
                >
                  {row.name}
                </button>
                <span>{row.status === 'active' ? 'Active' : row.status}</span>
              </li>
            ))}
          </ul>

          {selectedGroup && (
            <div className="admin-subsection">
              <h3>
                Members of{' '}
                {groups.find((group) => group.id === selectedGroup)?.name}
              </h3>
              <form
                className="admin-form"
                onSubmit={(event) => {
                  event.preventDefault()
                  const principalId = String(
                    new FormData(event.currentTarget).get('principal') || '',
                  )
                  if (!principalId) return
                  review(
                    'Add group membership?',
                    `Add ${displayName(members.find((member) => member.principal_id === principalId)!)} to ${groups.find((group) => group.id === selectedGroup)?.name}. This may grant access to documents and data shared with this group or department.`,
                    'Confirm add to group',
                    async () => {
                      await api.admin.addGroupMember(selectedGroup, principalId)
                      await refresh()
                    },
                  )
                }}
              >
                <fieldset disabled={locked} className="admin-form-fields">
                  <label>
                    Add a user to this group
                    <select name="principal" defaultValue="" required>
                      <option value="">Choose a user</option>
                      {members
                        .filter(
                          (member) =>
                            member.status === 'active' &&
                            !member.group_ids.includes(selectedGroup),
                        )
                        .map((member) => (
                          <option
                            key={member.principal_id}
                            value={member.principal_id}
                          >
                            {displayName(member)}
                          </option>
                        ))}
                    </select>
                  </label>
                  <button type="submit" disabled={locked}>
                    Add to group
                  </button>
                </fieldset>
              </form>

              <ul className="admin-list">
                {members
                  .filter((member) => member.group_ids.includes(selectedGroup))
                  .map((member) => (
                    <li key={member.principal_id}>
                      <strong>{displayName(member)}</strong>
                      <button
                        type="button"
                        disabled={locked}
                        onClick={() =>
                          review(
                            'Remove group membership?',
                            `Remove ${displayName(member)} from this group. Access provided by this group or department may end.`,
                            'Confirm remove member',
                            async () => {
                              await api.admin.removeGroupMember(
                                selectedGroup,
                                member.principal_id,
                              )
                              await refresh()
                            },
                          )
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
            People trusted to manage how information is classified inside one
            department or group. A steward manages governed sources without
            becoming a workspace administrator.
          </p>
          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault()
              review(
                'Grant stewardship?',
                `Allow ${displayName(members.find((member) => member.principal_id === stewardPrincipal)!)} to manage classification and sharing for ${scopeOptions.find((option) => option.id === stewardScopeId)?.label}.`,
                'Confirm grant stewardship',
                async () => {
                  await api.admin.grantSteward({
                    principal_id: stewardPrincipal,
                    scope_type: stewardScopeType,
                    scope_id: stewardScopeId,
                  })
                  await loadStewards()
                },
              )
            }}
          >
            <fieldset disabled={locked} className="admin-form-fields">
              <label>
                Person
                <select
                  value={stewardPrincipal}
                  onChange={(event) => setStewardPrincipal(event.target.value)}
                  required
                >
                  <option value="">Choose a user</option>
                  {activeMembers.map((member) => (
                    <option
                      key={member.principal_id}
                      value={member.principal_id}
                    >
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
              <button
                type="submit"
                disabled={locked || !stewardPrincipal || !stewardScopeId}
              >
                Grant stewardship
              </button>
            </fieldset>
          </form>

          <ul className="admin-list">
            {stewards
              .filter((row) => row.status === 'active')
              .map((row) => {
                const owner =
                  members.find(
                    (member) => member.principal_id === row.principal_id,
                  ) || null
                const scope =
                  departments.find((item) => item.id === row.scope_id)?.name ||
                  groups.find((item) => item.id === row.scope_id)?.name ||
                  row.scope_id
                return (
                  <li
                    key={`${row.principal_id}-${row.scope_type}-${row.scope_id}`}
                  >
                    <strong>
                      {owner ? displayName(owner) : row.principal_id}
                    </strong>
                    <span>
                      {row.scope_type === 'department' ? 'Department' : 'Group'}
                      : {scope}
                    </span>
                    <button
                      type="button"
                      disabled={locked}
                      onClick={() =>
                        review(
                          'Remove stewardship?',
                          `${owner ? displayName(owner) : 'This person'} will no longer manage policies for ${scope} through this grant. Other access stays governed by their role and groups.`,
                          'Confirm remove stewardship',
                          async () => {
                            await api.admin.revokeSteward(
                              row.principal_id,
                              row.scope_type,
                              row.scope_id,
                            )
                            await loadStewards()
                          },
                        )
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
        <PolicyEditor
          departments={departments}
          groups={groups}
          onDirtyChange={setPolicyDirty}
        />
      )}

      {tab === 'usage' && (
        <div className="admin-section">
          <h2>Usage / Plan</h2>
          <p className="admin-help">
            Model usage recorded for this workspace, together with the
            configured plan and allowances.
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
              <div className="admin-subsection">
                <h3>Plan</h3>
                <p>
                  {usage.plan?.plan_name ||
                    'No plan configured for this workspace.'}
                </p>
                {usage.entitlements && (
                  <dl className="admin-facts">
                    <div>
                      <dt>Subscription</dt>
                      <dd>
                        {usage.entitlements.subscription_status ||
                          'Not configured'}
                      </dd>
                    </div>
                    <div>
                      <dt>Available credits</dt>
                      <dd>
                        {usage.entitlements.allowance?.available_units ??
                          'Not reported'}
                      </dd>
                    </div>
                  </dl>
                )}
              </div>
            </>
          )}
        </div>
      )}
      {tab === 'audit' && (
        <div className="admin-section">
          <h2>Recent activity</h2>
          <p className="admin-help">
            The latest 50 workspace governance events.
          </p>
          <button
            type="button"
            onClick={() =>
              void api.admin
                .audit()
                .then((result) => setAudit(result.entries))
                .catch((caught) =>
                  setError(errorText(caught, 'Unable to load activity')),
                )
            }
          >
            Refresh activity
          </button>
          <ul className="admin-list">
            {audit.map((entry, index) => (
              <li key={index}>
                <strong>{entry.action.replace(/[._]/g, ' ')}</strong>
                <span>{entry.decision}</span>
                <time>
                  {entry.created_at
                    ? new Date(entry.created_at).toLocaleString()
                    : 'Time unavailable'}
                </time>
              </li>
            ))}
          </ul>
          {!audit.length && (
            <p className="admin-help">No activity to display.</p>
          )}
        </div>
      )}
      {confirmation.dialog}
    </section>
  )
}
