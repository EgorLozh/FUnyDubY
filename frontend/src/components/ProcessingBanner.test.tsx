/** Статус обработки: прогресс, этапы и перезапуск после ошибки. */

import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ProcessingBanner } from './ProcessingBanner'
import type { Job } from '../types'

function makeJob(overrides: Partial<Job>): Job {
  return {
    id: 'job-1',
    room_id: 'room1',
    status: 'RUNNING',
    current_stage: 'transcribe',
    progress: 42,
    scope: 'all',
    attempt: 1,
    error: null,
    created_at: '',
    started_at: null,
    finished_at: null,
    stages: [
      { stage: 'extract_audio', status: 'DONE', attempt: 1, progress: 100, duration_ms: 900, metrics: null, artifacts: null, error: null },
      { stage: 'transcribe', status: 'RUNNING', attempt: 1, progress: 42, duration_ms: null, metrics: null, artifacts: null, error: null },
    ],
    ...overrides,
  }
}

describe('баннер обработки', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('показывает текущий этап и процент', () => {
    render(<ProcessingBanner roomId="room1" job={makeJob({})} onChanged={vi.fn()} />)
    expect(screen.getByText('Обработка')).toBeInTheDocument()
    expect(screen.getByText('Транскрипция')).toBeInTheDocument()
    expect(screen.getByText('42%')).toBeInTheDocument()
  })

  it('во время работы кнопка перезапуска заблокирована', () => {
    render(<ProcessingBanner roomId="room1" job={makeJob({})} onChanged={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Перезапустить' })).toBeDisabled()
  })

  it('после ошибки показывает понятную причину и разрешает перезапуск', () => {
    render(
      <ProcessingBanner
        roomId="room1"
        job={makeJob({
          status: 'FAILED',
          progress: 60,
          error: { code: 'no_speech_found', message: 'В дорожке не найдено речи', stage: 'separate_speech' },
        })}
        onChanged={vi.fn()}
      />,
    )
    expect(screen.getByText(/не найдено речи/)).toBeInTheDocument()
    expect(screen.getByText(/no_speech_found/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Перезапустить' })).toBeEnabled()
  })
})
