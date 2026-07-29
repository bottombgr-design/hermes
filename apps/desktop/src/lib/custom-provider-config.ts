// Pure helpers for reading/writing custom provider entries in Hermes config.
//
// Custom providers live in config.yaml under `custom_providers` (a list of
// { name, base_url, api_key, api_mode, models, enabled, ... }). The desktop
// reads/writes the whole config via getHermesConfig()/saveHermesConfig()
// (PUT /api/config). These helpers are framework-free so they can be unit
// tested without React or the backend.
//
// Provider activation is persisted two ways (see setProviderEnabled):
//   - custom providers: an `enabled: false` flag on the entry (default true)
//   - built-in providers: a `model.disabled_providers: string[]` slug list

import type { HermesConfigRecord } from '@/types/hermes'

export type CustomProviderApiMode = 'chat_completions' | 'anthropic_messages'

/** A single model exposed by a custom provider. */
export interface CustomProviderModel {
  /** Stable model id sent to the backend (e.g. "gpt-4o"). */
  id: string
  /** Optional display name shown in the UI instead of the raw id. */
  name?: string
  /** Optional provider-specific advanced parameters (unused for now). */
  advanced?: Record<string, unknown>
}

export interface CustomProviderEntry {
  /** Display + identity name. Unique after normalization. */
  name: string
  /** OpenAI-compatible (or Anthropic) base URL, e.g. https://my.host/v1 */
  base_url: string
  /** Optional secret API key. Empty string means "leave existing". */
  api_key?: string
  api_mode?: CustomProviderApiMode
  /** Models the user wants this provider to expose. */
  models: CustomProviderModel[]
}

interface RawCustomProvider {
  name?: string
  base_url?: string
  api_key?: string
  api_mode?: string
  enabled?: boolean
  models?: Record<string, unknown> | string[]
}

interface ConfigShape {
  custom_providers?: RawCustomProvider[]
  model?: { disabled_providers?: string[] }
}

// Mirror the backend's _normalize_custom_pool_name: lowercased, spaces -> '-'.
export function normalizeProviderName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
}

/**
 * Generate a clean, unique internal provider id from a display name.
 *
 * The backend `custom_providers` schema has no separate id field — the provider
 * identity (slug `custom:<normalized-name>`) is derived entirely from `name`.
 * So the Add-Provider modal lets the user type a friendly name and silently
 * turns it into a machine-safe, unique id that is stored as the provider's name.
 *
 * Normalizes (lowercase, spaces → '-', strip anything not alnum/'-'), then
 * appends a numeric suffix (-2, -3, …) until the id is unique among
 * `existingIds`. Falls back to "provider" when the name normalizes to nothing.
 */
export function generateProviderId(displayName: string, existingIds: string[]): string {
  const base =
    normalizeProviderName(displayName)
      .replace(/[^a-z0-9-]/g, '')
      .replace(/-+/g, '-')
      .replace(/^-+|-+$/g, '') || 'provider'

  const taken = new Set(existingIds.map(normalizeProviderName))

  if (!taken.has(base)) {
    return base
  }

  let n = 2
  while (taken.has(`${base}-${n}`)) {
    n++
  }

  return `${base}-${n}`
}

/** Parse a raw `models` config value (dict or legacy list) into entries. */
function modelsToEntries(models: RawCustomProvider['models']): CustomProviderModel[] {
  if (!models) {
    return []
  }

  if (Array.isArray(models)) {
    return models
      .map((m) => String(m).trim())
      .filter(Boolean)
      .map((id) => ({ id }))
  }

  const out: CustomProviderModel[] = []

  for (const [key, value] of Object.entries(models)) {
    const id = key.trim()

    if (!id) {
      continue
    }

    if (value && typeof value === 'object') {
      const v = value as Record<string, unknown>
      const entry: CustomProviderModel = { id }

      if (typeof v['name'] === 'string' && v['name']) {
        entry.name = v['name']
      }

      if (v['advanced'] && typeof v['advanced'] === 'object') {
        entry.advanced = v['advanced'] as Record<string, unknown>
      }

      out.push(entry)
    } else {
      out.push({ id })
    }
  }

  return out
}

/** Serialize entries back to the config dict form. */
function entriesToDict(models: CustomProviderModel[]): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {}

  for (const m of models) {
    const id = m.id.trim()

    if (!id) {
      continue
    }

    const dict: Record<string, unknown> = {}

    if (m.name) {
      dict['name'] = m.name
    }

    if (m.advanced && Object.keys(m.advanced).length > 0) {
      dict['advanced'] = m.advanced
    }

    out[id] = dict
  }

  return out
}

