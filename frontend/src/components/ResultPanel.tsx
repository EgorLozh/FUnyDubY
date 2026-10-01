/**
 * Вкладка «Результат»: собрать финальное видео из записей участников и скачать его.
 *
 * Сборка идёт на сервере (микс -> кодирование), поэтому показываем этап, а не абстрактный
 * процент: «идёт микширование», «собираю файл». Прогресс приходит и по SSE, и опросом —
 * если поток событий оборвётся, интерфейс всё равно обновится.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, media } from '../api/client'
import type { Render, Room } from '../types'

const STAGE_LABEL: Record<string, string> = {
  QUEUED: 'в очереди',
  MIXING: 'микширую дорожки',
  ENCODING: 'собираю файл',
  DONE: 'готово',
  FAILED: 'ошибка сборки',
  CANCELED: 'отменено',
}

const ACTIVE = ['QUEUED', 'MIXING', 'ENCODING']

function humanSize(bytes: number | null): string {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
}

function humanTime(ms: number | null): string {
  if (!ms) return '—'
  const total = Math.round(ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

export function ResultPanel({
  room,
  events,
  notify,
  onChanged,
}: {
  room: Room
  events: unknown[]
  notify: (text: string, ok?: boolean) => void
  onChanged: () => void
}) {
  const [renders, setRenders] = useState<Render[]>([])
  const [unrecorded, setUnrecorded] = useState<'silent' | 'original'>('silent')
  const [busy, setBusy] = useState(false)
  const lastEventCount = useRef(-1)

  const load = useCallback(async () => {
    try {
      setRenders(await api.listRenders(room.id))
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Не удалось получить сборки', false)
    }
  }, [room.id, notify])

  useEffect(() => {
    void load()
  }, [load])

  // Новые события комнаты (в том числе render.updated) — повод перечитать список
  useEffect(() => {
    if (events.length !== lastEventCount.current) {
      lastEventCount.current = events.length
      void load()
    }
  }, [events, load])

  const active = useMemo(() => renders.find((r) => ACTIVE.includes(r.status)) ?? null, [renders])
  const current = useMemo(
    () => renders.find((r) => r.is_current && r.status === 'DONE') ?? null,
    [renders],
  )
  const recorded = room.counters.recorded_lines
  const total = room.counters.lines

  // Пока сборка идёт — подстраховываемся опросом (SSE может отвалиться)
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => void load(), 3000)
    return () => window.clearInterval(timer)
  }, [active, load])

  const start = async () => {
    setBusy(true)
    try {
      const render = await api.startRender(room.id, { unrecorded })
      notify(
        ACTIVE.includes(render.status) ? 'Сборка запущена' : 'Сборка уже готова — отдаю результат',
      )
      await load()
      onChanged()
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Не удалось запустить сборку', false)
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!active) return
    try {
      await api.cancelRender(room.id, active.id)
      notify('Сборка отменена')
      await load()
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Не удалось отменить', false)
    }
  }

  return (
    <div className="panel-body result-panel">
      <p className="small">
        Озвучено <b>{recorded}</b> из <b>{total}</b> реплик. В готовом видео картинка исходника
        остаётся без изменений, музыка и эффекты — из оригинала, а голоса реплик берутся из записей
        участников.
      </p>

      {recorded === 0 && <p className="muted">Запишите хотя бы одну реплику — тогда сможем собрать.</p>}

      {recorded > 0 && !current && !active && (
        <>
          <label className="small">
            Неозвученные реплики:{' '}
            <select value={unrecorded} onChange={(e) => setUnrecorded(e.target.value as 'silent' | 'original')}>
              <option value="silent">оставить только фон</option>
              <option value="original">оставить оригинальный голос</option>
            </select>
          </label>
          <button onClick={() => void start()} disabled={busy}>
            {busy ? 'Запускаю…' : 'Собрать озвучку'}
          </button>
        </>
      )}

      {active && (
        <div className="render-active">
          <div className="row small">
            <b>{STAGE_LABEL[active.status] ?? active.status}</b>
            <span className="muted">{active.progress}%</span>
          </div>
          <div className="progress">
            <div style={{ width: `${active.progress}%` }} />
          </div>
          <p className="small muted">
            Реплик в сборке: {active.used_lines || active.recorded_lines} из {active.total_lines}
          </p>
          <button className="ghost" onClick={() => void cancel()}>
            Отменить
          </button>
        </div>
      )}

      {current && (
        <div className="render-done">
          <video controls preload="metadata" src={media.renderUrl(room.id, current.id)} />
          <div className="row small">
            <span>
              {humanTime(current.duration_ms)} · {humanSize(current.size_bytes)} · тейков{' '}
              {current.metrics?.used_takes ?? current.used_lines}
            </span>
            {typeof current.metrics?.loudness_lufs === 'number' && (
              <span className="muted">громкость {current.metrics.loudness_lufs.toFixed(1)} LUFS</span>
            )}
          </div>
          <div className="actions">
            <a className="button" href={media.renderDownloadUrl(room.id, current.id)} download>
              Скачать видео
            </a>
            <button className="ghost" onClick={() => void start()} disabled={busy}>
              Пересобрать
            </button>
          </div>
          <p className="small muted">
            Собрано {new Date(current.created_at).toLocaleString('ru-RU')}
            {current.metrics?.used_originals
              ? `, оригинальный голос оставлен в ${current.metrics.used_originals} репликах`
              : ''}
          </p>
        </div>
      )}

      {renders.some((r) => r.status === 'FAILED') && (
        <p className="small error">
          Сборка не удалась: {renders.find((r) => r.status === 'FAILED')?.error?.message ?? 'внутренняя ошибка'}.
          Попробуйте запустить ещё раз.
        </p>
      )}

      {renders.length > 1 && (
        <details className="small">
          <summary>История сборок ({renders.length})</summary>
          <ul className="plain">
            {renders.map((r) => (
              <li key={r.id}>
                {new Date(r.created_at).toLocaleString('ru-RU')} · {STAGE_LABEL[r.status] ?? r.status}
                {r.status === 'DONE' &&
                  (r.files_purged ? (
                    <span className="muted"> · файл удалён (место на диске)</span>
                  ) : (
                    <>
                      {' · '}
                      {humanSize(r.size_bytes)} ·{' '}
                      <a href={media.renderDownloadUrl(room.id, r.id)} download>
                        скачать
                      </a>
                    </>
                  ))}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
