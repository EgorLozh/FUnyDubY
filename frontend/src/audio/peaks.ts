/**
 * Волна звука для интерфейса: считаем пики по колонкам и рисуем их на canvas.
 *
 * Зачем: при озвучке важно видеть, где фраза начинается и заканчивается. Оригинал рисуем
 * полупрозрачной подложкой, свою запись — поверх, в тех же координатах времени, поэтому видно,
 * попадаешь ли ты в реплику. Живую запись дорисовываем по ходу, из данных анализатора.
 */

/** Сколько колонок (столбиков) считаем по умолчанию: примерно пиксель на колонку. */
export const DEFAULT_BUCKETS = 480

/**
 * Пик по каждой колонке: берём максимум модуля внутри колонки.
 *
 * Максимум (а не среднее) — потому что волна нужна как ориентир по громкости: тихие участки
 * должны выглядеть тихими, а не размазываться соседями.
 */
export function computePeaks(samples: Float32Array, buckets: number = DEFAULT_BUCKETS): Float32Array {
  const size = Math.max(1, Math.floor(buckets))
  const peaks = new Float32Array(size)
  if (samples.length === 0) return peaks
  const perBucket = samples.length / size
  for (let bucket = 0; bucket < size; bucket += 1) {
    const start = Math.floor(bucket * perBucket)
    const end = Math.max(start + 1, Math.floor((bucket + 1) * perBucket))
    let peak = 0
    for (let index = start; index < end && index < samples.length; index += 1) {
      const value = Math.abs(samples[index])
      if (value > peak) peak = value
    }
    peaks[bucket] = peak
  }
  return peaks
}

/** Пик куска живой записи — одна колонка текущего кадра. */
export function peakOf(chunk: Float32Array): number {
  let peak = 0
  for (let index = 0; index < chunk.length; index += 1) {
    const value = Math.abs(chunk[index])
    if (value > peak) peak = value
  }
  return peak
}

/**
 * Привести колонки к общему максимуму, чтобы тихая запись не выглядела плоской линией.
 * Возвращаем множитель, а не новые данные: сами пики нужны неизменными для сравнения.
 */
export function normalizeGain(peaks: Float32Array | null): number {
  if (!peaks || peaks.length === 0) return 1
  let max = 0
  for (const value of peaks) if (value > max) max = value
  if (max <= 0.0001) return 1
  return Math.min(12, 0.95 / max)
}
