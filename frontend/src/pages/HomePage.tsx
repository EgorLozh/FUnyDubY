/** Домашняя страница: создать комнату и загрузить видео, либо войти в существующую. */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api, session } from '../api/client'
import type { ApiError } from '../types'

export function HomePage() {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [name, setName] = useState(session.name('') ?? '')
  const [joinId, setJoinId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function createRoom(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const created = await api.createRoom(title.trim() || 'Без названия', name.trim())
      session.save(created.room.id, created.token, created.participant_id, name.trim())
      navigate(`/room/${created.room.id}`)
    } catch (exc) {
      setError((exc as ApiError).hint)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="home">
      <div className="brand">FUNYDUBY</div>
      <h1>Совместная озвучка видео</h1>
      <p className="muted">
        Загрузите ролик — система сама найдёт реплики и говорящих. Дальше каждый берёт свои
        реплики и записывает голос с микрофона прямо в браузере.
      </p>

      <form className="card" onSubmit={createRoom}>
        <div className="field">
          <label htmlFor="name">Как вас зовут (увидят остальные)</label>
          <input
            id="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Егор"
            maxLength={40}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="title">Название комнаты</label>
          <input
            id="title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Ocean's Eleven, сцена 3"
            maxLength={120}
          />
        </div>
        <button className="primary" type="submit" disabled={busy || name.trim().length === 0}>
          Создать комнату
        </button>
        {error && <p className="small" style={{ color: 'var(--accent)' }}>{error}</p>}
      </form>

      <form
        className="card"
        onSubmit={(event) => {
          event.preventDefault()
          const id = joinId.trim()
          if (id) navigate(`/room/${id}`)
        }}
      >
        <div className="field">
          <label htmlFor="join">Войти в существующую комнату</label>
          <input
            id="join"
            value={joinId}
            onChange={(event) => setJoinId(event.target.value)}
            placeholder="код комнаты из ссылки"
            maxLength={20}
          />
        </div>
        <button type="submit" disabled={joinId.trim().length < 4}>
          Войти
        </button>
      </form>
    </div>
  )
}
