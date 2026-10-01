/**
 * Комната: плеер, диалог, мои реплики, участники, результат.
 *
 * Порядок экранов повторяет путь пользователя: нет имени → вход; нет видео → загрузка;
 * идёт обработка → прогресс; готово → диалог и работа над репликами.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'

import { ApiError } from '../types'
import { api, session } from '../api/client'
import { useRoomData } from '../hooks/useRoomData'
import { DialogueTable } from '../components/DialogueTable'
import { MyLinesPanel } from '../components/MyLinesPanel'
import { ParticipantsPanel } from '../components/ParticipantsPanel'
import { ProcessingBanner, StageList } from '../components/ProcessingBanner'
import { VideoPlayer, type PlayerHandle } from '../components/VideoPlayer'

type Tab = 'dialogue' | 'mine' | 'participants' | 'result'

const TABS: [Tab, string][] = [
  ['dialogue', 'Диалог'],
  ['mine', 'Мои реплики'],
  ['participants', 'Участники'],
  ['result', 'Результат'],
]

export function RoomPage() {
  const { roomId = '' } = useParams()
  const [myId, setMyId] = useState<string | null>(session.participantId(roomId))
  const [tab, setTab] = useState<Tab>('dialogue')
  const [toast, setToast] = useState<{ text: string; ok: boolean } | null>(null)
  const [currentMs, setCurrentMs] = useState(0)
  const [uploadPercent, setUploadPercent] = useState<number | null>(null)
  const [joinName, setJoinName] = useState(session.name(roomId) ?? '')
  const player = useRef<PlayerHandle>(null)

  const { room, lines, speakers, participants, job, loading, error, reload } = useRoomData(roomId)

  const notify = useCallback((text: string, ok = false) => {
    setToast({ text, ok })
    window.setTimeout(() => setToast(null), 4200)
  }, [])

  useEffect(() => {
    setMyId(session.participantId(roomId))
  }, [roomId, participants.length])

  const activeLineId = useMemo(() => {
    const line = lines.find((item) => currentMs >= item.start_ms && currentMs < item.end_ms)
    return line?.id ?? null
  }, [lines, currentMs])

  async function upload(file: File) {
    setUploadPercent(0)
    try {
      await api.uploadVideo(roomId, file, setUploadPercent)
      notify('Видео загружено, запускаю обработку', true)
      await api.startJob(roomId, 'all')
      await reload()
    } catch (exc) {
      notify((exc as ApiError).hint)
    } finally {
      setUploadPercent(null)
    }
  }

  if (error) {
    return (
      <div className="home">
        <div className="brand">FUNYDUBY</div>
        <div className="card">
          <h2>Комната недоступна</h2>
          <p className="muted">{error.hint}</p>
        </div>
      </div>
    )
  }

  if (loading && !room) return <div className="empty">Загружаю комнату…</div>

  // Вход в комнату: без токена участника реплики брать нельзя, а он выдаётся по имени.
  if (!myId) {
    return (
      <div className="home">
        <div className="brand">FUNYDUBY</div>
        <h1>{room?.title ?? 'Комната'}</h1>
        <form
          className="card"
          onSubmit={async (event) => {
            event.preventDefault()
            try {
              const registered = await api.joinRoom(roomId, joinName.trim())
              session.save(roomId, registered.token, registered.participant_id, registered.display_name)
              setMyId(registered.participant_id)
              await reload()
              notify(`Вы вошли как ${registered.display_name}`, true)
            } catch (exc) {
              notify((exc as ApiError).hint)
            }
          }}
        >
          <div className="field">
            <label htmlFor="join-name">Ваше имя в комнате</label>
            <input
              id="join-name"
              value={joinName}
              onChange={(event) => setJoinName(event.target.value)}
              maxLength={40}
              required
            />
          </div>
          <button className="primary" type="submit" disabled={joinName.trim().length === 0}>
            Войти
          </button>
        </form>
      </div>
    )
  }

  const hasVideo = Boolean(room?.video)
  const processing = !job || job.status === 'RUNNING' || job.status === 'QUEUED'

  return (
    <div className="app">
      <div className="topbar">
        <span className="brand">FUNYDUBY</span>
        <span className="title">{room?.title ?? 'Комната'}</span>
        <span className="badge">{room?.id}</span>
        <span className="muted small">реплик: {room?.counters.lines ?? 0}</span>
        <span className="muted small">озвучено: {room?.counters.recorded_lines ?? 0}</span>
        <div className="spacer" />
        <button
          className="ghost"
          onClick={async () => {
            await navigator.clipboard?.writeText(window.location.href)
            notify('Ссылка на комнату скопирована', true)
          }}
        >
          Ссылка
        </button>
        <span className="badge">{session.name(roomId) ?? 'участник'}</span>
      </div>

      {!hasVideo ? (
        <div className="home" style={{ paddingTop: '2rem' }}>
          <div className="card">
            <h2>Загрузите видео</h2>
            <p className="muted small">
              MP4, WebM или MOV до 10 минут. Речь, музыка и эффекты будут разделены автоматически.
            </p>
            <input
              type="file"
              accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) void upload(file)
              }}
            />
            {uploadPercent !== null && (
              <>
                <div className="progress" style={{ marginTop: '0.8rem' }}>
                  <span style={{ width: `${uploadPercent}%` }} />
                </div>
                <div className="muted small" style={{ marginTop: '0.3rem' }}>
                  Загрузка: {uploadPercent}%
                </div>
              </>
            )}
          </div>
        </div>
      ) : (
        <>
          <ProcessingBanner roomId={roomId} job={job} onChanged={() => void reload()} />

          <div className="room">
            <div>
              <VideoPlayer
                ref={player}
                roomId={roomId}
                lines={lines}
                onTimeUpdate={setCurrentMs}
              />
              <div className="panel" style={{ marginTop: '0.8rem' }}>
                <div className="panel-head">
                  <strong>Обработка</strong>
                  <span className="muted small">
                    {job ? `${job.progress}%` : 'не запускалась'}
                  </span>
                </div>
                <StageList job={job} />
                {room?.video && (
                  <div className="panel-body small muted">
                    {room.video.original_filename} · {(room.video.duration_ms / 1000).toFixed(1)} с ·{' '}
                    {(room.video.size_bytes / 1024 / 1024).toFixed(1)} МБ
                  </div>
                )}
              </div>
            </div>

            <div className="panel">
              <div className="tabs">
                {TABS.map(([value, label]) => (
                  <button
                    key={value}
                    className={tab === value ? 'active' : ''}
                    onClick={() => setTab(value)}
                  >
                    {label}
                    {value === 'mine' && lines.filter((l) => l.assigned_participant_id === myId).length > 0
                      ? ` (${lines.filter((l) => l.assigned_participant_id === myId).length})`
                      : ''}
                  </button>
                ))}
              </div>

              {tab === 'dialogue' && (
                <DialogueTable
                  roomId={roomId}
                  lines={lines}
                  speakers={speakers}
                  participants={participants}
                  myId={myId}
                  activeLineId={activeLineId}
                  onSeek={(start, end) => player.current?.playRange(start, end)}
                  onPlayOriginal={(lineId) => player.current?.playOriginal(lineId)}
                  onChanged={() => void reload()}
                  notify={notify}
                />
              )}

              {tab === 'mine' && (
                <MyLinesPanel
                  roomId={roomId}
                  lines={lines}
                  myId={myId}
                  activeLineId={activeLineId}
                  onSeek={(start, end) => player.current?.playRange(start, end)}
                  onPlayOriginal={(lineId) => player.current?.playOriginal(lineId)}
                  onChanged={() => void reload()}
                  notify={notify}
                />
              )}

              {tab === 'participants' && (
                <ParticipantsPanel
                  roomId={roomId}
                  participants={participants}
                  lines={lines}
                  speakers={speakers}
                  myId={myId}
                  onChanged={() => void reload()}
                  notify={notify}
                />
              )}

              {tab === 'result' && (
                <div className="panel-body">
                  <p className="muted">
                    Здесь появится готовое видео: картинка исходника, музыка и эффекты без
                    оригинального голоса, поверх — ваши записи.
                  </p>
                  <p className="small">
                    Озвучено {room?.counters.recorded_lines ?? 0} из {room?.counters.lines ?? 0} реплик.
                    {processing
                      ? ' Дождитесь окончания обработки.'
                      : ' Сборка финального микса — следующий этап разработки.'}
                  </p>
                  <button disabled>Скачать финальное видео</button>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {toast && <div className={`toast${toast.ok ? ' ok' : ''}`}>{toast.text}</div>}
    </div>
  )
}
