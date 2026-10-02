/**
 * Волна звука: своя запись поверх бледной волны оригинала.
 *
 * Во время записи ось времени — вся запись целиком, включая «разгон»: собственная волна растёт
 * слева направо без остановок, а бледная волна оригинала сдвинута вправо ровно на длину разгона.
 * Так видно, что разгон — часть записи, но в реплику он не попадёт: под ним оригинала нет.
 */

import { useEffect, useRef } from 'react'

export type WaveformProps = {
  /** Оригинальная реплика — бледная подложка. */
  ghost?: Float32Array | null
  /** Своя запись (актуальный тейк). */
  peaks?: Float32Array | null
  /** Живая запись: функция отдаёт накопленные колонки. */
  liveSource?: (() => Float32Array) | null
  /** Идёт запись — перерисовываем каждый кадр. */
  active?: boolean
  /** Позиция прослушивания, 0..1. */
  progress?: number | null
  /**
   * Доля «разгона» в общей длине записи (0..1). Только во время записи: подложка и своя волна
   * сдвигаются на эту долю, потому что начало записи в реплику не входит.
   */
  leadInFraction?: number | null
  /** Сколько всего колонок по оси времени (для живой волны). */
  buckets?: number
  height?: number
  label?: string
  hint?: string
}

const GHOST_COLOR = 'rgba(148, 163, 184, 0.45)'
const GHOST_EDGE = 'rgba(148, 163, 184, 0.9)'
const MAIN_COLOR = '#ff6b4a'
const LIVE_COLOR = '#ff3d1f'
const RUNUP_COLOR = 'rgba(255, 61, 31, 0.35)'
const AXIS_COLOR = 'rgba(148, 163, 184, 0.25)'
const PLAYHEAD_COLOR = 'rgba(255, 255, 255, 0.75)'

function maxOf(data: Float32Array): number {
  let max = 0
  for (const value of data) if (value > max) max = value
  return max
}

export function Waveform({
  ghost,
  peaks,
  liveSource,
  active = false,
  progress = null,
  leadInFraction = null,
  buckets = 480,
  height = 56,
  label,
  hint,
}: WaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const frame = useRef<number | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const draw = () => {
      const rect = canvas.getBoundingClientRect()
      const width = Math.max(80, Math.round(rect.width))
      const dpr = window.devicePixelRatio || 1
      if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
        canvas.width = width * dpr
        canvas.height = height * dpr
      }
      // В окружениях без canvas (jsdom) контекст не выдаётся — молча оставляем поле пустым
      let ctx: CanvasRenderingContext2D | null = null
      try {
        ctx = canvas.getContext('2d')
      } catch {
        ctx = null
      }
      if (!ctx) return
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, width, height)

      const middle = height / 2
      const maxBar = middle - 3

      // Во время записи разгон занимает левую долю оси; в покое его нет — вся ширина про реплику
      const lead = active && leadInFraction ? Math.min(0.9, Math.max(0, leadInFraction)) : 0
      const leadWidth = width * lead
      const replicaWidth = width - leadWidth

      ctx.strokeStyle = AXIS_COLOR
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(0, middle)
      ctx.lineTo(width, middle)
      ctx.stroke()

      /**
       * Рисуем колонки. `total` — сколько колонок на всей оси: у живой записи их набирается
       * меньше, чем поместится, и без этого её столбики растягивались на всю ширину, хотя
       * запись только началась.
       */
      const bars = (
        data: Float32Array,
        color: string,
        fromX: number,
        span: number,
        total?: number,
      ) => {
        const peak = maxOf(data)
        const gain = peak > 0.0001 ? Math.min(12, 0.95 / peak) : 1
        const step = span / Math.max(1, total ?? data.length)
        ctx.fillStyle = color
        for (let index = 0; index < data.length; index += 1) {
          const value = Math.min(1, data[index] * gain)
          const barHeight = Math.max(1, value * maxBar)
          ctx.fillRect(fromX + index * step, middle - barHeight, Math.max(1, step - 0.4), barHeight * 2)
        }
      }

      if (ghost && ghost.length > 0) {
        // Подложка: под разгоном её нет — там реплика ещё не пишется
        ctx.save()
        ctx.beginPath()
        ctx.rect(leadWidth, 0, replicaWidth, height)
        ctx.clip()
        bars(ghost, GHOST_COLOR, leadWidth, replicaWidth)
        ctx.restore()
        if (lead > 0) {
          ctx.strokeStyle = GHOST_EDGE
          ctx.lineWidth = 2
          ctx.beginPath()
          ctx.moveTo(leadWidth, 2)
          ctx.lineTo(leadWidth, height - 2)
          ctx.stroke()
        }
      }

      const live = active && liveSource ? liveSource() : null
      if (live && live.length > 0 && lead > 0) {
        // Разгон рисуем приглушённо: это часть записи, но в реплику она не войдёт
        ctx.save()
        ctx.beginPath()
        ctx.rect(0, 0, leadWidth, height)
        ctx.clip()
        bars(live, RUNUP_COLOR, 0, width, buckets)
        ctx.restore()
        ctx.save()
        ctx.beginPath()
        ctx.rect(leadWidth, 0, replicaWidth, height)
        ctx.clip()
        bars(live, LIVE_COLOR, 0, width, buckets)
        ctx.restore()
        // Курсор записи идёт без остановок — по разгону тоже
        const end = (live.length / Math.max(1, buckets)) * width
        ctx.strokeStyle = PLAYHEAD_COLOR
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.moveTo(Math.min(width, end), 2)
        ctx.lineTo(Math.min(width, end), height - 2)
        ctx.stroke()
      } else if (live && live.length > 0) {
        bars(live, LIVE_COLOR, 0, width, buckets)
        const end = (live.length / Math.max(1, buckets)) * width
        ctx.strokeStyle = PLAYHEAD_COLOR
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.moveTo(Math.min(width, end), 2)
        ctx.lineTo(Math.min(width, end), height - 2)
        ctx.stroke()
      } else if (peaks && peaks.length > 0) {
        bars(peaks, MAIN_COLOR, 0, width)
      }

      if (progress !== null && progress >= 0 && progress <= 1 && !active) {
        const x = progress * width
        ctx.strokeStyle = PLAYHEAD_COLOR
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.moveTo(x, 2)
        ctx.lineTo(x, height - 2)
        ctx.stroke()
      }
    }

    draw()

    if (active) {
      const tick = () => {
        draw()
        frame.current = requestAnimationFrame(tick)
      }
      frame.current = requestAnimationFrame(tick)
    }

    const onResize = () => draw()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      if (frame.current !== null) cancelAnimationFrame(frame.current)
      frame.current = null
    }
  }, [ghost, peaks, liveSource, active, progress, leadInFraction, buckets, height])

  return (
    <div className="waveform">
      {(label || hint) && (
        <div className="row small" style={{ justifyContent: 'space-between' }}>
          <span className="muted">{label}</span>
          <span className="muted">{hint}</span>
        </div>
      )}
      <canvas ref={canvasRef} style={{ width: '100%', height }} />
    </div>
  )
}
