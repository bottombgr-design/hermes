import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { hiddenPaneProps } from '@/components/pane-shell/pane-visibility'

import { $terminalTakeover } from '../store'

import { PersistentTerminal, TerminalSlot } from './persistent'

vi.mock('./terminals', () => ({ ensureTerminal: vi.fn() }))
vi.mock('./workspace', () => ({ TerminalWorkspace: () => <div data-testid="terminal-workspace" /> }))

const SLOT_RECT = {
  bottom: 220,
  height: 200,
  left: 10,
  right: 410,
  top: 20,
  width: 400,
  x: 10,
  y: 20,
  toJSON: () => ({})
} as DOMRect

describe('PersistentTerminal', () => {
  let frames: FrameRequestCallback[]

  beforeEach(() => {
    frames = []
    $terminalTakeover.set(true)
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(SLOT_RECT)
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      frames.push(callback)

      return frames.length
    })
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
  })

  afterEach(() => {
    cleanup()
    $terminalTakeover.set(false)
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  const harness = (hidden: boolean) => (
    <>
      <div {...hiddenPaneProps(hidden)}>
        <TerminalSlot />
      </div>
      <PersistentTerminal onAddSelectionToChat={() => undefined} />
    </>
  )

  const flushFrame = () => {
    const frame = frames.shift()

    expect(frame).toBeDefined()
    act(() => {
      frame?.(0)
    })
  }

  it('hides the overlay but keeps its workspace mounted when the terminal tab becomes inactive', async () => {
    const view = render(harness(false))
    const overlay = view.container.lastElementChild as HTMLElement

    expect(overlay.style.visibility).toBe('visible')
    expect(overlay.style.pointerEvents).toBe('auto')
    expect(await screen.findByTestId('terminal-workspace')).not.toBeNull()

    view.rerender(harness(true))
    flushFrame()

    expect(overlay.style.visibility).toBe('hidden')
    expect(overlay.style.pointerEvents).toBe('none')
    expect(screen.getByTestId('terminal-workspace')).not.toBeNull()

    view.rerender(harness(false))
    flushFrame()

    expect(overlay.style.visibility).toBe('visible')
    expect(overlay.style.pointerEvents).toBe('auto')
  })
})
