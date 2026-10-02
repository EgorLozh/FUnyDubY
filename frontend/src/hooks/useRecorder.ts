/**
 * Запись реплики с микрофона.
 *
 * Особенности, которые важны для озвучки:
 * - **Разгон** (по умолчанию 3 секунды): запись начинается сразу по нажатию, но первые секунды
 *   намеренно уходят в мусор — за это время участник собирается и начинает вместе с оригиналом.
 *   Сервер обрезает разгон, поэтому в реплику он не попадает, а волна записи совпадает с волной
 *   оригинала.
 * - **Регулировка громкости микрофона**: тихий микрофон тянем вверх узлом усиления, громкий —
 *   приглушаем. Усилитель стоит до рекордера, поэтому в файл попадает уже выправленный звук,
 *   и индикатор уровня показывает то же, что запишется.
 * - **Автостоп ровно на длительности реплики**: сервер повторно проверяет длительность.
 * - **Живая волна**: накапливаем по колонке на долю реплики, чтобы ширина волны совпадала
 *   с осью времени оригинала.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { peakOf } from '../audio/peaks'

export type RecorderStatus =
  | 'idle'
  | 'requesting'
  | 'recording'
  | 'uploading'
  | 'ready'
  | 'error'

export type Recorder = {
  status: RecorderStatus
  elapsedMs: number
  /** Сколько осталось до конца «разгона»: пока больше нуля, реплика ещё не пишется. */
  leadInLeftMs: number
  level: number
  error: string | null
  limitMs: number
  start: () => Promise<void>
  stop: () => void
  cancel: () => void
  reset: () => void
  /** Накопленная волна текущей записи: колонки слева направо, как по оси времени. */
  getLivePeaks: () => Float32Array
}

type Options = {
  limitMs: number
  onRecorded: (blob: Blob, mimeType: string) => Promise<void> | void
  /** Сколько колонок волны набираем за всю реплику (совпадает с числом колонок подложки). */
  buckets?: number
  /** «Разгон»: сколько первых миллисекунд записи отбросить, мс. 0 — писать сразу в реплику. */
  leadInMs?: number
  /** Усиление микрофона: 1 — как есть, меньше — тише, больше — громче. */
  gain?: number
}

const CANDIDATE_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
]

export function pickMimeType(isSupported: (type: string) => boolean): string | null {
  for (const type of CANDIDATE_MIME_TYPES) {
    if (isSupported(type)) return type
  }
  return null
}

export function extensionFor(mimeType: string): string {
  if (mimeType.includes('ogg')) return 'ogg'
  if (mimeType.includes('mp4')) return 'm4a'
  if (mimeType.includes('wav')) return 'wav'
  return 'webm'
}

