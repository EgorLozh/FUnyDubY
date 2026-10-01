/** Диалог: рендер реплик, состояние назначения и захват реплики. */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DialogueTable } from './DialogueTable'
import { session } from '../api/client'
import type { Line, Participant, Speaker } from '../types'

const speaker: Speaker = {
  speaker_key: 'spk_0',
  speaker_label: 'Speaker 1',
  lines: 2,
  total_ms: 6000,
}

const participants: Participant[] = [
  { id: 'me', display_name: 'Егор', color: '#ff5c4d', is_creator: true, last_seen_at: null, created_at: '' },
  { id: 'other', display_name: 'Аня', color: '#46d39a', is_creator: false, last_seen_at: null, created_at: '' },
]

function makeLine(overrides: Partial<Line>): Line {
  return {
    id: 'line-1',
    idx: 0,
    start_ms: 500,
    end_ms: 3000,
    duration_ms: 2500,
    speaker_key: 'spk_0',
    speaker_label: 'Speaker 1',
    text: 'Привет, мир',
    is_short: false,
    overlaps: false,
    is_edited: false,
    keep_original: false,
    version: 1,
    has_original_audio: true,
    words: null,
    assigned_participant_id: null,
    assigned_display_name: null,
    assigned_expires_at: null,
    has_recording: false,
    current_recording_id: null,
    recording_duration_ms: null,
    ...overrides,
  }
}

function renderTable(lines: Line[]) {
  const onChanged = vi.fn()
  const notify = vi.fn()
  render(
    <DialogueTable
      roomId="room1"
      lines={lines}
      speakers={[speaker]}
      participants={participants}
      myId="me"
      activeLineId={null}
      onSeek={vi.fn()}
      onPlayOriginal={vi.fn()}
      onChanged={onChanged}
      notify={notify}
    />,
  )
  return { onChanged, notify }
}

describe('таблица реплик', () => {
  beforeEach(() => {
    localStorage.clear()
    session.save('room1', 'token', 'me', 'Егор')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('показывает текст, таймкод и кто держит реплику', () => {
    renderTable([
      makeLine({ id: 'line-1', text: 'Привет, мир' }),
      makeLine({
        id: 'line-2',
        idx: 1,
        start_ms: 4000,
        end_ms: 7000,
        duration_ms: 3000,
        text: 'Вторая реплика',
        assigned_participant_id: 'other',
        assigned_display_name: 'Аня',
      }),
    ])

    expect(screen.getByText('Привет, мир')).toBeInTheDocument()
    expect(screen.getByText('Вторая реплика')).toBeInTheDocument()
    expect(screen.getByText('00:00.500')).toBeInTheDocument()
    expect(screen.getByText('00:04.000')).toBeInTheDocument()
    expect(screen.getByText('Аня')).toBeInTheDocument()
  })

  it('занятую чужую реплику взять нельзя, свободную — можно', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          line_id: 'line-1',
          participant_id: 'me',
          display_name: 'Егор',
          assigned_at: '2026-01-01T00:00:00Z',
          expires_at: null,
          version: 1,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { onChanged, notify } = renderTable([
      makeLine({ id: 'line-1' }),
      makeLine({ id: 'line-2', idx: 1, assigned_participant_id: 'other', assigned_display_name: 'Аня' }),
    ])

    const claimButtons = screen.getAllByRole('button', { name: 'Взять' })
    expect(claimButtons[0]).toBeEnabled()
    expect(claimButtons[1]).toBeDisabled()

    await userEvent.click(claimButtons[0])
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(notify).toHaveBeenCalledWith('Реплика взята', true)

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/rooms/room1/lines/line-1/assignment')
    expect(init.method).toBe('PUT')
  })

  it('правка текста уходит с expected_version', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(makeLine({ text: 'Новый текст' })), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    renderTable([makeLine({ id: 'line-1', version: 4 })])
    await userEvent.click(screen.getByRole('button', { name: 'Правка' }))
    const area = screen.getByRole('textbox', { name: 'Текст реплики' })
    await userEvent.clear(area)
    await userEvent.type(area, 'Новый текст')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(String(init.body))).toEqual({ text: 'Новый текст', expected_version: 4 })
  })
})
