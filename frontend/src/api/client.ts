/**
 * Клиент API: один тонкий слой над fetch.
 *
 * Токен участника хранится в localStorage по комнате — регистрации нет, вход в комнату
 * сводится к «открыл ссылку → получил или переиспользовал токен». Токен не уходит никуда,
 * кроме заголовка X-Participant-Token.
 */

import {
  ApiError,
  type Assignment,
  type BulkAssignment,
  type Job,
  type Line,
  type Participant,
  type ParticipantRegistered,
  type Room,
  type RoomCreated,
  type Speaker,
  type StageName,
  type VideoMeta,
} from '../types'

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api'

export type MediaKind = 'speech' | 'background' | 'original' | 'recording'

// ------------------------------------------------------------------ токен участника

const tokenKey = (roomId: string) => `funyduby:token:${roomId}`
const nameKey = (roomId: string) => `funyduby:name:${roomId}`
const pidKey = (roomId: string) => `funyduby:participant:${roomId}`

export const session = {
  token(roomId: string): string | null {
    return localStorage.getItem(tokenKey(roomId))
  },
  participantId(roomId: string): string | null {
    return localStorage.getItem(pidKey(roomId))
  },
  name(roomId: string): string | null {
    return localStorage.getItem(nameKey(roomId))
  },
  save(roomId: string, token: string, participantId: string, displayName: string): void {
    localStorage.setItem(tokenKey(roomId), token)
    localStorage.setItem(pidKey(roomId), participantId)
    localStorage.setItem(nameKey(roomId), displayName)
  },
  clear(roomId: string): void {
    localStorage.removeItem(tokenKey(roomId))
    localStorage.removeItem(pidKey(roomId))
    localStorage.removeItem(nameKey(roomId))
  },
}

// ------------------------------------------------------------------ базовый запрос

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Record<string, unknown>
    const code = String(body.code ?? 'http_error')
    const title = String(body.title ?? response.statusText)
    const { type: _type, title: _title, status: _status, code: _code, ...extra } = body
    return new ApiError(response.status, code, title, extra)
  } catch {
    return new ApiError(response.status, 'http_error', response.statusText)
  }
}

type RequestOptions = {
  method?: string
  body?: unknown
  roomId?: string
  token?: string | null
  signal?: AbortSignal
  formData?: FormData
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {}
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  const token = options.token ?? (options.roomId ? session.token(options.roomId) : null)
  if (token) headers['X-Participant-Token'] = token

  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method ?? (options.body !== undefined ? 'POST' : 'GET'),
    headers,
    body: options.formData ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined),
    signal: options.signal,
  })
  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

