/** Рекордер: автостоп по длительности реплики, ручной стоп и обработка пустой записи. */

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { extensionFor, pickMimeType, useRecorder } from './useRecorder'

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = []
  state: 'inactive' | 'recording' = 'inactive'
  mimeType: string
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  /** Следующий экземпляр стартует «с пустым микрофоном» — воспроизводит тишину. */
  static nextIsEmpty = false

  constructor(
    public stream: unknown,
    public options?: { mimeType?: string },
  ) {
    this.mimeType = options?.mimeType ?? 'audio/webm'
    FakeMediaRecorder.instances.push(this)
  }

  static isTypeSupported(type: string): boolean {
    return type.startsWith('audio/webm')
  }

  start(): void {
    this.state = 'recording'
    const data = FakeMediaRecorder.nextIsEmpty
      ? new Blob([])
      : new Blob(['audio-bytes'], { type: this.mimeType })
    this.ondataavailable?.({ data })
  }

  stop(): void {
    this.state = 'inactive'
    this.onstop?.()
  }
}

function installBrowserStubs(): void {
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  vi.stubGlobal('AudioContext', class {
    createAnalyser() {
      return { fftSize: 0, getFloatTimeDomainData: () => undefined }
    }
    createMediaStreamSource() {
      return { connect: () => undefined }
    }
    close() {
      return Promise.resolve()
    }
  })
  vi.stubGlobal('requestAnimationFrame', () => 0)
  vi.stubGlobal('cancelAnimationFrame', () => undefined)
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: () => undefined }] })),
    },
  })
}

describe('выбор формата записи', () => {
  it('берёт первый поддерживаемый формат', () => {
    expect(pickMimeType((type) => type === 'audio/webm;codecs=opus')).toBe('audio/webm;codecs=opus')
    expect(pickMimeType((type) => type === 'audio/ogg;codecs=opus')).toBe('audio/ogg;codecs=opus')
    expect(pickMimeType(() => false)).toBe('')
  })

  it('определяет расширение файла по типу', () => {
    expect(extensionFor('audio/webm;codecs=opus')).toBe('webm')
    expect(extensionFor('audio/ogg;codecs=opus')).toBe('ogg')
    expect(extensionFor('audio/mp4')).toBe('m4a')
  })
})

describe('хук записи', () => {
  // Прокачивает микрозадачи: обработчик остановки асинхронный, waitFor с фейковыми
  // таймерами зависает, поэтому ждём явно.
  const flush = async () => {
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
  }

  beforeEach(() => {
    vi.useFakeTimers()
    FakeMediaRecorder.instances = []
    FakeMediaRecorder.nextIsEmpty = false
    installBrowserStubs()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('останавливает запись сама на длительности реплики', async () => {
    const onRecorded = vi.fn(async () => undefined)
    const { result } = renderHook(() => useRecorder({ limitMs: 3000, onRecorded }))

    await act(async () => {
      await result.current.start()
    })
    expect(result.current.status).toBe('recording')

    // Сдвигаем время до лимита: рекордер обязан остановиться сам
    await act(async () => {
      vi.advanceTimersByTime(3000)
    })

    await flush()
    expect(onRecorded).toHaveBeenCalledTimes(1)
    const [blob, mime] = onRecorded.mock.calls[0] as unknown as [Blob, string]
    expect(blob.size).toBeGreaterThan(0)
    expect(mime).toContain('audio/webm')
    expect(FakeMediaRecorder.instances[0].state).toBe('inactive')
  })

  it('останавливается вручную раньше лимита', async () => {
    const onRecorded = vi.fn(async () => undefined)
    const { result } = renderHook(() => useRecorder({ limitMs: 5000, onRecorded }))

    await act(async () => {
      await result.current.start()
    })
    await act(async () => {
      vi.advanceTimersByTime(1000)
      result.current.stop()
    })

    await flush()
    expect(onRecorded).toHaveBeenCalled()
  })

  it('сообщает об ошибке, если микрофон ничего не записал', async () => {
    const onRecorded = vi.fn(async () => undefined)
    FakeMediaRecorder.nextIsEmpty = true
    const { result } = renderHook(() => useRecorder({ limitMs: 2000, onRecorded }))

    await act(async () => {
      await result.current.start()
    })
    await act(async () => {
      result.current.stop()
    })

    await flush()
    expect(result.current.status).toBe('error')
    expect(result.current.error).toContain('не записал')
    expect(onRecorded).not.toHaveBeenCalled()
  })
})
