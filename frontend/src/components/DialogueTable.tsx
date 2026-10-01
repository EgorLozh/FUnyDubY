/**
 * Таблица реплик: правка текста и спикера, разделение/склейка, назначение, прослушивание.
 *
 * Правка текста идёт с `expected_version`: если реплику успели изменить, сервер вернёт 409,
 * и мы не затрём чужую работу молча — покажем подсказку и перечитаем список.
 */

import { useMemo, useState } from 'react'

import { api, media } from '../api/client'
import { formatPrecise } from './VideoPlayer'
import type { ApiError, Line, Participant, Speaker } from '../types'

type Props = {
  roomId: string
  lines: Line[]
  speakers: Speaker[]
  participants: Participant[]
  myId: string | null
  activeLineId: string | null
  onSeek: (startMs: number, endMs: number) => void
  onPlayOriginal: (lineId: string) => void
  onChanged: () => void
  notify: (message: string, ok?: boolean) => void
}

type Filter = 'all' | 'mine' | 'free'

export function DialogueTable({
  roomId,
  lines,
  speakers,
  participants,
  myId,
  activeLineId,
  onSeek,
  onPlayOriginal,
  onChanged,
  notify,
}: Props) {
  const [filter, setFilter] = useState<Filter>('all')
  const [speakerFilter, setSpeakerFilter] = useState<string>('')
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [busyLine, setBusyLine] = useState<string | null>(null)

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return lines.filter((line) => {
      if (speakerFilter && line.speaker_key !== speakerFilter) return false
      if (filter === 'mine' && line.assigned_participant_id !== myId) return false
      if (filter === 'free' && line.assigned_participant_id) return false
      if (needle && !line.text.toLowerCase().includes(needle)) return false
      return true
    })
  }, [lines, filter, speakerFilter, search, myId])

  async function guard(line: Line, action: () => Promise<void>, okMessage: string) {
    setBusyLine(line.id)
    try {
      await action()
      notify(okMessage, true)
      onChanged()
    } catch (exc) {
      notify((exc as ApiError).hint)
      onChanged()
    } finally {
      setBusyLine(null)
    }
  }

  function saveText(line: Line) {
    const text = draft.trim()
    if (text === line.text) {
      setEditing(null)
      return
    }
    void guard(
      line,
      async () => {
        await api.updateLine(roomId, line.id, { text, expected_version: line.version })
        setEditing(null)
      },
      'Текст реплики обновлён',
    )
  }

  async function renameSpeaker(key: string, label: string) {
    try {
      await api.renameSpeaker(roomId, key, { label })
      notify(`Спикер переименован: ${label}`, true)
      onChanged()
    } catch (exc) {
      notify((exc as ApiError).hint)
    }
  }

  return (
    <div className="panel">
      <div className="toolbar">
        <div className="row" style={{ gap: '0.3rem' }}>
          {(
            [
              ['all', 'Все реплики'],
              ['mine', 'Мои'],
              ['free', 'Свободные'],
            ] as [Filter, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              className={filter === value ? 'primary' : 'ghost'}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <select value={speakerFilter} onChange={(event) => setSpeakerFilter(event.target.value)}>
          <option value="">Все спикеры</option>
          {speakers.map((speaker) => (
            <option key={speaker.speaker_key} value={speaker.speaker_key}>
              {speaker.speaker_label} ({speaker.lines})
            </option>
          ))}
        </select>
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Поиск по тексту"
        />
        <div className="spacer" />
        <span className="muted small">
          показано {visible.length} из {lines.length}
        </span>
      </div>

      <div className="lines">
        {visible.length === 0 && <div className="empty">Реплик по этому фильтру нет</div>}
        {visible.map((line) => {
          const mine = Boolean(myId) && line.assigned_participant_id === myId
          const takenByOther = Boolean(line.assigned_participant_id) && !mine
          const holder =
            line.assigned_display_name ??
            participants.find((person) => person.id === line.assigned_participant_id)?.display_name ??
            'участник'
          return (
            <div
              key={line.id}
              className={`line${line.id === activeLineId ? ' current' : ''}${mine ? ' mine' : ''}`}
            >
              <div>
                <button className="ghost time" onClick={() => onSeek(line.start_ms, line.end_ms)}>
                  {formatPrecise(line.start_ms)}
                </button>
                <div className="time small">{(line.duration_ms / 1000).toFixed(1)} с</div>
              </div>

              <div>
                <div className="meta">
                  <select
                    className="speaker"
                    value={line.speaker_key}
                    onChange={(event) => {
                      const key = event.target.value
                      const label =
                        speakers.find((speaker) => speaker.speaker_key === key)?.speaker_label ?? key
                      void guard(
                        line,
                        () => api.updateLine(roomId, line.id, { speaker_label: label }).then(() => {}),
                        'Спикер реплики обновлён',
                      )
                    }}
                  >
                    {!speakers.some((speaker) => speaker.speaker_key === line.speaker_key) && (
                      <option value={line.speaker_key}>{line.speaker_label}</option>
                    )}
                    {speakers.map((speaker) => (
                      <option key={speaker.speaker_key} value={speaker.speaker_key}>
                        {speaker.speaker_label}
                      </option>
                    ))}
                  </select>
                  {line.is_edited && <span className="badge">правлено</span>}
                  {line.overlaps && <span className="badge warn">перекрытие</span>}
                  {line.has_recording && <span className="badge ok">озвучено</span>}
                  {takenByOther && <span className="who taken">{holder}</span>}
                  {mine && <span className="who">моя</span>}
                </div>

                {editing === line.id ? (
                  <>
                    <textarea
                      aria-label="Текст реплики"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      autoFocus
                    />
                    <div className="actions" style={{ justifyContent: 'flex-start', marginTop: '0.3rem' }}>
                      <button className="primary" onClick={() => saveText(line)}>
                        Сохранить
                      </button>
                      <button className="ghost" onClick={() => setEditing(null)}>
                        Отмена
                      </button>
                    </div>
                  </>
                ) : (
                  <div
                    className="text"
                    onDoubleClick={() => {
                      setEditing(line.id)
                      setDraft(line.text)
                    }}
                    title="Двойной клик — правка текста"
                  >
                    {line.text || <span className="muted">— нет текста —</span>}
                  </div>
                )}
              </div>

              <div className="actions">
                <button
                  disabled={!line.has_original_audio}
                  onClick={() => onPlayOriginal(line.id)}
                  title="Прослушать оригинальную реплику"
                >
                  ▶
                </button>
                <button
                  onClick={() => {
                    setEditing(line.id)
                    setDraft(line.text)
                  }}
                >
                  Правка
                </button>
                {mine ? (
                  <button
                    className="ghost"
                    disabled={busyLine === line.id}
                    onClick={() =>
                      void guard(line, () => api.releaseLine(roomId, line.id), 'Реплика освобождена')
                    }
                  >
                    Отдать
                  </button>
                ) : (
                  <button
                    className="primary"
                    disabled={busyLine === line.id || takenByOther}
                    onClick={() =>
                      void guard(line, () => api.claimLine(roomId, line.id).then(() => {}), 'Реплика взята')
                    }
                  >
                    Взять
                  </button>
                )}
                <button
                  disabled={busyLine === line.id}
                  title="Разделить реплику пополам"
                  onClick={() => {
                    const middle = Math.round((line.start_ms + line.end_ms) / 2)
                    const raw = window.prompt('Разделить реплику в момент, мс:', String(middle))
                    if (!raw) return
                    const at = Number(raw)
                    if (!Number.isFinite(at)) {
                      notify('Нужно число в миллисекундах')
                      return
                    }
                    void guard(line, async () => {
                      await api.splitLine(roomId, line.id, at)
                    }, 'Реплика разделена')
                  }}
                >
                  Разделить
                </button>
                <button
                  disabled={busyLine === line.id}
                  title="Склеить со следующей"
                  onClick={() =>
                    void guard(line, async () => {
                      await api.mergeLine(roomId, line.id)
                    }, 'Реплики склеены')
                  }
                >
                  Склеить
                </button>
                <a href={media.url(roomId, 'original', line.id)} download={`line-${line.idx}.wav`}>
                  <button>⤓</button>
                </a>
              </div>
            </div>
          )
        })}
      </div>

      <div className="panel-body row small muted">
        <span>Переименовать спикера:</span>
        {speakers.map((speaker) => (
          <button
            key={speaker.speaker_key}
            onClick={() => {
              const label = window.prompt(`Новое имя для «${speaker.speaker_label}»`, speaker.speaker_label)
              if (label && label.trim()) void renameSpeaker(speaker.speaker_key, label.trim())
            }}
          >
            {speaker.speaker_label}
          </button>
        ))}
      </div>
    </div>
  )
}
