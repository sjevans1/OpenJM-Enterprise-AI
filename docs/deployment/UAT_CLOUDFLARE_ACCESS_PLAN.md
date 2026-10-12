# OpenJM UAT — Cloudflare Access and tester handoff plan

This plan covers public exposure of the OpenJM UAT environment: who may reach the
UAT hostname, how they authenticate, what OpenJM privileges they hold inside the
product, and what must be verified before the URL is handed over.

**Status: plan. The Cloudflare Access application, the tester identities and the
access tests described here have NOT yet been executed.** Nothing in this document
should be read as evidence that a test passed. The execution checklist below
carries the verification state.

## Two independent layers

Access to UAT is gated twice, and passing one layer grants nothing at the other.

| Layer | Question it answers | Where it is enforced |
| --- | --- | --- |
| **Cloudflare Access** | May this person's browser reach the host at all? | Cloudflare edge, before any request reaches OpenJM |
| **OpenJM / Keycloak authorization** | What may this authenticated principal do in the product? | OpenJM application and Keycloak realm |

Passing Cloudflare Access **does not** create an OpenJM account, a tenant
membership or any authority. A user who clears the edge with no OpenJM identity
reaches the login surface and no further. Conversely, an OpenJM account does not
bypass the edge.

## Cloudflare Access policy — approved testers

The Access application protecting the UAT hostname must be **deny by default**:
no rule grants access unless it names an individual approved address.

Allow **only** these individual email addresses:

| # | Approved tester address |
| --- | --- |
| 1 | `anu@crimsontide.ai` |
| 2 | `garry@crimsontide.ai` |
| 3 | `leanne@crimsontide.ai` |
| 4 | `andrew@crimsontide.ai` |
| 5 | `bradley@crimsontide.ai` |

### Explicitly prohibited

Do **not** add any domain-wide or pattern rule, including:

- `*@crimsontide.ai`
- `crimsontide.ai`
- any `*.` wildcard, regex, or "email domain" rule that would admit unlisted
  colleagues

An unlisted `@crimsontide.ai` address must be **denied**. This is a deliberate
least-privilege decision, not an oversight: UAT carries demo tenants and demo
content, and a domain-wide rule would silently widen the audience to the whole
organisation.

Also do not add rules that admit by IP range, country, or "everyone in the
organisation" group as a substitute for the five named identities.

### Rule shape

Five `allow` decisions, each matching one exact email address with an `eq`/exact
matcher (never a substring or domain matcher), plus the Access application's
default deny for everything else. Prefer an explicit deny-all fallback so the
intent survives later edits.

## OpenJM accounts and roles

Provision a matching OpenJM/Keycloak identity for each of the five testers, all in
the synthetic UAT tenant.

- **Default role for all five: the least-privileged ordinary tester / end-user
  role.** This is the baseline unless an existing requirement dictates otherwise.
- Place all five in the **synthetic UAT tenant only** (see below).

None of the five may be granted:

- OpenJM **platform-operator** capability;
- **unrestricted cross-tenant** access;
- **delegated support** capability;
- access to **production or customer data**.

If one tester needs client-admin capability for a specific UAT exercise, **stop
and identify the proposed person and the reason before granting the elevated
role** - do not assume it, and do not grant it to the group as a convenience.

### Test tenant

Tenant: **`OpenJM UAT`** (synthetic).

Populate it with synthetic/demo content only - demo Knowledge, demo Data sources
and demo reports. No production data, no customer data, no real documents.

## Pre-handover access tests

The public UAT URL is not shared with anyone until every check below passes.

### Positive (each approved tester)

1. Each of the five approved addresses can pass Cloudflare Access (identity
   prompt completes and the request reaches OpenJM).

### Negative

2. An **unlisted `@crimsontide.ai`** address is **denied** at the edge.
3. An **unrelated external** email address is **denied** at the edge.
4. **Anonymous / incognito** access with no authentication is **denied**.
5. Passing Cloudflare Access **does not by itself grant OpenJM privileges** - a
   principal that cleared the edge but has no OpenJM membership reaches the login
   surface only and is refused product access by OpenJM/Keycloak.

Checks 2-5 are the ones that actually prove the policy. A plan that only tests the
five approved addresses proves the allow side and nothing about the deny side.

### Execution checklist

| Check | State |
| --- | --- |
| Five approved addresses reach UAT | NOT RUN |
| Unlisted `@crimsontide.ai` denied | NOT RUN |
| Unrelated external address denied | NOT RUN |
| Anonymous/incognito denied | NOT RUN |
| Edge access grants no OpenJM authority by itself | NOT RUN |

## Handoff table

Report the following for each tester once provisioning and the tests have actually
run. Until then every cell reads NOT RUN rather than implying success.

| Tester | Cloudflare allow entry | Keycloak/OpenJM identity | OpenJM tenant | Role | Login smoke |
| --- | --- | --- | --- | --- | --- |
| `anu@crimsontide.ai` | NOT RUN | NOT RUN | `OpenJM UAT` | ordinary tester (default) | NOT RUN |
| `garry@crimsontide.ai` | NOT RUN | NOT RUN | `OpenJM UAT` | ordinary tester (default) | NOT RUN |
| `leanne@crimsontide.ai` | NOT RUN | NOT RUN | `OpenJM UAT` | ordinary tester (default) | NOT RUN |
| `andrew@crimsontide.ai` | NOT RUN | NOT RUN | `OpenJM UAT` | ordinary tester (default) | NOT RUN |
| `bradley@crimsontide.ai` | NOT RUN | NOT RUN | `OpenJM UAT` | ordinary tester (default) | NOT RUN |

Any elevated role, if one is ever approved, is recorded here as an explicit
deviation with the named person and the reason - never as a silent default.

## Secret handling

Temporary passwords, tokens, recovery codes and authentication secrets are never
written into this document, into the repository, into logs, or into the handoff
report. Credentials are delivered to each tester out of band. The handoff report
states that a credential was issued and how it was delivered, never its value.

## Handover rule

The public UAT URL is shared with the five approved testers only after all five
positive checks and all four negative checks above have passed. Until then the URL
is treated as unverified and is not circulated.

## Where this runs

Deployment of the UAT host, the Cloudflare Access application and the
Cloudflare Tunnel, and the creation of the Keycloak identities, are operational
steps performed against the UAT account and host. They are not exercised by the
product's own test suite and are not claimed by this document. See
`docs/CONFIGURATION.md` for OpenJM-side OIDC settings and
`docs/PORTS_STORAGE_SECRETS.md` for the surface layout.
