/**
 * Плеер с привязкой к репликам.
 *
 * Синхронный просмотр между участниками не нужен (так решено в архитектуре), но нужен
 * удобный переход: клик по реплике — переход к её началу, «прослушать реплику» — короткое
 * воспроизведение ровно её интервала с остановкой на конце.
 */

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'

import { media } from '../api/client'
import type { Line } from '../types'
export type PlayerHandle = {
  seekTo: (ms: number) => void
  playRange: (startMs: number, endMs: number) => void
  playOriginal: (lineId: string) => void
  stop: () => void
}

type Props = {
  roomId: string
  lines: Line[]
  onTimeUpdate: (ms: number) => void
}

export const VideoPlayer = forwardRef<PlayerHandle, Props>(function VideoPlayer(
  { roomId, lines, onTimeUpdate },
  ref,
) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [currentMs, setCurrentMs] = useState(0)
  const stopAt = useRef<number | null>(null)
  const durationMs = lines.length ? lines[lines.length - 1].end_ms : 0

  useImperativeHandle(ref, () => ({
    seekTo(ms: number) {
      const video = videoRef.current
      if (!video) return
      video.currentTime = Math.max(0, ms / 1000)
    },
    playRange(startMs: number, endMs: number) {
      const video = videoRef.current
      if (!video) return
      stopAt.current = endMs
      video.currentTime = Math.max(0, startMs / 1000)
      void video.play()
    },
    playOriginal(lineId: string) {
      audioRef.current?.pause()
      const audio = new Audio(media.url(roomId, 'original', lineId))
      audioRef.current = audio
      void audio.play()
    },
    stop() {
      stopAt.current = null
      videoRef.current?.pause()
      audioRef.current?.pause()
    },
  }))

  useEffect(() => {
    return () => {
      audioRef.current?.pause()
      audioRef.current = null
    }
  }, [])

  const activeLine = lines.find((line) => currentMs >= line.start_ms && currentMs < line.end_ms)

  return (
    <div className="panel">
      <div className="player">
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <video
          ref={videoRef}
          src={media.videoUrl(roomId)}
          controls
          preload="metadata"
          onTimeUpdate={(event) => {
            const video = event.currentTarget
            const ms = video.currentTime * 1000
            if (stopAt.current !== null && ms >= stopAt.current) {
              video.pause()
              stopAt.current = null
            }
            setCurrentMs(ms)
            onTimeUpdate(ms)
          }}
        />
        {activeLine && <div className="subtitle">{activeLine.text}</div>}
      </div>
      <div className="player-bar">
        <span className="time">{formatMs(currentMs)}</span>
        <input
          type="range"
          min={0}
          max={Math.max(1, durationMs)}
          value={Math.min(currentMs, durationMs)}
          onChange={(event) => {
            const ms = Number(event.target.value)
            if (videoRef.current) videoRef.current.currentTime = ms / 1000
          }}
          style={{ flex: 1 }}
          aria-label="Позиция в ролике"
        />
        <span className="time">{formatMs(durationMs)}</span>
      </div>
    </div>
  )
})

export function formatMs(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

export function formatPrecise(ms: number): string {
  const seconds = Math.floor(ms / 1000)
  const rest = Math.floor(ms % 1000)
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}.${String(rest).padStart(3, '0')}`
}
