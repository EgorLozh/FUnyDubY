/**
 * Состояние комнаты: данные + живые обновления по SSE.
 *
 * События приходят из бэкенда (`line.updated`, `assignment.changed`, `job.progress`, …).
 * Перезагружаем данные с небольшим «склеиванием» — правки спикером-массовкой приходят
 * пачкой, и без него мы бы дёргали API на каждое событие.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { api, media, session } from '../api/client'
import type { ApiError, Job, Line, Participant, Room, RoomEvent, Speaker } from '../types'

export type Tab = 'dialogue' | 'mine' | 'participants' | 'result'

export type RoomData = {
  room: Room | null
  lines: Line[]
  speakers: Speaker[]
  participants: Participant[]
  job: Job | null
  events: RoomEvent[]
  loading: boolean
  error: ApiError | null
  reload: () => Promise<void>
}

const ERROR_CODES = new Set(['line_taken', 'version_conflict', 'line_bounds_conflict'])

export function useRoomData(roomId: string): RoomData {
  const [room, setRoom] = useState<Room | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [speakers, setSpeakers] = useState<Speaker[]>([])
  const [participants, setParticipants] = useState<Participant[]>([])
  const [job, setJob] = useState<Job | null>(null)
  const [events, setEvents] = useState<RoomEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)

  const timer = useRef<number | null>(null)
  const inFlight = useRef(false)

  const load = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      const [roomData, lineData, speakerData, participantData, jobs] = await Promise.all([
        api.getRoom(roomId),
        api.listLines(roomId),
        api.listSpeakers(roomId),
        api.listParticipants(roomId),
        api.listJobs(roomId),
      ])
      setRoom(roomData)
      setLines(lineData)
      setSpeakers(speakerData)
      setParticipants(participantData)
      setJob(jobs[0] ?? null)
      setError(null)
    } catch (exc) {
      setError(exc as ApiError)
    } finally {
      setLoading(false)
      inFlight.current = false
    }
  }, [roomId])

  const scheduleReload = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      timer.current = null
      void load()
    }, 250)
  }, [load])

  useEffect(() => {
    void load()
  }, [load])

  // SSE: сервер сам сообщает, что менять. EventSource переподключается браузером.
  useEffect(() => {
    const source = new EventSource(media.eventsUrl(roomId))
    const handler = (event: MessageEvent) => {
      try {
        const parsed = JSON.parse(event.data) as RoomEvent
        setEvents((prev) => [parsed, ...prev].slice(0, 30))
      } catch {
        /* сердцебиение — не событие */
      }
      scheduleReload()
    }
    source.onmessage = handler
    for (const name of ['job.stage', 'job.progress', 'job.failed', 'line.updated', 'assignment.changed']) {
      source.addEventListener(name, handler as EventListener)
    }
    return () => source.close()
  }, [roomId, scheduleReload])

  // Страховка на случай, если SSE отвалилось: пока идёт обработка — опрашиваем.
  useEffect(() => {
    const active = job && (job.status === 'RUNNING' || job.status === 'QUEUED')
    if (!active) return
    const id = window.setInterval(() => void load(), 4000)
    return () => window.clearInterval(id)
  }, [job, load])

  return { room, lines, speakers, participants, job, events, loading, error, reload: load }
}

/** Мой идентификатор в комнате (из localStorage): без него не отличить «мои реплики». */
export function useMyParticipantId(roomId: string): string | null {
  return session.participantId(roomId)
}

export function isAssignmentError(error: unknown): boolean {
  return error instanceof Error && ERROR_CODES.has((error as ApiError).code)
}
