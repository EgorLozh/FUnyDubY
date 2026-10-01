/**
 * Запись с микрофона: чистые функции + хук.
 *
 * Главное требование: **запись не длиннее реплики**. Клиент останавливает запись сам ровно на
 * длительности реплики (сервер допускает небольшой хвост энкодера — `RECORD_TOLERANCE_MS`
 * и проверяет предел ещё раз). Здесь же выбор формата: браузеры расходятся, поэтому берём
 * первый поддерживаемый из webm/opus, ogg/opus, mp4.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

const CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
]

/** Допуск на хвост энкодера — тот же порядок, что и на сервере. */
export const RECORD_TOLERANCE_MS = 250

export function pickMimeType(isSupported: (type: string) => boolean): string {
  for (const candidate of CANDIDATES) {
    if (isSupported(candidate)) return candidate
  }
  return ''
}

export function extensionFor(mimeType: string): string {
  if (mimeType.includes('ogg')) return 'ogg'
  if (mimeType.includes('mp4')) return 'm4a'
  return 'webm'
}

export type RecorderStatus = 'idle' | 'requesting' | 'recording' | 'ready' | 'uploading' | 'error'

export type Recorder = {
  status: RecorderStatus
  elapsedMs: number
  level: number
  error: string | null
  limitMs: number
  start: () => Promise<void>
  stop: () => void
  reset: () => void
}

type Options = {
  limitMs: number
  onRecorded: (blob: Blob, mimeType: string) => Promise<void> | void
}

export function useRecorder({ limitMs, onRecorded }: Options): Recorder {
  const [status, setStatus] = useState<RecorderStatus>('idle')
  const [elapsedMs, setElapsedMs] = useState(0)
  const [level, setLevel] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const recorder = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const stream = useRef<MediaStream | null>(null)
  const mimeType = useRef('')
  const startedAt = useRef(0)
  const timer = useRef<number | null>(null)
  const autoStop = useRef<number | null>(null)
  const audioCtx = useRef<AudioContext | null>(null)
  const frame = useRef<number | null>(null)
  const onRecordedRef = useRef(onRecorded)

  useEffect(() => {
    onRecordedRef.current = onRecorded
  }, [onRecorded])

  const cleanup = useCallback(() => {
    if (timer.current !== null) window.clearInterval(timer.current)
    if (autoStop.current !== null) window.clearTimeout(autoStop.current)
    if (frame.current !== null) cancelAnimationFrame(frame.current)
    timer.current = null
    autoStop.current = null
    frame.current = null
    stream.current?.getTracks().forEach((track) => track.stop())
    stream.current = null
    void audioCtx.current?.close().catch(() => undefined)
    audioCtx.current = null
    recorder.current = null
  }, [])

  useEffect(() => cleanup, [cleanup])

  const stop = useCallback(() => {
    if (recorder.current && recorder.current.state !== 'inactive') {
      recorder.current.stop()
    }
  }, [])

  const start = useCallback(async () => {
    setError(null)
    setStatus('requesting')
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      })
      stream.current = media
      mimeType.current = pickMimeType((type) => MediaRecorder.isTypeSupported(type))
      const instance = mimeType.current
        ? new MediaRecorder(media, { mimeType: mimeType.current, audioBitsPerSecond: 96000 })
        : new MediaRecorder(media)
      chunks.current = []
      instance.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.current.push(event.data)
      }
      instance.onstop = async () => {
        const type = instance.mimeType || mimeType.current || 'audio/webm'
        const blob = new Blob(chunks.current, { type })
        cleanup()
        setLevel(0)
        if (blob.size === 0) {
          setStatus('error')
          setError('Микрофон не записал звук — проверьте доступ и устройство')
          return
        }
        setStatus('uploading')
        try {
          await onRecordedRef.current(blob, type)
          setStatus('ready')
        } catch (exc) {
          setStatus('error')
          setError(exc instanceof Error ? exc.message : 'Не удалось загрузить запись')
        }
      }
      recorder.current = instance
      startedAt.current = Date.now()
      setElapsedMs(0)
      setStatus('recording')
      instance.start(250)

      // Индикатор уровня: показываем, что микрофон реально слышит.
      const ctx = new AudioContext()
      audioCtx.current = ctx
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 1024
      ctx.createMediaStreamSource(media).connect(analyser)
      const buffer = new Float32Array(analyser.fftSize)
      const tick = () => {
        analyser.getFloatTimeDomainData(buffer)
        let sum = 0
        for (const sample of buffer) sum += sample * sample
        setLevel(Math.min(1, Math.sqrt(sum / buffer.length) * 4))
        frame.current = requestAnimationFrame(tick)
      }
      frame.current = requestAnimationFrame(tick)

      timer.current = window.setInterval(() => {
        setElapsedMs(Date.now() - startedAt.current)
      }, 100)

      // Жёсткий автостоп: запись не может быть длиннее реплики.
      autoStop.current = window.setTimeout(() => {
        stop()
      }, limitMs)
    } catch (exc) {
      cleanup()
      setStatus('error')
      setError(
        exc instanceof Error && exc.name === 'NotAllowedError'
          ? 'Доступ к микрофону запрещён — разрешите его в браузере'
          : 'Не удалось получить доступ к микрофону',
      )
    }
  }, [cleanup, limitMs, stop])

  const reset = useCallback(() => {
    cleanup()
    setStatus('idle')
    setElapsedMs(0)
    setLevel(0)
    setError(null)
  }, [cleanup])

  return { status, elapsedMs, level, error, limitMs, start, stop, reset }
}
