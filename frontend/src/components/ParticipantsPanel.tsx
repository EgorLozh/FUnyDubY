/** Участники комнаты: кто есть, сколько реплик взял, массовый захват реплик. */

import { useState } from 'react'

import { api } from '../api/client'
import type { ApiError, Line, Participant, Speaker } from '../types'

const ONLINE_MS = 60_000

export function ParticipantsPanel({
  roomId,
  participants,
  lines,
  speakers,
  myId,
  onChanged,
  notify,
}: {
  roomId: string
  participants: Participant[]
  lines: Line[]
  speakers: Speaker[]
  myId: string | null
  onChanged: () => void
  notify: (message: string, ok?: boolean) => void
}) {
  const [speakerKey, setSpeakerKey] = useState('')
  const [busy, setBusy] = useState(false)

  const heldBy = (participantId: string) =>
    lines.filter((line) => line.assigned_participant_id === participantId).length

  async function bulk(payload: {
    scope: 'all-unassigned' | 'my-speaker'
    speaker_key?: string
  }) {
    setBusy(true)
    try {
      const result = await api.bulkClaim(roomId, payload)
      const busyCount = result.busy.length
      notify(
        `Взято реплик: ${result.captured.length}` +
          (result.already_mine.length ? `, уже было ${result.already_mine.length}` : '') +
          (busyCount ? `, занято другими ${busyCount}` : ''),
        true,
      )
      onChanged()
    } catch (exc) {
      notify((exc as ApiError).hint)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel-body">
      <div className="people">
        {participants.map((person) => {
          const online =
            person.last_seen_at !== null &&
            Date.now() - new Date(person.last_seen_at).getTime() < ONLINE_MS
          return (
            <div className="person" key={person.id}>
              <span className="swatch" style={{ background: person.color }} />
              <span style={{ flex: 1 }}>
                {person.display_name}
                {person.is_creator && <span className="badge" style={{ marginLeft: '0.4rem' }}>создатель</span>}
                {person.id === myId && <span className="badge ok" style={{ marginLeft: '0.4rem' }}>вы</span>}
              </span>
              <span className="muted small">реплик: {heldBy(person.id)}</span>
              <span className={`badge${online ? ' ok' : ''}`}>{online ? 'в сети' : 'оффлайн'}</span>
            </div>
          )
        })}
        {participants.length === 0 && <div className="empty">Пока никого</div>}
      </div>

      <div className="card" style={{ marginTop: '1rem' }}>
        <div className="small muted" style={{ marginBottom: '0.5rem' }}>
          Забрать реплики себе. Реплики, которые уже взял кто-то другой, останутся у него —
          сервер вернёт их отдельным списком.
        </div>
        <div className="row">
          <button className="primary" disabled={busy} onClick={() => void bulk({ scope: 'all-unassigned' })}>
            Взять все свободные
          </button>
          <select value={speakerKey} onChange={(event) => setSpeakerKey(event.target.value)}>
            <option value="">— спикер —</option>
            {speakers.map((speaker) => (
              <option key={speaker.speaker_key} value={speaker.speaker_key}>
                {speaker.speaker_label} ({speaker.lines})
              </option>
            ))}
          </select>
          <button
            disabled={busy || !speakerKey}
            onClick={() => void bulk({ scope: 'my-speaker', speaker_key: speakerKey })}
          >
            Взять все реплики спикера
          </button>
        </div>
      </div>
    </div>
  )
}
