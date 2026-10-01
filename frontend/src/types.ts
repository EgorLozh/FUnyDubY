/** Типы ответов API. Держим их в одном месте: схемы FastAPI — источник истины. */

export type Word = { w: string; t0: number; t1: number; p?: number | null }

export type RoomCounters = {
  participants: number
  lines: number
  recorded_lines: number
  assigned_lines: number
}

export type VideoMeta = {
  id: string
  original_filename: string
  container: string
  video_codec: string | null
  audio_codec: string | null
  size_bytes: number
  duration_ms: number
  width: number | null
  height: number | null
  fps: number | null
  has_audio: boolean
  sha256: string | null
  created_at: string
}

export type Room = {
  id: string
  title: string
  status: string
  settings: Record<string, unknown>
  created_at: string
  expires_at: string | null
  counters: RoomCounters
  video: VideoMeta | null
}

export type RoomCreated = {
  room: Room
  participant_id: string
  token: string
  share_url: string
}

export type Participant = {
  id: string
  display_name: string
  color: string
  is_creator: boolean
  last_seen_at: string | null
  created_at: string
}

export type ParticipantRegistered = {
  participant_id: string
  token: string
  display_name: string
  color: string
  is_creator: boolean
}

export type Line = {
  id: string
  idx: number
  start_ms: number
  end_ms: number
  duration_ms: number
  speaker_key: string
  speaker_label: string
  text: string
  is_short: boolean
  overlaps: boolean
  is_edited: boolean
  keep_original: boolean
  version: number
  has_original_audio: boolean
  words: Word[] | null
  assigned_participant_id: string | null
  assigned_display_name: string | null
  assigned_expires_at: string | null
  has_recording: boolean
  current_recording_id: string | null
  recording_duration_ms: number | null
}

export type Speaker = {
  speaker_key: string
  speaker_label: string
  lines: number
  total_ms: number
}

export type StageName =
  | 'extract_audio'
  | 'separate_speech'
  | 'transcribe'
  | 'diarize'
  | 'merge_dialogue'

export type JobStage = {
  stage: string
  status: string
  attempt: number
  progress: number
  duration_ms: number | null
  metrics: Record<string, unknown> | null
  artifacts: Record<string, unknown> | null
  error: Record<string, unknown> | null
}

export type Job = {
  id: string
  room_id: string
  status: string
  current_stage: string | null
  progress: number
  scope: string
  attempt: number
  error: Record<string, unknown> | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  stages: JobStage[]
}

export type Assignment = {
  line_id: string
  participant_id: string
  display_name: string | null
  assigned_at: string
  expires_at: string | null
  version: number
}

export type BulkAssignment = {
  captured: string[]
  already_mine: string[]
  busy: string[]
}

export type Recording = {
  id: string
  line_id: string
  participant_id: string | null
  take_number: number
  status: string
  is_current: boolean
  duration_ms: number | null
  size_bytes: number
  loudness_lufs: number | null
  rejected_reason: string | null
  created_at: string
}

export type RenderStatus = 'QUEUED' | 'MIXING' | 'ENCODING' | 'DONE' | 'FAILED' | 'CANCELED'

export type RenderMetrics = {
  used_takes?: number
  used_originals?: number
  silent_lines?: number
  applied_gain_db?: number
  peak_db?: number
  loudness_lufs?: number | null
}

export type Render = {
  id: string
  room_id: string
  status: RenderStatus
  progress: number
  total_lines: number
  recorded_lines: number
  used_lines: number
  size_bytes: number | null
  duration_ms: number | null
  is_current: boolean
  files_purged: boolean
  options: { unrecorded?: 'silent' | 'original' }
  metrics: RenderMetrics | null
  error: { code?: string; message?: string } | null
  created_at: string
  finished_at: string | null
  file_url: string | null
  download_url: string | null
}

export type PurgeScope = 'original' | 'artifacts' | 'recordings' | 'rendered'

export type AdminVideo = {
  duration_ms: number | null
  size_bytes: number | null
  container: string | null
  width: number | null
  height: number | null
}

export type AdminRoom = {
  id: string
  title: string
  status: string
  created_at: string
  expires_at: string | null
  deleted_at: string | null
  size_bytes: number
  categories: Record<string, number>
  files: Record<string, number>
  video: AdminVideo | null
  has_lines: boolean | null
  listing: Record<string, { path: string; size_bytes: number; modified_at: string }[]> | null
}

export type AdminRoomDetail = AdminRoom

export type AdminOrphan = {
  id: string
  size_bytes: number
  files: number
  modified_at: string
}

export type AdminOverview = {
  rooms: AdminRoom[]
  totals: { rooms: number; size_bytes: number; categories: Record<string, number> }
  disk_free_bytes: number
  orphans: { count: number; size_bytes: number }
}

export type AdminPurgeResult = {
  room_id: string
  scopes: Record<string, { freed_bytes: number; items?: number; room_reset?: boolean }>
  freed_bytes: number
  size_bytes_after: number
  categories_after: Record<string, number>
}

export type RoomEvent = {
  id?: number
  type: string
  payload?: Record<string, unknown>
}

/** Ошибка API в формате problem+json: code/title/status плюс поля вроде display_name. */
export class ApiError extends Error {
  status: number
  code: string
  extra: Record<string, unknown>

  constructor(status: number, code: string, title: string, extra: Record<string, unknown> = {}) {
    super(title)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.extra = extra
  }

  /** Человекочитаемая подсказка для типовых конфликтов. */
  get hint(): string {
    if (this.code === 'line_taken') {
      const who = (this.extra.display_name as string) || 'другой участник'
      return `Реплику уже взял(а) ${who}`
    }
    if (this.code === 'version_conflict') return 'Реплику успели изменить — обновите список'
    if (this.code === 'line_bounds_conflict') return 'Новые границы пересекаются с соседней репликой'
    if (this.code === 'storage_exhausted') return 'На сервере закончилось место — сообщите администратору'
    return this.message
  }
}
