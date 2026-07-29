import { QueryClient } from '@tanstack/react-query'
import { act, cleanup, render } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { createClientSessionState } from '@/lib/chat-runtime'

import { useMessageStream } from './index'

const SID = 'session-1'
let appendAssistantDelta: ((sessionId: string, delta: string) => void) | null = null
let states: Map<string, ClientSessionState>
type UpdateSessionState = (
  sessionId: string,
  updater: (state: ClientSessionState) => ClientSessionState,
  storedSessionId?: string | null
) => ClientSessionState
let updateSessionState: ReturnType<typeof vi.fn<UpdateSessionState>>

function Harness() {
  const activeSessionIdRef = useRef<string | null>(SID)
  const sessionStateByRuntimeIdRef = useRef(states)
  const queryClientRef = useRef(new QueryClient())

  const stream = useMessageStream({
    activeSessionIdRef,
    hydrateFromStoredSession: vi.fn(async () => undefined),
    queryClient: queryClientRef.current,
    refreshHermesConfig: vi.fn(async () => undefined),
    refreshSessions: vi.fn(async () => undefined),
    sessionStateByRuntimeIdRef,
    updateSessionState
  })

  useEffect(() => {
    appendAssistantDelta = stream.appendAssistantDelta
  }, [stream.appendAssistantDelta])

  return null
}

function mountStream() {
  render(<Harness />)
  expect(appendAssistantDelta).not.toBeNull()
}

function assistantText() {
  const message = states.get(SID)?.messages.at(-1)
  const part = message?.parts.at(-1)

  return part?.type === 'text' ? part.text : ''
}

describe('useMessageStream delta flush scheduling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    appendAssistantDelta = null
    states = new Map()
    updateSessionState = vi.fn((sessionId: string, updater: (state: ClientSessionState) => ClientSessionState) => {
      const next = updater(states.get(sessionId) ?? createClientSessionState())
      states.set(sessionId, next)

      return next
    })
    vi.spyOn(performance, 'now').mockReturnValue(100)
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1)
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined)
    vi.spyOn(document, 'hasFocus').mockReturnValue(false)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('flushes streaming text on a bounded timer while the window is unfocused', async () => {
    mountStream()

    act(() => appendAssistantDelta!(SID, 'still streaming'))

    expect(window.requestAnimationFrame).not.toHaveBeenCalled()
    expect(assistantText()).toBe('')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(assistantText()).toBe('still streaming')
  })

  it('cancels the pending timer on unmount and flushes exactly once', async () => {
    vi.mocked(performance.now).mockReturnValue(0)
    mountStream()

    act(() => appendAssistantDelta!(SID, 'final delta'))
    expect(vi.getTimerCount()).toBe(1)

    cleanup()

    expect(vi.getTimerCount()).toBe(0)
    expect(assistantText()).toBe('final delta')
    const updatesAfterUnmount = updateSessionState.mock.calls.length

    await vi.advanceTimersByTimeAsync(100)

    expect(updateSessionState).toHaveBeenCalledTimes(updatesAfterUnmount)
    expect(window.requestAnimationFrame).not.toHaveBeenCalled()
  })

  it('stretches the flush gap when the deferred commit frame is expensive', async () => {
    // The streaming-path $messages publish (React commit + Streamdown
    // re-parse) is deferred to a view-sync rAF inside updateSessionState, so
    // the flush cost must be measured through that frame. Simulate one
    // expensive frame and expect the next gap to adapt to 3x the frame cost.
    let now = 1000
    vi.mocked(performance.now).mockImplementation(() => now)
    const rafCallbacks: FrameRequestCallback[] = []
    vi.mocked(window.requestAnimationFrame).mockImplementation(cb => {
      rafCallbacks.push(cb)

      return rafCallbacks.length
    })

    mountStream()

    act(() => appendAssistantDelta!(SID, 'first'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(assistantText()).toBe('first')
    expect(rafCallbacks).toHaveLength(1)

    // Frame started at 1040, the measurement callback runs at 1100: 60ms of
    // in-frame work (view sync + commit), so the next floor is 180ms.
    now = 1100
    act(() => rafCallbacks[0](1040))

    act(() => appendAssistantDelta!(SID, 'second'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(79)
    })

    expect(assistantText()).toBe('first')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })

    expect(assistantText()).toBe('firstsecond')
  })

  it('keeps the write-cost floor when no frame fires (hidden renderer)', async () => {
    // A parked renderer never runs rAF callbacks. The cost must stay at the
    // synchronous store-write measurement so the gap falls back to the fixed
    // 33ms floor instead of waiting on a frame that will never come.
    let now = 1000
    vi.mocked(performance.now).mockImplementation(() => now)
    vi.mocked(window.requestAnimationFrame).mockImplementation(() => 1)

    mountStream()

    act(() => appendAssistantDelta!(SID, 'first'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(assistantText()).toBe('first')

    // 100ms later (well past the 33ms floor): the next flush is immediate.
    now = 1100
    act(() => appendAssistantDelta!(SID, 'second'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(assistantText()).toBe('firstsecond')
  })

  it('ignores a late frame measurement once a newer flush has started', async () => {
    let now = 1000
    vi.mocked(performance.now).mockImplementation(() => now)
    const rafCallbacks: FrameRequestCallback[] = []
    vi.mocked(window.requestAnimationFrame).mockImplementation(cb => {
      rafCallbacks.push(cb)

      return rafCallbacks.length
    })

    mountStream()

    act(() => appendAssistantDelta!(SID, 'a'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    // A second flush starts before the first flush's frame lands.
    now = 1010
    act(() => appendAssistantDelta!(SID, 'b'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(23)
    })

    expect(assistantText()).toBe('ab')
    expect(rafCallbacks).toHaveLength(2)

    // The stale callback must not overwrite the newer flush's cost. If it
    // did, cost would read 30ms and the next gap would stretch to 70ms.
    now = 1030
    act(() => rafCallbacks[0](1000))

    act(() => appendAssistantDelta!(SID, 'c'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(13)
    })

    expect(assistantText()).toBe('abc')
  })
})
