/**
 * Админка: какие комнаты есть, сколько занимают и что можно убрать.
 *
 * Доступ по отдельному токену (заголовок X-Admin-Token): он вводится один раз и живёт в
 * localStorage этой вкладки. Токен не участвует ни в чём другом — это не токен участника.
 *
 * Задача страницы прикладная: увидеть, из чего состоит объём комнаты, и освободить место —
 * по категориям (видео, дорожки и нарезки, записи участников, готовые сборки) или удалить
 * комнату целиком.
 */

import { useCallback, useEffect, useState } from 'react'

import { api } from '../api/client'
import type { AdminOrphan, AdminOverview, AdminRoom, AdminRoomDetail, PurgeScope } from '../types'

const TOKEN_KEY = 'funyduby:admin-token'

const CATEGORY_LABEL: Record<string, string> = {
  original: 'видео',
  artifacts: 'дорожки и нарезки',
  recordings: 'записи участников',
  rendered: 'сборки',
  other: 'прочее',
}

const SCOPE_HINT: Record<PurgeScope, string> = {
  rendered: 'Уберёт готовые сборки. Историю сборок оставит, скачать их будет нельзя.',
  recordings: 'Уберёт записи участников: реплики снова станут неозвученными.',
  artifacts: 'Уберёт дорожки конвейера и разделение речи. Для новой сборки нужно будет прогнать обработку заново.',
  original: 'Уберёт исходное видео вместе с разметкой, записями и джобом — комната вернётся в состояние «загрузите видео».',
}

const STATUS_LABEL: Record<string, string> = {
  CREATED: 'создана',
  PROCESSING: 'обработка',
  READY: 'готова',
  FAILED: 'ошибка',
  DELETING: 'удаляется',
  EXPIRED: 'истекла',
}

function humanSize(bytes: number | null | undefined): string {
  if (!bytes) return '0'
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} МБ`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} ГБ`
}

