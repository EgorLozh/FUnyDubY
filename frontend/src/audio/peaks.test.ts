/**
 * Тесты волны: пики по колонкам, пик куска для живой записи, выравнивание громкости.
 * Волна — это то, по чему участник понимает, попадает ли он в реплику, поэтому проверяем
 * границы: тишина, одиночный всплеск, ровный сигнал и короткие входные данные.
 */

import { describe, expect, it } from 'vitest'

import { computePeaks, DEFAULT_BUCKETS, normalizeGain, peakOf } from './peaks'

describe('computePeaks', () => {
  it('на тишине даёт нули', () => {
    const peaks = computePeaks(new Float32Array(1000), 10)
    expect(peaks).toHaveLength(10)
    expect(Array.from(peaks).every((value) => value === 0)).toBe(true)
  })

  it('ловит всплеск в нужной колонке по времени', () => {
    const samples = new Float32Array(1000)
    samples[750] = 0.8 // три четверти длины — значит восьмая колонка из десяти
    const peaks = computePeaks(samples, 10)
    expect(peaks[7]).toBeCloseTo(0.8, 5)
    expect(peaks[0]).toBe(0)
    expect(peaks[9]).toBe(0)
  })

  it('на ровном сигнале все колонки заполнены', () => {
    const samples = new Float32Array(500).fill(0.5)
    const peaks = computePeaks(samples, 50)
    expect(Array.from(peaks).every((value) => Math.abs(value - 0.5) < 1e-6)).toBe(true)
  })

  it('не падает на пустом входе и на числе колонок меньше единицы', () => {
    expect(computePeaks(new Float32Array(0), 10)).toHaveLength(10)
    expect(computePeaks(new Float32Array(10).fill(0.2), 0)).toHaveLength(1)
  })

  it('по умолчанию столько колонок, сколько ждёт подложка', () => {
    expect(computePeaks(new Float32Array(100)).length).toBe(DEFAULT_BUCKETS)
  })
})

describe('peakOf', () => {
  it('берёт максимум по модулю — знак не важен', () => {
    expect(peakOf(new Float32Array([0.1, -0.7, 0.3]))).toBeCloseTo(0.7, 6)
  })

  it('на пустом куске даёт ноль', () => {
    expect(peakOf(new Float32Array(0))).toBe(0)
  })
})

describe('normalizeGain', () => {
  it('поднимает тихую запись, но не бесконечно', () => {
    const quiet = new Float32Array([0.02, 0.01])
    const gain = normalizeGain(quiet)
    expect(gain).toBeGreaterThan(1)
    expect(gain * 0.02).toBeLessThanOrEqual(1)
    expect(gain).toBeLessThanOrEqual(12)
  })

  it('громкую запись не усиливает', () => {
    expect(normalizeGain(new Float32Array([0.9, 0.5]))).toBeLessThanOrEqual(1.06)
  })

  it('на тишине и пустых данных даёт единицу', () => {
    expect(normalizeGain(new Float32Array(100))).toBe(1)
    expect(normalizeGain(null)).toBe(1)
  })
})
