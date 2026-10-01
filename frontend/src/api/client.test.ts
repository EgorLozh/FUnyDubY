/** Проверки клиента: разбор ошибок problem+json и заголовок с токеном участника. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, session } from './client'
import { ApiError } from '../types'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('клиент API', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('разбирает 409 line_taken и подсказывает, кто держит реплику', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          { code: 'line_taken', title: 'Реплика уже занята', status: 409, display_name: 'Аня' },
          409,
        ),
      ),
    )

    await expect(api.claimLine('room1', 'line1')).rejects.toBeInstanceOf(ApiError)
    try {
      await api.claimLine('room1', 'line1')
    } catch (exc) {
      expect((exc as ApiError).code).toBe('line_taken')
      expect((exc as ApiError).hint).toContain('Аня')
    }
  })

  it('отправляет токен участника заголовком и тело назначения', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        line_id: 'line1',
        participant_id: 'p1',
        display_name: 'Егор',
        assigned_at: '2026-01-01T00:00:00Z',
        expires_at: null,
        version: 1,
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    session.save('room1', 'secret-token', 'p1', 'Егор')

    await api.claimLine('room1', 'line1', 'p2')

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/rooms/room1/lines/line1/assignment')
    expect(init.method).toBe('PUT')
    expect((init.headers as Record<string, string>)['X-Participant-Token']).toBe('secret-token')
    expect(JSON.parse(String(init.body))).toEqual({ participant_id: 'p2' })
  })

  it('фильтр «мои реплики» уходит в параметр assigned_to', async () => {
    const fetchMock = vi.fn(async () => jsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await api.listLines('room1', { assignedTo: 'me' })

    const [url] = fetchMock.mock.calls[0] as unknown as [string]
    expect(url).toContain('assigned_to=me')
  })
})
