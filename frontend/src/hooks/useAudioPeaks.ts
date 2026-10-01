/**
 * Загрузка волны для аудио: скачиваем файл, декодируем и считаем пики.
 *
 * Результат кешируем по адресу: панель «Мои реплики» перерисовывается часто (SSE-события,
 * переключение тейков), а декодирование одного и того же файла каждый раз — лишняя работа
 * и лишние запросы к серверу.
 */

import { useCallback, useEffect, useState } from 'react'

import { computePeaks, DEFAULT_BUCKETS } from '../audio/peaks'

export type PeaksResult = {
  peaks: Float32Array
  durationMs: number
}

const cache = new Map<string, PeaksResult>()
const pending = new Map<string, Promise<PeaksResult>>()

export async function loadPeaks(url: string, buckets = DEFAULT_BUCKETS): Promise<PeaksResult> {
  const key = `${url}|${buckets}`
  const cached = cache.get(key)
  if (cached) return cached
  const inFlight = pending.get(key)
  if (inFlight) return inFlight

  const task = (async () => {
    const response = await fetch(url)
    if (!response.ok) throw new Error(`Не удалось загрузить звук: ${response.status}`)
    const data = await response.arrayBuffer()
    const ctx = new AudioContext()
    try {
      const buffer = await ctx.decodeAudioData(data)
      const channel = buffer.getChannelData(0)
      const result: PeaksResult = {
        peaks: computePeaks(channel, buckets),
        durationMs: Math.round(buffer.duration * 1000),
      }
      cache.set(key, result)
      return result
    } finally {
      void ctx.close().catch(() => undefined)
      pending.delete(key)
    }
  })()

  pending.set(key, task)
  return task
}

/** Сбросить кеш (например, после перезаписи реплики — файл по тому же адресу изменился). */
export function invalidatePeaks(url?: string): void {
  if (!url) {
    cache.clear()
    return
  }
  for (const key of [...cache.keys()]) {
    if (key.startsWith(url)) cache.delete(key)
  }
}

export function useAudioPeaks(url: string | null, buckets = DEFAULT_BUCKETS) {
  const [result, setResult] = useState<PeaksResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!url) {
      setResult(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      setResult(await loadPeaks(url, buckets))
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Не удалось построить волну')
    } finally {
      setLoading(false)
    }
  }, [url, buckets])

  useEffect(() => {
    void load()
  }, [load])

  return { result, loading, error, reload: load }
}
