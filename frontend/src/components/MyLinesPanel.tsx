/**
 * «Мои реплики» — рабочее место участника.
 *
 * Показывает только то, что человек взял: реплику, её оригинал и кнопку записи.
 * Захват продлевается heartbeat-ом, пока панель открыта, — иначе через 15 минут
 * (assignment_ttl_minutes) реплика освободится и её сможет взять кто-то другой.
 */

import { useEffect } from 'react'

import { api, media } from '../api/client'
import { formatPrecise } from './VideoPlayer'
import type { Line } from '../types'

const HEARTBEAT_MS = 5 * 60 * 1000

export function MyLinesPanel({
  roomId,
  lines,
  myId,
  activeLineId,
  onSeek,
  onPlayOriginal,
  onChanged,
  notify,
}: {
  roomId: string
  lines: Line[]
  myId: string | null
  activeLineId: string | null
  onSeek: (startMs: number, endMs: number) => void
  onPlayOriginal: (lineId: string) => void
  onChanged: () => void
  notify: (message: string, ok?: boolean) => void
}) {
  const mine = lines.filter((line) => line.assigned_participant_id === myId)

  useEffect(() => {
    if (!myId || mine.length === 0) return
    const id = window.setInterval(() => {
      for (const line of mine) void api.heartbeatLine(roomId, line.id).catch(() => undefined)
    }, HEARTBEAT_MS)
    return () => window.clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, myId, mine.map((line) => line.id).join(',')])

  if (!myId) {
    return (
      <div className="empty">
        Войдите в комнату под своим именем — тогда можно взять реплики.
      </div>
    )
  }

  return (
    <div className="lines">
      {mine.length === 0 && (
        <div className="empty">
          Вы ещё не взяли ни одной реплики. Нажмите «Взять» в списке диалога или забрать
          все реплики спикера на вкладке «Участники».
        </div>
      )}
      {mine.map((line) => (
        <div key={line.id} className={`line stack mine${line.id === activeLineId ? ' current' : ''}`}>
          <div>
            <div className="time">{formatPrecise(line.start_ms)}</div>
            <div className="time small">{(line.duration_ms / 1000).toFixed(1)} с</div>
          </div>
          <div>
            <div className="speaker">{line.speaker_label}</div>
            <div className="text">{line.text || <span className="muted">— нет текста —</span>}</div>
            {line.has_recording ? (
              <div className="meta">
                <span className="badge ok">записано</span>
                <audio controls src={media.url(roomId, 'recording', line.id)} />
              </div>
            ) : (
              <div className="meta">
                <span className="badge">не записано</span>
              </div>
            )}
          </div>
          <div className="actions">
            <button
              disabled={!line.has_original_audio}
              onClick={() => onPlayOriginal(line.id)}
              title="Прослушать оригинал"
            >
              ▶ оригинал
            </button>
            <button onClick={() => onSeek(line.start_ms, line.end_ms)}>Найти в видео</button>
            <button
              className="primary"
              disabled
              title="Запись голоса с микрофона — следующий этап разработки"
            >
              Записать
            </button>
            <button
              className="ghost"
              onClick={async () => {
                try {
                  await api.releaseLine(roomId, line.id)
                  notify('Реплика освобождена', true)
                  onChanged()
                } catch {
                  notify('Не удалось освободить реплику')
                }
              }}
            >
              Отдать
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
