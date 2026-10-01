/** Статус обработки: этапы, прогресс, ошибка и перезапуск. */

import { useState } from 'react'

import { api, stages } from '../api/client'
import type { Job } from '../types'

const STAGE_LABELS = new Map(stages.map((stage) => [stage.key, stage.label]))

const STATUS_TEXT: Record<string, string> = {
  QUEUED: 'В очереди',
  RUNNING: 'Обработка',
  DONE: 'Готово',
  FAILED: 'Ошибка',
  CANCELED: 'Отменено',
}

export function ProcessingBanner({
  roomId,
  job,
  onChanged,
}: {
  roomId: string
  job: Job | null
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const active = job && (job.status === 'RUNNING' || job.status === 'QUEUED')
  const failed = job?.status === 'FAILED'

  async function restart() {
    setBusy(true)
    try {
      await api.startJob(roomId, 'all', true)
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  if (!job) {
    return (
      <div className="banner">
        <span className="muted">Видео загружено, обработка ещё не запускалась.</span>
        <button onClick={restart} disabled={busy}>
          Запустить обработку
        </button>
      </div>
    )
  }

  return (
    <div className={`banner${failed ? ' error' : ''}`}>
      <div style={{ flex: 1 }}>
        <div className="row" style={{ gap: '0.5rem' }}>
          <strong>{STATUS_TEXT[job.status] ?? job.status}</strong>
          {job.current_stage && (
            <span className="muted small">
              {STAGE_LABELS.get(job.current_stage as never) ?? job.current_stage}
            </span>
          )}
          <span className="muted small">{job.progress}%</span>
        </div>
        {failed && job.error && (
          <div className="small" style={{ color: 'var(--accent)', marginTop: '0.2rem' }}>
            {String(job.error.message ?? 'этап не завершился')}{' '}
            <span className="muted">
              ({String(job.error.code ?? '')}
              {job.error.stage ? `, этап ${String(job.error.stage)}` : ''})
            </span>
          </div>
        )}
        <div className="progress" style={{ marginTop: '0.4rem' }}>
          <span style={{ width: `${job.progress}%` }} />
        </div>
      </div>
      <button onClick={restart} disabled={busy || Boolean(active)} title="Перезапустить конвейер">
        Перезапустить
      </button>
    </div>
  )
}

export function StageList({ job }: { job: Job | null }) {
  if (!job || job.stages.length === 0) return null
  return (
    <div className="panel-body stage-list">
      {job.stages.map((stage) => (
        <div className="stage-row" key={stage.stage}>
          <span>
            <span
              className={`dot ${
                stage.status === 'DONE'
                  ? 'done'
                  : stage.status === 'RUNNING'
                    ? 'running'
                    : stage.status === 'FAILED'
                      ? 'failed'
                      : ''
              }`}
            />
            {STAGE_LABELS.get(stage.stage as never) ?? stage.stage}
          </span>
          <span className="progress">
            <span style={{ width: `${stage.status === 'DONE' ? 100 : stage.progress}%` }} />
          </span>
          <span className="time">
            {stage.duration_ms ? `${(stage.duration_ms / 1000).toFixed(1)} с` : '—'}
          </span>
        </div>
      ))}
    </div>
  )
}