export function useRecorder({
  limitMs,
  onRecorded,
  buckets = 480,
  leadInMs = 0,
  gain = 1,
}: Options): Recorder {
  const [status, setStatus] = useState<RecorderStatus>('idle')
  const [elapsedMs, setElapsedMs] = useState(0)
  const [leadInLeftMs, setLeadInLeftMs] = useState(0)
  const [level, setLevel] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const audioCtx = useRef<AudioContext | null>(null)
  const chunks = useRef<Blob[]>([])
  const mimeType = useRef<string | null>(null)
  const startedAt = useRef(0)
  /** Момент, с которого пишется сама реплика (начало записи плюс разгон). */
  const takeStartAt = useRef(0)
  const timer = useRef<number | null>(null)
  const frame = useRef<number | null>(null)
  const autoStop = useRef<number | null>(null)
  const cancelled = useRef(false)
  const onRecordedRef = useRef(onRecorded)
  // Волна текущей записи: одна колонка на долю реплики, поэтому ширина растёт вместе с временем
  const liveBars = useRef<number[]>([])
  const pendingPeak = useRef(0)

  onRecordedRef.current = onRecorded

  const cleanup = useCallback(() => {
    if (timer.current !== null) window.clearInterval(timer.current)
    if (frame.current !== null) cancelAnimationFrame(frame.current)
    if (autoStop.current !== null) window.clearTimeout(autoStop.current)
    timer.current = null
    frame.current = null
    autoStop.current = null
    if (recorder.current && recorder.current.state !== 'inactive') {
      try {
        recorder.current.stop()
      } catch {
        /* уже остановлен */
      }
    }
    recorder.current = null
    if (stream.current) {
      for (const track of stream.current.getTracks()) track.stop()
      stream.current = null
    }
    if (audioCtx.current) {
      void audioCtx.current.close().catch(() => undefined)
      audioCtx.current = null
    }
  }, [])

  useEffect(() => cleanup, [cleanup])

  const stop = useCallback(() => {
    // Стоп во время разгона: реплика ещё не началась, сохранять нечего — это отмена.
    if (Date.now() < takeStartAt.current) {
      cancelled.current = true
    }
    if (recorder.current && recorder.current.state !== 'inactive') {
      recorder.current.stop()
    }
  }, [])

  const cancel = useCallback(() => {
    cancelled.current = true
    cleanup()
    setLeadInLeftMs(0)
    setStatus('idle')
    setElapsedMs(0)
    setLevel(0)
  }, [cleanup])

  const start = useCallback(async () => {
    setError(null)
    cancelled.current = false
    setStatus('requesting')
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      })

      // Микрофон может вернуться уже отключённым (устройство пропало, доступ отозван):
      // без этой проверки запись молча получится пустой.
      const tracks = media.getAudioTracks()
      if (tracks.length === 0 || tracks.every((track) => track.readyState !== 'live')) {
        for (const track of media.getTracks()) track.stop()
        throw new Error('Микрофон не отдаёт звук — проверьте, что устройство подключено')
      }

      stream.current = media

      // Один аудиоконтекст: усиление микрофона, индикатор уровня и поток для рекордера.
      const ctx = new AudioContext()
      audioCtx.current = ctx
      const source = ctx.createMediaStreamSource(media)
      const gainNode = ctx.createGain()
      gainNode.gain.value = Math.min(4, Math.max(0.1, gain))
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 1024
      const dest = ctx.createMediaStreamDestination()
      source.connect(gainNode)
      gainNode.connect(analyser)
      gainNode.connect(dest)

      const recordingStream = dest.stream
      mimeType.current = pickMimeType((type) => MediaRecorder.isTypeSupported(type))
      const instance = mimeType.current
        ? new MediaRecorder(recordingStream, {
            mimeType: mimeType.current,
            audioBitsPerSecond: 96000,
          })
        : new MediaRecorder(recordingStream)
      chunks.current = []
      liveBars.current = []
      pendingPeak.current = 0
      instance.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.current.push(event.data)
      }
      instance.onstop = async () => {
        const type = instance.mimeType || mimeType.current || 'audio/webm'
        const blob = new Blob(chunks.current, { type })
        const wasCancelled = cancelled.current
        cleanup()
        setLevel(0)
        if (wasCancelled) {
          // Отмена (в том числе стоп во время разгона): тейка нет, состояние — в покое.
          setStatus('idle')
          setElapsedMs(0)
          setLeadInLeftMs(0)
          return
        }
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
      // Реплика начинается после разгона: до этого момента запись идёт «в мусор»
      takeStartAt.current = Date.now() + leadInMs
      setElapsedMs(0)
      setLeadInLeftMs(leadInMs)
      setStatus('recording')
      instance.start(250)

      // Индикатор уровня и живая волна: показываем, что микрофон реально слышит.
      const buffer = new Float32Array(analyser.fftSize)
      const tick = () => {
        analyser.getFloatTimeDomainData(buffer)
        let sum = 0
        for (const sample of buffer) sum += sample * sample
        setLevel(Math.min(1, Math.sqrt(sum / buffer.length) * 4))

        // Волна: колонки начинаем копить только после разгона, иначе своя запись уезжала бы
        // вправо относительно оригинала, а звук разгона в реплику не попадает вовсе.
        const peak = peakOf(buffer)
        const takeElapsed = Date.now() - takeStartAt.current
        if (takeElapsed > 0) {
          if (peak > pendingPeak.current) pendingPeak.current = peak
        }
        const elapsed = Math.max(0, takeElapsed)
        const expected = Math.min(buckets, Math.round((buckets * elapsed) / limitMs))
        while (liveBars.current.length < expected) {
          liveBars.current.push(pendingPeak.current)
          pendingPeak.current = 0
        }
        frame.current = requestAnimationFrame(tick)
      }
      frame.current = requestAnimationFrame(tick)

      timer.current = window.setInterval(() => {
        const now = Date.now()
        setLeadInLeftMs(Math.max(0, takeStartAt.current - now))
        setElapsedMs(Math.max(0, now - takeStartAt.current))
      }, 100)

      // Жёсткий автостоп: разгон плюс длительность реплики, и ни миллисекунды больше.
      autoStop.current = window.setTimeout(() => {
        stop()
      }, limitMs + leadInMs)
    } catch (exc) {
      cleanup()
      setStatus('error')
      const message = exc instanceof Error ? exc.message : ''
      if (exc instanceof Error && exc.name === 'NotAllowedError') {
        setError('Доступ к микрофону запрещён — разрешите его в браузере')
      } else if (message.startsWith('Микрофон')) {
        setError(message)
      } else {
        setError('Не удалось получить доступ к микрофону')
      }
    }
  }, [buckets, cleanup, gain, leadInMs, limitMs, stop])

  const getLivePeaks = useCallback(() => Float32Array.from(liveBars.current), [])

  const reset = useCallback(() => {
    cancelled.current = true
    cleanup()
    cancelled.current = false
    setStatus('idle')
    setElapsedMs(0)
    setLeadInLeftMs(0)
    setLevel(0)
    setError(null)
    liveBars.current = []
    pendingPeak.current = 0
  }, [cleanup])

  return {
    status,
    elapsedMs,
    leadInLeftMs,
    level,
    error,
    limitMs,
    start,
    stop,
    cancel,
    reset,
    getLivePeaks,
  }
}
