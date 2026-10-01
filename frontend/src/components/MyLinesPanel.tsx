/**
 * «Мои реплики» — рабочее место участника: прослушать оригинал, записать свой голос, выбрать
 * актуальный тейк.
 *
 * Запись ограничена длительностью реплики: рекордер останавливается сам ровно на её конце,
 * а сервер повторно проверяет длительность и нормализует тейк до точной длины. Захват реплики
 * продлевается heartbeat-ом, пока панель открыта, — иначе через `assignment_ttl_minutes`
 * реплика освободится и её сможет взять кто-то другой.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { api, media } from '../api/client'
import { invalidatePeaks, useAudioPeaks } from '../hooks/useAudioPeaks'
import { extensionFor, useRecorder } from '../hooks/useRecorder'
import { formatPrecise } from './VideoPlayer'
import { Waveform } from './Waveform'
import type { ApiError, Line, Recording } from '../types'

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
    return <div className="empty">Войдите в комнату под своим именем — тогда можно взять реплики.</div>
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
        <MyLine
          key={line.id}
          roomId={roomId}
          line={line}
          isActive={line.id === activeLineId}
          onSeek={onSeek}
          onPlayOriginal={onPlayOriginal}
          onChanged={onChanged}
          notify={notify}
        />
      ))}
    </div>
  )
}

function MyLine({
  roomId,
  line,
  isActive,
  onSeek,
  onPlayOriginal,
  onChanged,
  notify,
}: {
  roomId: string
  line: Line
  isActive: boolean
  onSeek: (startMs: number, endMs: number) => void
  onPlayOriginal: (lineId: string) => void
  onChanged: () => void
  notify: (message: string, ok?: boolean) => void
}) {
  const [takes, setTakes] = useState<Recording[]>([])
  const [busy, setBusy] = useState(false)

  const loadTakes = useCallback(async () => {
    try {
      setTakes(await api.listTakes(roomId, line.id))
    } catch {
      /* список тейков вторичен: ошибку показывать не нужно */
    }
  }, [roomId, line.id])

  useEffect(() => {
    void loadTakes()
  }, [loadTakes, line.has_recording, line.version])

  const recorder = useRecorder({
    limitMs: line.duration_ms,
    onRecorded: async (blob, mimeType) => {
      try {
        await api.uploadTake(
          roomId,
          line.id,
          blob,
          {
            idempotencyKey: `${line.id}:${Date.now()}`,
            filename: `take-${line.id.slice(0, 8)}.${extensionFor(mimeType)}`,
          },
          () => undefined,
        )
        invalidatePeaks(media.url(roomId, 'recording', line.id))
        notify(`Реплика озвучена (${(blob.size / 1024).toFixed(0)} КБ)`, true)
        await loadTakes()
        setPosition(0)
        void takeReload.current?.()
        onChanged()
      } catch (exc) {
        const error = exc as ApiError
        notify(error.hint ?? 'Не удалось сохранить запись')
        throw error
      }
    },
  })

  const current = takes.find((take) => take.is_current)
  const recording = recorder.status === 'recording'

  // Волны: оригинал — бледная подложка-ориентир, запись — поверх. Адрес тейка постоянный,
  // поэтому после новой записи кеш волны сбрасываем вручную, иначе останется старая картинка.
  const originalUrl = line.has_original_audio ? media.url(roomId, 'original', line.id) : null
  const takeUrl = current ? media.url(roomId, 'recording', line.id) : null
  const original = useAudioPeaks(originalUrl)
  const take = useAudioPeaks(takeUrl)
  const takeReload = useRef<null | (() => Promise<void>)>(null)
  takeReload.current = take.reload
  const [playing, setPlaying] = useState<'original' | 'take' | null>(null)
  const [position, setPosition] = useState(0)
  const originalAudio = useRef<HTMLAudioElement | null>(null)
  const takeAudio = useRef<HTMLAudioElement | null>(null)

  const togglePlay = useCallback((which: 'original' | 'take') => {
    const element = which === 'original' ? originalAudio.current : takeAudio.current
    const other = which === 'original' ? takeAudio.current : originalAudio.current
    if (!element) return
    other?.pause()
    if (element.paused) {
      void element
        .play()
        .then(() => setPlaying(which))
        .catch(() => setPlaying(null))
    } else {
      element.pause()
      setPlaying(null)
    }
  }, [])
  const elapsed = Math.min(recorder.elapsedMs, recorder.limitMs)
  const leftMs = Math.max(0, recorder.limitMs - elapsed)

  return (
    <div className={`line stack mine${isActive ? ' current' : ''}`}>
      <div>
        <div className="time">{formatPrecise(line.start_ms)}</div>
        <div className="time small">{(line.duration_ms / 1000).toFixed(1)} с</div>
      </div>

      <div>
        <div className="speaker">{line.speaker_label}</div>
        <div className="text">{line.text || <span className="muted">— нет текста —</span>}</div>

        <div className="meta">
          {current ? <span className="badge ok">записано: тейк {current.take_number}</span> : <span className="badge">не записано</span>}
          {line.overlaps && <span className="badge warn">перекрытие</span>}
        </div>

        {recording && (
          <div style={{ marginTop: '0.5rem' }}>
            <div className="row small">
              <span style={{ color: 'var(--accent)' }}>● запись</span>
              <span className="time">
                {(elapsed / 1000).toFixed(1)} / {(recorder.limitMs / 1000).toFixed(1)} с
              </span>
              <span className="muted">осталось {(leftMs / 1000).toFixed(1)} с</span>
            </div>
            <div className="progress" style={{ marginTop: '0.25rem' }}>
              <span style={{ width: `${Math.min(100, (elapsed / recorder.limitMs) * 100)}%` }} />
            </div>
            <div className="progress" style={{ marginTop: '0.25rem' }} title="уровень микрофона">
              <span style={{ width: `${Math.round(recorder.level * 100)}%`, opacity: 0.6 }} />
            </div>
          </div>
        )}

        {recorder.error && (
          <div className="small" style={{ color: 'var(--accent)', marginTop: '0.3rem' }}>
            {recorder.error}
          </div>
        )}

        {(originalUrl || takeUrl || recording) && (
          <div style={{ marginTop: '0.45rem' }}>
            <Waveform
              ghost={original.result?.peaks ?? null}
              peaks={recording ? null : (take.result?.peaks ?? null)}
              liveSource={recorder.getLivePeaks}
              active={recording}
              progress={playing === 'take' ? position : null}
              label={
                recording
                  ? '● идёт запись — волна растёт слева направо'
                  : takeUrl
                    ? 'ваша запись'
                    : 'ваша запись (пока пусто)'
              }
              hint={originalUrl ? 'бледная волна — оригинал реплики' : 'оригинал недоступен'}
            />
            <div className="row small" style={{ gap: '0.35rem', marginTop: '0.3rem' }}>
              {originalUrl && (
                <button className="ghost" onClick={() => togglePlay('original')}>
                  {playing === 'original' ? '⏸ оригинал' : '▶ оригинал'}
                </button>
              )}
              {takeUrl && (
                <button className="ghost" onClick={() => togglePlay('take')}>
                  {playing === 'take' ? '⏸ запись' : '▶ запись'}
                </button>
              )}
              {playing === 'take' && take.result && (
                <span className="muted">{(position * (take.result.durationMs / 1000)).toFixed(1)} с</span>
              )}
            </div>
            <audio
              ref={originalAudio}
              preload="none"
              hidden
              src={originalUrl ?? undefined}
              onTimeUpdate={(event) => {
                if (playing !== 'original') return
                const el = event.currentTarget
                setPosition(el.duration ? el.currentTime / el.duration : 0)
              }}
              onEnded={() => {
                setPlaying(null)
                setPosition(0)
              }}
            />
            <audio
              ref={takeAudio}
              preload="none"
              hidden
              src={takeUrl ?? undefined}
              onTimeUpdate={(event) => {
                if (playing !== 'take') return
                const el = event.currentTarget
                setPosition(el.duration ? el.currentTime / el.duration : 0)
              }}
              onEnded={() => {
                setPlaying(null)
                setPosition(0)
              }}
            />
          </div>
        )}

        {takes.length > 1 && (
          <div className="row small" style={{ marginTop: '0.4rem', gap: '0.35rem' }}>
            <span className="muted">тейки:</span>
            {takes.map((take) => (
              <span key={take.id} className="row" style={{ gap: '0.2rem' }}>
                <button
                  className={take.is_current ? 'primary' : 'ghost'}
                  disabled={busy || take.is_current}
                  title={`Сделать актуальным тейк ${take.take_number}`}
                  onClick={async () => {
                    setBusy(true)
                    try {
                      await api.makeTakeCurrent(roomId, line.id, take.id)
                      notify(`Актуальный тейк: ${take.take_number}`, true)
                      await loadTakes()
                      onChanged()
                    } catch {
                      notify('Не удалось переключить тейк')
                    } finally {
                      setBusy(false)
                    }
                  }}
                >
                  #{take.take_number}
                </button>
                <button
                  className="ghost"
                  disabled={busy}
                  title="Удалить тейк"
                  onClick={async () => {
                    setBusy(true)
                    try {
                      await api.deleteTake(roomId, line.id, take.id)
                      notify(`Тейк ${take.take_number} удалён`, true)
                      await loadTakes()
                      onChanged()
                    } catch {
                      notify('Не удалось удалить тейк')
                    } finally {
                      setBusy(false)
                    }
                  }}
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="actions">
        {recording ? (
          <button className="primary" onClick={recorder.stop}>
            Стоп
          </button>
        ) : (
          <button
            className="primary"
            disabled={recorder.status === 'uploading' || recorder.status === 'requesting'}
            onClick={() => void recorder.start()}
            title={`Запись ограничена длительностью реплики: ${(line.duration_ms / 1000).toFixed(1)} с`}
          >
            {recorder.status === 'uploading'
              ? 'Отправка…'
              : current
                ? 'Перезаписать'
                : `🎙 Записать (${(line.duration_ms / 1000).toFixed(1)} с)`}
          </button>
        )}
        <button disabled={!line.has_original_audio} onClick={() => onPlayOriginal(line.id)}>
          ▶ оригинал
        </button>
        <button onClick={() => onSeek(line.start_ms, line.end_ms)}>Найти в видео</button>
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
  )
}