function humanTime(ms: number | null | undefined): string {
  if (!ms) return '—'
  const total = Math.round(ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

function breakdown(categories: Record<string, number>): string {
  return Object.entries(categories)
    .filter(([, size]) => size > 0)
    .map(([name, size]) => `${CATEGORY_LABEL[name] ?? name}: ${humanSize(size)}`)
    .join(' · ')
}

export function AdminPage() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? '')
  const [tokenInput, setTokenInput] = useState('')
  const [data, setData] = useState<AdminOverview | null>(null)
  const [openRoom, setOpenRoom] = useState<AdminRoomDetail | null>(null)
  const [orphans, setOrphans] = useState<AdminOrphan[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null)

  const notify = useCallback((text: string, ok = false) => {
    setMessage({ text, ok })
    window.setTimeout(() => setMessage(null), 5000)
  }, [])

  const load = useCallback(
    async (adminToken: string) => {
      setBusy(true)
      try {
        setData(await api.adminOverview(adminToken))
      } catch (error) {
        const text = error instanceof Error ? error.message : 'Не удалось получить данные'
        notify(text, false)
        if (token && text.toLowerCase().includes('токен')) {
          localStorage.removeItem(TOKEN_KEY)
          setToken('')
        }
      } finally {
        setBusy(false)
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [notify],
  )

  useEffect(() => {
    if (token) void load(token)
  }, [token, load])

  const purge = async (roomId: string, scopes: PurgeScope[]) => {
    if (!token) return
    const labels = scopes.map((scope) => CATEGORY_LABEL[scope] ?? scope).join(', ')
    if (!window.confirm(`Убрать: ${labels}?\n\nФайлы удаляются с диска безвозвратно.`)) return
    setBusy(true)
    try {
      const result = await api.adminPurge(token, roomId, scopes)
      notify(`Освобождено ${humanSize(result.freed_bytes)}`, true)
      await load(token)
      if (openRoom?.id === roomId) setOpenRoom(await api.adminRoom(token, roomId))
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Не удалось убрать файлы', false)
    } finally {
      setBusy(false)
    }
  }

  const loadOrphans = async (adminToken: string) => {
    try {
      const result = await api.adminOrphans(adminToken)
      setOrphans(result.orphans)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Не удалось получить список файлов', false)
    }
  }

  const purgeOrphans = async (ids?: string[]) => {
    if (!token) return
    const what = ids ? `${ids.length} каталогов` : 'все каталоги без комнат'
    if (!window.confirm(`Убрать ${what}?

Это файлы, чьи комнаты уже удалены: удаляются с диска безвозвратно.`)) return
    setBusy(true)
    try {
      const result = await api.adminPurgeOrphans(token, ids)
      notify(`Убрано ${result.removed.length} каталогов, освобождено ${humanSize(result.freed_bytes)}`, true)
      await load(token)
      await loadOrphans(token)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Не удалось убрать файлы', false)
    } finally {
      setBusy(false)
    }
  }

  const removeRoom = async (room: AdminRoom) => {
    if (!token) return
    if (!window.confirm(`Удалить комнату «${room.title}» (${room.id}) вместе со всеми файлами?`)) return
    setBusy(true)
    try {
      await api.adminDeleteRoom(token, room.id)
      notify(`Комната ${room.id} удалена`, true)
      setOpenRoom(null)
      await load(token)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Не удалось удалить комнату', false)
    } finally {
      setBusy(false)
    }
  }

  if (!token) {
    return (
      <div className="home">
        <div className="brand">FUNYDUBY</div>
        <h1>Админка</h1>
        <form
          className="card"
          onSubmit={(event) => {
            event.preventDefault()
            const value = tokenInput.trim()
            if (!value) return
            localStorage.setItem(TOKEN_KEY, value)
            setToken(value)
          }}
        >
          <div className="field">
            <label htmlFor="admin-token">Админ-токен</label>
            <input
              id="admin-token"
              value={tokenInput}
              onChange={(event) => setTokenInput(event.target.value)}
              placeholder="значение ADMIN_TOKEN из .env на сервере"
            />
          </div>
          <button className="primary" type="submit">
            Войти
          </button>
        </form>
        {message && <div className={`toast${message.ok ? ' ok' : ''}`}>{message.text}</div>}
      </div>
    )
  }

  const rooms = data?.rooms ?? []

  return (
    <div className="app">
      <div className="topbar">
        <span className="brand">FUNYDUBY</span>
        <span className="title">Админка</span>
        {data && (
          <span className="badge">
            комнат: {data.totals.rooms} · занято: {humanSize(data.totals.size_bytes)} · свободно:{' '}
            {humanSize(data.disk_free_bytes)}
          </span>
        )}
        <button
          className="ghost"
          onClick={() => {
            localStorage.removeItem(TOKEN_KEY)
            setToken('')
            setData(null)
            setOpenRoom(null)
          }}
        >
          Выйти
        </button>
      </div>

      <div className="panel-body">
        {data && data.orphans.count > 0 && (
          <div className="card" style={{ marginBottom: 8, borderColor: 'var(--accent)' }}>
            <div className="row">
              <b>Файлы без комнат</b>
              <span className="muted small">
                {data.orphans.count} каталогов на {humanSize(data.orphans.size_bytes)} — следы
                неудачной уборки
              </span>
            </div>
            <p className="small muted">
              Строки этих комнат уже удалены, поэтому штатными средствами файлы не найти: только
              сверкой диска с базой. Здесь их можно убрать.
            </p>
            <div className="actions">
              <button className="ghost" onClick={() => void loadOrphans(token)} disabled={busy}>
                {orphans ? 'Свернуть состав' : 'Показать состав'}
              </button>
              <button className="ghost" onClick={() => void purgeOrphans()} disabled={busy}>
                Убрать всё
              </button>
            </div>
            {orphans && (
              <ul className="plain small">
                {orphans.map((item) => (
                  <li key={item.id}>
                    <span className="muted">{item.id}</span> · файлов {item.files} ·{' '}
                    {humanSize(item.size_bytes)} ·{' '}
                    {new Date(item.modified_at).toLocaleString('ru-RU')} ·{' '}
                    <button className="ghost" onClick={() => void purgeOrphans([item.id])} disabled={busy}>
                      Убрать
                    </button>
                  </li>
                ))}
                {orphans.length === 0 && <li className="muted">список пуст</li>}
              </ul>
            )}
          </div>
        )}

        <div className="row small">
          <b>Комнаты</b>
          <button className="ghost" onClick={() => void load(token)} disabled={busy}>
            Обновить
          </button>
        </div>

        {rooms.length === 0 && <p className="muted">Комнат пока нет.</p>}

        {rooms.map((room) => (
          <div className="card" key={room.id} style={{ marginBottom: 8 }}>
            <div className="row">
              <b>{room.title}</b>
              <span className="badge">{room.id}</span>
              <span className="muted small">
                {STATUS_LABEL[room.status] ?? room.status} · {humanSize(room.size_bytes)}
              </span>
            </div>
            <p className="small muted">{breakdown(room.categories) || 'файлов нет'}</p>
            {room.video && (
              <p className="small muted">
                видео: {humanTime(room.video.duration_ms)} · {room.video.container} ·{' '}
                {room.video.width}×{room.video.height} · {humanSize(room.video.size_bytes)}
              </p>
            )}
            <div className="actions">
              <button
                className="ghost"
                onClick={async () => {
                  if (openRoom?.id === room.id) {
                    setOpenRoom(null)
                    return
                  }
                  try {
                    setOpenRoom(await api.adminRoom(token, room.id))
                  } catch (error) {
                    notify(error instanceof Error ? error.message : 'Не удалось открыть', false)
                  }
                }}
              >
                {openRoom?.id === room.id ? 'Свернуть' : 'Подробнее'}
              </button>
              {(['rendered', 'recordings', 'artifacts', 'original'] as PurgeScope[]).map((scope) => (
                <button
                  key={scope}
                  className="ghost"
                  disabled={busy || (room.categories[scope] ?? 0) === 0}
                  title={SCOPE_HINT[scope]}
                  onClick={() => void purge(room.id, [scope])}
                >
                  Убрать {CATEGORY_LABEL[scope]}
                </button>
              ))}
              <button className="ghost" disabled={busy} onClick={() => void removeRoom(room)}>
                Удалить комнату
              </button>
            </div>

            {openRoom?.id === room.id && (
              <div className="small" style={{ marginTop: 8 }}>
                {Object.entries(openRoom.listing ?? {}).map(([category, items]) =>
                  items.length === 0 ? null : (
                    <details key={category} open={category !== 'other'}>
                      <summary>
                        {CATEGORY_LABEL[category] ?? category}: файлов {openRoom.files[category]} на{' '}
                        {humanSize(openRoom.categories[category])}
                      </summary>
                      <ul className="plain">
                        {items.map((item) => (
                          <li key={item.path}>
                            <span className="muted">{item.path}</span> · {humanSize(item.size_bytes)} ·{' '}
                            {new Date(item.modified_at).toLocaleString('ru-RU')}
                          </li>
                        ))}
                      </ul>
                    </details>
                  ),
                )}
                {openRoom.has_lines && (
                  <p className="muted">
                    В комнате есть разметка реплик — уборка дорожек сотрёт нарезки, но текст и таймкоды
                    останутся.
                  </p>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {message && <div className={`toast${message.ok ? ' ok' : ''}`}>{message.text}</div>}
    </div>
  )
}
