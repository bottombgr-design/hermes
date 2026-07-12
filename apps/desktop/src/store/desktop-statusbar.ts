import { atom } from 'nanostores'

import { getHermesConfigRecord, saveHermesConfig } from '@/hermes'
import type { HermesConfigRecord } from '@/types/hermes'

export type DesktopStatusbarMode = 'auto-hide' | 'off' | 'on'

type VisibleDesktopStatusbarMode = Exclude<DesktopStatusbarMode, 'off'>

export const DEFAULT_DESKTOP_STATUSBAR_MODE: VisibleDesktopStatusbarMode = 'on'

export const $desktopStatusbarMode = atom<DesktopStatusbarMode>(DEFAULT_DESKTOP_STATUSBAR_MODE)

let lastVisibleMode: VisibleDesktopStatusbarMode = DEFAULT_DESKTOP_STATUSBAR_MODE

export function normalizeDesktopStatusbarMode(value: unknown): DesktopStatusbarMode {
  return value === 'off' || value === 'auto-hide' || value === 'on' ? value : DEFAULT_DESKTOP_STATUSBAR_MODE
}

function publishDesktopStatusbarMode(mode: DesktopStatusbarMode): void {
  if (mode !== 'off') {
    lastVisibleMode = mode
  }

  $desktopStatusbarMode.set(mode)
}

export function applyDesktopStatusbarFromConfig(
  config: { display?: { desktop_statusbar?: unknown } | null } | null | undefined
): void {
  const mode = normalizeDesktopStatusbarMode(config?.display?.desktop_statusbar)

  // An off value loaded for a profile does not encode that profile's previous
  // visible mode. Reset the restore target instead of leaking another
  // profile's in-memory choice across a live profile switch.
  if (mode === 'off') {
    lastVisibleMode = DEFAULT_DESKTOP_STATUSBAR_MODE
    $desktopStatusbarMode.set(mode)

    return
  }

  publishDesktopStatusbarMode(mode)
}

/**
 * Persist the profile-scoped Desktop status bar preference. The atom updates
 * optimistically so the chrome responds immediately, then rolls back if the
 * whole-record config write fails.
 */
export async function persistDesktopStatusbarMode(mode: DesktopStatusbarMode): Promise<HermesConfigRecord> {
  const previous = $desktopStatusbarMode.get()
  const previousLastVisibleMode = lastVisibleMode

  if (previous !== mode) {
    publishDesktopStatusbarMode(mode)
  }

  try {
    const record = await getHermesConfigRecord()

    const display =
      record.display && typeof record.display === 'object' && !Array.isArray(record.display)
        ? (record.display as Record<string, unknown>)
        : {}

    const next = { ...record, display: { ...display, desktop_statusbar: mode } }

    await saveHermesConfig(next)

    return next
  } catch (error) {
    $desktopStatusbarMode.set(previous)
    lastVisibleMode = previousLastVisibleMode

    throw error
  }
}

/**
 * Shared whole-bar toggle used by the keybind, command palette, and context
 * menu. Restoring the bar returns to the last visible mode, so auto-hide is not
 * silently replaced with always-on after a temporary hide.
 */
export async function toggleDesktopStatusbarVisible(): Promise<HermesConfigRecord> {
  const current = $desktopStatusbarMode.get()

  return persistDesktopStatusbarMode(current === 'off' ? lastVisibleMode : 'off')
}
