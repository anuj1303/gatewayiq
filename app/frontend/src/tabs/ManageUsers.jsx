import React, { useEffect, useMemo, useState } from 'react'
import { UserPlus, UserMinus, Search, Users, Mail, ShieldAlert, CheckCircle2 } from 'lucide-react'
import { fetchGroup, addMember, removeMember } from '../api'
import { initials } from '../components/Login.jsx'
import { Card } from '../components/ui.jsx'

function Avatar({ name, mgr }) {
  return <div className={`avatar avatar-sm ${mgr ? 'avatar-mgr' : ''}`}>{initials(name)}</div>
}

// Manager-only tab: create & manage your team by adding / removing people by
// email. Adds are validated against the org directory server-side — only people
// who exist in the directory can be added (unknown emails are rejected).
export default function ManageUsers({ persona, onChanged }) {
  const [group, setGroup] = useState(null)
  const [q, setQ] = useState('')
  const [emailInput, setEmailInput] = useState('')
  const [busy, setBusy] = useState(null)
  const [err, setErr] = useState(null)
  const [ok, setOk] = useState(null)

  useEffect(() => { load() }, [])
  function load() { fetchGroup().then(setGroup).catch((e) => setErr(e.message)) }

  const candidates = useMemo(() => {
    const s = q.trim().toLowerCase()
    const list = group?.candidates || []
    return s ? list.filter((p) => (p.name + ' ' + p.title + ' ' + p.email).toLowerCase().includes(s)) : list
  }, [q, group])

  async function act(fn, email, label) {
    setBusy(email); setErr(null); setOk(null)
    try {
      const g = await fn(email)
      setGroup(g)
      setOk(label)
      onChanged?.()
    } catch (e) { setErr(e.message) } finally { setBusy(null) }
  }

  async function addByEmail(e) {
    e.preventDefault()
    const email = emailInput.trim().toLowerCase()
    if (!email) return
    setBusy(email); setErr(null); setOk(null)
    try {
      const g = await addMember(email)
      setGroup(g)
      setOk(`Added ${email} to your team.`)
      setEmailInput('')
      onChanged?.()
    } catch (e2) { setErr(e2.message) } finally { setBusy(null) }
  }

  if (!persona?.can_manage) {
    return (
      <div className="page-head">
        <div className="login-sso-note">
          <ShieldAlert size={14} style={{ verticalAlign: '-2px', marginRight: 6 }} />
          Team management is available to managers only.
        </div>
      </div>
    )
  }

  const members = group?.members || []

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Manage Users</h1>
        <div className="page-desc">
          Add or remove people from your team by email. Your dashboards, forecasts and
          reports rescope to your team instantly. You can only add users who exist in your
          organization’s directory.
        </div>
      </div>

      {err && (
        <div className="login-sso-note" style={{ marginBottom: 14 }}>
          <ShieldAlert size={14} style={{ verticalAlign: '-2px', marginRight: 6 }} /> {err}
        </div>
      )}
      {ok && (
        <div className="banner-ok" style={{ marginBottom: 14 }}>
          <CheckCircle2 size={14} style={{ verticalAlign: '-2px', marginRight: 6 }} /> {ok}
        </div>
      )}

      <div className="grid cols-2">
        {/* Add by email */}
        <Card title="Add a user" hint="Enter the email address of someone in your directory.">
          <form className="add-email-row" onSubmit={addByEmail}>
            <div className="login-search" style={{ flex: 1 }}>
              <Mail size={15} className="muted" />
              <input
                className="input"
                type="email"
                placeholder="name@company.com"
                value={emailInput}
                onChange={(e) => setEmailInput(e.target.value)}
              />
            </div>
            <button className="pw-submit" type="submit" disabled={!emailInput.trim() || busy === emailInput.trim().toLowerCase()}>
              <UserPlus size={15} /> Add
            </button>
          </form>

          <div className="section-label" style={{ marginTop: 18 }}>Or pick from the directory</div>
          <div className="login-search" style={{ marginBottom: 10 }}>
            <Search size={15} className="muted" />
            <input className="input" placeholder="Search directory…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="member-list">
            {candidates.map((p) => (
              <div key={p.email} className="member-row">
                <Avatar name={p.name} />
                <div className="login-person-info">
                  <div className="login-person-name">{p.name}</div>
                  <div className="login-person-title">{p.title} · {p.email}</div>
                </div>
                <button className="btn-ghost btn-add" disabled={busy === p.email}
                  onClick={() => act(addMember, p.email, `Added ${p.name} to your team.`)}>
                  <UserPlus size={14} /> Add
                </button>
              </div>
            ))}
            {group && !candidates.length && <div className="empty">Everyone in the directory is already on your team.</div>}
            {!group && <div className="empty">Loading directory…</div>}
          </div>
        </Card>

        {/* Current members */}
        <Card title={`Your team${group ? ` · ${members.length}` : ''}`} hint="People whose AI Gateway activity you can see and manage.">
          <div className="member-list">
            {members.map((m) => (
              <div key={m.email} className="member-row">
                <Avatar name={m.name} mgr={m.role_type !== 'ic'} />
                <div className="login-person-info">
                  <div className="login-person-name">{m.name}{m.is_self && <span className="faint"> · you</span>}</div>
                  <div className="login-person-title">{m.title} · {m.email}</div>
                </div>
                {!m.is_self && (
                  <button className="btn-ghost btn-danger" disabled={busy === m.email}
                    onClick={() => act(removeMember, m.email, `Removed ${m.name} from your team.`)}>
                    <UserMinus size={14} /> Remove
                  </button>
                )}
              </div>
            ))}
            {!group && <div className="empty">Loading…</div>}
            {group && members.length <= 1 && (
              <div className="empty">Your team is empty. Add people using the panel on the left.</div>
            )}
          </div>
        </Card>
      </div>
    </>
  )
}