/** Загрузка с прогрессом: fetch не умеет прогресс, поэтому здесь XHR. */
export function uploadWithProgress<T>(
  path: string,
  file: File,
  roomId: string,
  onProgress: (percent: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('file', file)
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}${path}`)
    const token = session.token(roomId)
    if (token) xhr.setRequestHeader('X-Participant-Token', token)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as T)
        } catch {
          resolve(undefined as T)
        }
        return
      }
      try {
        const body = JSON.parse(xhr.responseText) as Record<string, unknown>
        reject(
          new ApiError(
            xhr.status,
            String(body.code ?? 'http_error'),
            String(body.title ?? xhr.statusText),
            body,
          ),
        )
      } catch {
        reject(new ApiError(xhr.status, 'http_error', xhr.statusText))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, 'network_error', 'Сеть недоступна'))
    xhr.send(form)
  })
}

// ------------------------------------------------------------------ комнаты

export const api = {
  createRoom(title: string, displayName: string): Promise<RoomCreated> {
    return request<RoomCreated>('/rooms', { body: { title, display_name: displayName } })
  },

  getRoom(roomId: string): Promise<Room> {
    return request<Room>(`/rooms/${roomId}`, { roomId })
  },

  joinRoom(roomId: string, displayName: string): Promise<ParticipantRegistered> {
    return request<ParticipantRegistered>(`/rooms/${roomId}/participants`, {
      body: { display_name: displayName },
      roomId,
      token: '',
    })
  },

  listParticipants(roomId: string): Promise<Participant[]> {
    return request<Participant[]>(`/rooms/${roomId}/participants`, { roomId })
  },

  deleteRoom(roomId: string): Promise<void> {
    return request<void>(`/rooms/${roomId}`, { method: 'DELETE', roomId })
  },

  // ---------------------------------------------------------------- видео и обработка

  uploadVideo(roomId: string, file: File, onProgress: (percent: number) => void) {
    return uploadWithProgress<VideoMeta>(`/rooms/${roomId}/video`, file, roomId, onProgress)
  },

  getVideo(roomId: string): Promise<VideoMeta> {
    return request<VideoMeta>(`/rooms/${roomId}/video`, { roomId })
  },

  startJob(roomId: string, scope = 'all', force = false): Promise<Job> {
    return request<Job>(`/rooms/${roomId}/jobs`, { body: { scope, force }, roomId })
  },

  listJobs(roomId: string): Promise<Job[]> {
    return request<Job[]>(`/rooms/${roomId}/jobs`, { roomId })
  },

  getJob(roomId: string, jobId: string): Promise<Job> {
    return request<Job>(`/rooms/${roomId}/jobs/${jobId}`, { roomId })
  },

  // ---------------------------------------------------------------- диалог

  listLines(
    roomId: string,
    params: { speaker?: string; assignedTo?: 'me' | 'unassigned' | 'all' } = {},
  ): Promise<Line[]> {
    const query = new URLSearchParams()
    if (params.speaker) query.set('speaker', params.speaker)
    if (params.assignedTo && params.assignedTo !== 'all') query.set('assigned_to', params.assignedTo)
    const suffix = query.toString() ? `?${query}` : ''
    return request<Line[]>(`/rooms/${roomId}/lines${suffix}`, { roomId })
  },

  updateLine(
    roomId: string,
    lineId: string,
    patch: {
      text?: string
      speaker_label?: string
      start_ms?: number
      end_ms?: number
      expected_version?: number
    },
  ): Promise<Line> {
    return request<Line>(`/rooms/${roomId}/lines/${lineId}`, {
      method: 'PATCH',
      body: patch,
      roomId,
    })
  },

  splitLine(roomId: string, lineId: string, atMs: number): Promise<Line[]> {
    return request<Line[]>(`/rooms/${roomId}/lines/${lineId}/split`, {
      body: { at_ms: atMs },
      roomId,
    })
  },

  mergeLine(roomId: string, lineId: string): Promise<Line> {
    return request<Line>(`/rooms/${roomId}/lines/${lineId}/merge`, { body: {}, roomId })
  },

  listSpeakers(roomId: string): Promise<Speaker[]> {
    return request<Speaker[]>(`/rooms/${roomId}/speakers`, { roomId })
  },

  renameSpeaker(
    roomId: string,
    speakerKey: string,
    patch: { label?: string; merge_into?: string },
  ): Promise<Speaker> {
    return request<Speaker>(`/rooms/${roomId}/speakers/${speakerKey}`, {
      method: 'PATCH',
      body: patch,
      roomId,
    })
  },

  // ---------------------------------------------------------------- назначения

  claimLine(roomId: string, lineId: string, participantId?: string): Promise<Assignment> {
    return request<Assignment>(`/rooms/${roomId}/lines/${lineId}/assignment`, {
      method: 'PUT',
      body: participantId ? { participant_id: participantId } : {},
      roomId,
    })
  },

  releaseLine(roomId: string, lineId: string): Promise<void> {
    return request<void>(`/rooms/${roomId}/lines/${lineId}/assignment`, {
      method: 'DELETE',
      roomId,
    })
  },

  heartbeatLine(roomId: string, lineId: string): Promise<Assignment> {
    return request<Assignment>(`/rooms/${roomId}/lines/${lineId}/assignment/heartbeat`, {
      body: {},
      roomId,
    })
  },

  bulkClaim(
    roomId: string,
    payload: { scope: 'all-unassigned' | 'my-speaker' | 'line-ids'; speaker_key?: string },
  ): Promise<BulkAssignment> {
    return request<BulkAssignment>(`/rooms/${roomId}/assignments/bulk`, { body: payload, roomId })
  },
}

// ------------------------------------------------------------------ ссылки на медиа

export const media = {
  url(roomId: string, kind: MediaKind, ref: string): string {
    return `${API_BASE}/rooms/${roomId}/media/${kind}/${encodeURIComponent(ref)}`
  },
  videoUrl(roomId: string): string {
    return `${API_BASE}/rooms/${roomId}/video/file`
  },
  eventsUrl(roomId: string): string {
    return `${API_BASE}/rooms/${roomId}/events`
  },
}

export const stages: { key: StageName; label: string }[] = [
  { key: 'extract_audio', label: 'Извлечение звука' },
  { key: 'separate_speech', label: 'Отделение речи' },
  { key: 'transcribe', label: 'Транскрипция' },
  { key: 'diarize', label: 'Определение говорящих' },
  { key: 'merge_dialogue', label: 'Сборка реплик' },
]
