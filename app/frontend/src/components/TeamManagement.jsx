import React, { useEffect, useMemo, useState } from 'react'
import { X, UserPlus, UserMinus, Search, Users } from 'lucide-react'
import { fetchGroup, addMember, removeMember } from '../api'
import { initials } from './Login.jsx'

function Avatar({ name, mgr }) {
  return <div className={`avatar avatar-sm ${mgr ? 'avatar-mgr' : ''}`}>{initials(name)}</div>
}

export default function TeamManagement({ onClose, onChanged }) {
  const [group, setGroup] = useState(null)
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => { fetchGroup().then(setGroup).catch((e) => setErr(e.message)) }, [])

  const candidates = useMemo(() => {
    const s = q.trim().toLowerCase()
    const list = group?.candidates || []
    return s ? list.filter((p) => (p.name + ' ' + p.title).toLowerCase().includes(s)) : list
  }, [q, group])

  async function act(fn, email) {
    setBusy(email); setErr(null)
    try {
      const g = await fn(email)
      setGroup(g)
      onChanged?.()
    } catch (e) { setErr(e.message) } finally { setBusy(null) }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal fade-in" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Users size={18} className="muted" />
            <div>
              <div className="modal-title">Manage your team</div>
              <div className="card-hint">Add or remove people — your dashboard rescopes instantly.</div>
            </div>
          </div>
          <button className="icon-btn" onClick={onClose}><X size={18} /></button>
        </div>

        {err && <div className="login-sso-note" style={{ margin: '0 20px' }}>⚠️ {err}</div>}

        <div className="modal-body">
          <div className="modal-col">
            <div className="section-label">Current members{group ? ` · ${group.members.length}` : ''}</div>
            <div className="member-list">
              {(group?.members || []).map((m) => (
                <div key={m.email} className="member-row">
                  <Avatar name={m.name} mgr={m.role_type !== 'ic'} />
                  <div className="login-person-info">
                    <div className="login-person-name">{m.name}{m.is_self && <span className="faint"> · you</span>}</div>
                    <div className="login-person-title">{m.title}</div>
                  </div>
                  {!m.is_self && (
                    <button className="btn-ghost btn-danger" disabled={busy === m.email}
                      onClick={() => act(removeMember, m.email)}>
                      <UserMinus size={14} /> Remove
                    </button>
                  )}
                </div>
              ))}
              {!group && <div className="empty">Loading…</div>}
            </div>
          </div>

          <div className="modal-col">
            <div className="section-label">Add people</div>
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
                    <div className="login-person-title">{p.title}</div>
                  </div>
                  <button className="btn-ghost btn-add" disabled={busy === p.email}
                    onClick={() => act(addMember, p.email)}>
                    <UserPlus size={14} /> Add
                  </button>
                </div>
              ))}
              {group && !candidates.length && <div className="empty">Everyone’s already on your team.</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
