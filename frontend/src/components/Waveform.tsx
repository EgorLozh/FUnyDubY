/**
 * Волна звука на canvas: оригинал полупрозрачной подложкой, запись — поверх.
 *
 * Ориентир (оригинальная реплика) и своя запись рисуются в одних координатах времени, поэтому
 * видно, попадаешь ли ты в фразу. Во время записи волна дорисовывается слева направо: ширина
 * показывает, какую часть реплики ты уже использовал.
 *
 * Без canvas-контекста (например, в jsdom) компонент молча рисует пустое поле — тесты не падают.
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
  /** Сколько всего колонок по оси времени (для живой волны). */
  buckets?: number
  height?: number
  label?: string
  hint?: string
}

const GHOST_COLOR = 'rgba(148, 163, 184, 0.55)'
const MAIN_COLOR = '#ff6b4a'
const LIVE_COLOR = '#ff3d1f'
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

      // Ось по центру: без неё тишина выглядит как пустое место
      ctx.strokeStyle = AXIS_COLOR
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(0, middle)
      ctx.lineTo(width, middle)
      ctx.stroke()

      const total = Math.max(1, buckets)
      const barWidth = width / total

      const drawBars = (data: Float32Array, color: string, span: number) => {
        const gain = maxOf(data) > 0.0001 ? Math.min(12, 0.95 / maxOf(data)) : 1
        ctx.fillStyle = color
        for (let index = 0; index < data.length; index += 1) {
          const value = Math.min(1, data[index] * gain)
          const barHeight = Math.max(1, value * maxBar)
          const x = index * span
          if (x > width) break
          ctx.fillRect(x, middle - barHeight, Math.max(1, span - 0.5), barHeight * 2)
        }
      }

      // Подложка — оригинал: рисуем первым, чтобы запись легла сверху
      if (ghost && ghost.length > 0) drawBars(ghost, GHOST_COLOR, width / ghost.length)

      const live = active && liveSource ? liveSource() : null
      if (live && live.length > 0) {
        drawBars(live, LIVE_COLOR, barWidth)
        // Отметка «докуда дошёл»: остаток реплики виден как пустое место
        const end = live.length * barWidth
        ctx.strokeStyle = PLAYHEAD_COLOR
        ctx.beginPath()
        ctx.moveTo(end, 2)
        ctx.lineTo(end, height - 2)
        ctx.stroke()
      } else if (peaks && peaks.length > 0) {
        drawBars(peaks, MAIN_COLOR, width / peaks.length)
      }

      if (progress !== null && progress >= 0 && progress <= 1 && (!active || !live)) {
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
  }, [ghost, peaks, liveSource, active, progress, buckets, height])

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