export function readCustomProviders(config: HermesConfigRecord): CustomProviderEntry[] {
  const cfg = config as unknown as ConfigShape
  const list = Array.isArray(cfg.custom_providers) ? cfg.custom_providers : []

  return list
    .filter((e): e is RawCustomProvider => !!e && typeof e === 'object' && typeof e.name === 'string')
    .map((e) => ({
      name: e.name as string,
      base_url: (e.base_url as string) ?? '',
      api_key: e.api_key as string | undefined,
      api_mode: (e.api_mode as CustomProviderApiMode) || 'chat_completions',
      models: modelsToEntries(e.models)
    }))
}

export function upsertCustomProvider(config: HermesConfigRecord, entry: CustomProviderEntry): HermesConfigRecord {
  const cfg = config as unknown as ConfigShape
  const list: RawCustomProvider[] = Array.isArray(cfg.custom_providers) ? [...cfg.custom_providers] : []
  const norm = normalizeProviderName(entry.name)
  const idx = list.findIndex((e) => typeof e.name === 'string' && normalizeProviderName(e.name) === norm)

  const newEntry: RawCustomProvider = {
    name: entry.name.trim(),
    base_url: entry.base_url.trim(),
    api_mode: entry.api_mode || 'chat_completions'
  }

  // When editing and the caller supplied no models (the Add/Edit form no
  // longer edits models), preserve the existing models dict so discovery /
  // manual-add changes are not wiped on a name/base_url edit.
  const hasModels = Array.isArray(entry.models) && entry.models.length > 0

  if (idx >= 0 && !hasModels) {
    newEntry.models = (list[idx].models as Record<string, unknown>) ?? {}
  } else {
    newEntry.models = entriesToDict(entry.models ?? [])
  }

  // Only overwrite the key when the user actually supplied one; an empty
  // string means "keep the existing secret" (the form never shows it back).
  if (entry.api_key !== undefined && entry.api_key !== '') {
    newEntry.api_key = entry.api_key
  }

  if (idx >= 0) {
    list[idx] = { ...list[idx], ...newEntry }
  } else {
    list.push(newEntry)
  }

  return { ...config, custom_providers: list } as HermesConfigRecord
}

export function removeCustomProvider(config: HermesConfigRecord, name: string): HermesConfigRecord {
  const cfg = config as unknown as ConfigShape
  const norm = normalizeProviderName(name)
  const list: RawCustomProvider[] = Array.isArray(cfg.custom_providers)
    ? cfg.custom_providers.filter((e) => !(typeof e.name === 'string' && normalizeProviderName(e.name) === norm))
    : []

  return { ...config, custom_providers: list } as HermesConfigRecord
}

export function setProviderEnabled(config: HermesConfigRecord, slug: string, enabled: boolean): HermesConfigRecord {
  const cfg = config as unknown as ConfigShape

  // Custom provider: slug is `custom:<name>` (or the bare name). Match by the
  // normalized name; toggle the `enabled` flag (absence == enabled).
  const normName = normalizeProviderName(slug.replace(/^custom:/, ''))
  const customList: RawCustomProvider[] = Array.isArray(cfg.custom_providers) ? [...cfg.custom_providers] : []
  const matched = customList.some(
    (e) => typeof e.name === 'string' && normalizeProviderName(e.name) === normName
  )

  if (matched) {
    for (const e of customList) {
      if (typeof e.name === 'string' && normalizeProviderName(e.name) === normName) {
        if (enabled) {
          delete e.enabled
        } else {
          e.enabled = false
        }
      }
    }

    return { ...config, custom_providers: customList } as HermesConfigRecord
  }

  // Built-in provider: maintain model.disabled_providers.
  const model: { disabled_providers?: string[] } =
    cfg.model && typeof cfg.model === 'object' && !Array.isArray(cfg.model)
      ? { ...(cfg.model as Record<string, unknown>) }
      : {}
  const disabled: string[] = Array.isArray(model.disabled_providers) ? [...model.disabled_providers] : []
  const di = disabled.indexOf(slug)

  if (!enabled && di === -1) {
    disabled.push(slug)
  } else if (enabled && di !== -1) {
    disabled.splice(di, 1)
  }

  model.disabled_providers = disabled

  return { ...config, model } as HermesConfigRecord
}

export function isProviderEnabled(config: HermesConfigRecord, slug: string): boolean {
  const cfg = config as unknown as ConfigShape

  const normName = normalizeProviderName(slug.replace(/^custom:/, ''))
  const customList: RawCustomProvider[] = Array.isArray(cfg.custom_providers) ? cfg.custom_providers : []

  for (const e of customList) {
    if (typeof e.name === 'string' && normalizeProviderName(e.name) === normName) {
      return e.enabled !== false
    }
  }

  const disabled: string[] = Array.isArray(cfg.model?.disabled_providers) ? cfg.model!.disabled_providers! : []

  return !disabled.includes(slug)
}
