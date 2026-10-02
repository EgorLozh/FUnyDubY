/**
 * Тесты компонента волны. Canvas в jsdom не реализован, поэтому проверяем главное: компонент
 * не падает без контекста рисования (иначе панель записи ломалась бы в окружениях без canvas)
 * и честно показывает подписи-ориентиры.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Waveform } from './Waveform'

describe('Waveform', () => {
  it('рисует поле и подписи ориентира', () => {
    render(
      <Waveform
        ghost={new Float32Array([0.2, 0.5, 0.9])}
        peaks={new Float32Array([0.1, 0.4, 0.8])}
        label="ваша запись"
        hint="бледная волна — оригинал реплики"
      />,
    )
    expect(screen.getByText('ваша запись')).toBeTruthy()
    expect(screen.getByText('бледная волна — оригинал реплики')).toBeTruthy()
    expect(document.querySelector('canvas')).toBeTruthy()
  })

  it('не падает без данных и в режиме живой записи', () => {
    const { container } = render(
      <Waveform
        liveSource={() => new Float32Array([0.3, 0.6])}
        active
        leadInFraction={0.5}
        ghost={new Float32Array([0.2, 0.5])}
        label="● идёт запись"
      />,
    )
    expect(container.querySelector('canvas')).toBeTruthy()
    expect(screen.getByText('● идёт запись')).toBeTruthy()
  })

  it('выдерживает пустые волны', () => {
    const { container } = render(<Waveform ghost={null} peaks={null} />)
    expect(container.querySelector('canvas')).toBeTruthy()
  })
})
