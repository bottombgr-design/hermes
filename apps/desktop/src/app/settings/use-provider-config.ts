import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'

import {
  discoverProviderModels,
  getGlobalModelOptions,
  getHermesConfigRecord,
  saveHermesConfig,
  setEnvVar,
  testCustomProviderConnection
} from '@/hermes'
import type { HermesConfigRecord } from '@/types/hermes'

import {
  readCustomProviders,
  removeCustomProvider,
  setProviderEnabled,
  upsertCustomProvider,
  normalizeProviderName,
  type CustomProviderEntry,
  type CustomProviderModel
} from '@/lib/custom-provider-config'
import {
  $visibleModels,
  emptyProviderSentinelKey,
  modelVisibilityKey,
  setVisibleModels
} from '@/store/model-visibility'

const CATALOG_KEY = ['provider-model-manager', 'catalog'] as const
const CONFIG_KEY = ['provider-config', 'record'] as const

export interface UseProviderConfig {
  /** Custom providers parsed from config (for the Add/Edit form + delete). */
  customProviders: CustomProviderEntry[]
  isLoading: boolean
  isError: boolean
  /** True while a save/delete/enable mutation is in flight. */
  isSaving: boolean
  /** Create or update a custom provider, then re-probe the backend catalog. */
  saveCustomProvider: (entry: CustomProviderEntry) => Promise<void>
  /** Remove a custom provider by (raw) name. */
  deleteCustomProvider: (name: string) => Promise<void>
  /** Toggle activation for any provider slug (built-in or custom:<name>). */
  setEnabled: (slug: string, enabled: boolean) => Promise<void>
  /** Query a custom provider's /models endpoint and merge discovered models
   *  into its config (all added as inactive). */
  discoverModels: (slug: string) => Promise<string[]>
  /** Manually add a single model to a custom provider (added as active). */
  addModel: (slug: string, model: CustomProviderModel) => Promise<void>
  /** Test a custom provider's connectivity (latency/error inline). */
  testProviderConnection: (slug: string) => Promise<{ ok: boolean; latencyMs?: number; error?: string }>
  /** Force the backend to re-probe all configured providers and refresh the
   *  catalog query. Used by the "Update list" button on built-in providers. */
  refreshCatalog: () => Promise<void>
  /** Persist API key (+ optional base URL override) for a built-in provider
   *  via setEnvVar. `keyEnv` is the env var name (e.g. "OPENAI_API_KEY"). */
  saveBuiltInCredentials: (keyEnv: string, apiKey: string, baseUrl?: string, slug?: string) => Promise<void>
}

/**
 * Reads/writes the custom-provider + provider-activation sections of
 * ~/.hermes/config.yaml via the desktop backend (getHermesConfig /
 * saveHermesConfig). Every mutation persists the whole config, asks the
 * backend to re-probe custom providers, and invalidates the Provider Manager
 * catalog so the UI reflects the change immediately.
 */
export function useProviderConfig(): UseProviderConfig {
  const qc = useQueryClient()

  const configQuery = useQuery({
    queryKey: CONFIG_KEY,
    queryFn: getHermesConfigRecord
  })

  const customProviders = useMemo(
    () => readCustomProviders((configQuery.data ?? {}) as HermesConfigRecord),
    [configQuery.data]
  )

  const persist = useMutation({
    mutationFn: async (next: HermesConfigRecord) => {
      await saveHermesConfig(next)
      // Re-probe so the backend picks up new/changed custom providers, then
      // refresh the catalog + config views.
      await getGlobalModelOptions({
        includeUnconfigured: true,
        explicitOnly: false,
        refresh: true
      })
      qc.invalidateQueries({ queryKey: CATALOG_KEY })
      qc.invalidateQueries({ queryKey: CONFIG_KEY })
    }
  })

  const saveCustomProvider = useCallback(
    async (entry: CustomProviderEntry) => {
      const base = (configQuery.data ?? {}) as HermesConfigRecord
      const norm = normalizeProviderName(entry.name)
      const isNew = !customProviders.some(c => normalizeProviderName(c.name) === norm)
      await persist.mutateAsync(upsertCustomProvider(base, entry))
      // A brand-new provider starts with every model hidden (the store's
      // default for an uncustomized provider is "all visible", so we must
      // write the hide-all sentinel explicitly). Editing an existing provider
      // leaves its current visibility untouched.
      if (isNew) {
        const slug = `custom:${norm}`
        const next = new Set($visibleModels.get() ?? [])
        next.add(emptyProviderSentinelKey(slug))
        setVisibleModels(next)
      }
    },
    [configQuery.data, customProviders, persist]
  )

  const deleteCustomProvider = useCallback(
    async (name: string) => {
      const base = (configQuery.data ?? {}) as HermesConfigRecord
      await persist.mutateAsync(removeCustomProvider(base, name))
    },
    [configQuery.data, persist]
  )

  const setEnabled = useCallback(
    async (slug: string, enabled: boolean) => {
      const base = (configQuery.data ?? {}) as HermesConfigRecord
      await persist.mutateAsync(setProviderEnabled(base, slug, enabled))
    },
    [configQuery.data, persist]
  )

  const findEntry = useCallback(
    (slug: string): CustomProviderEntry | undefined =>
      customProviders.find((c) => `custom:${normalizeProviderName(c.name)}` === slug),
    [customProviders]
  )

  const discoverModels = useCallback(
    async (slug: string): Promise<string[]> => {
      const entry = findEntry(slug)

      if (!entry) {
        throw new Error(`Unknown custom provider: ${slug}`)
      }

      const { models } = await discoverProviderModels({
        baseUrl: entry.base_url,
        apiKey: entry.api_key,
        apiMode: entry.api_mode
      })

      // Merge discovered models into the existing list, dedupe by id.
      const byId = new Map<string, CustomProviderModel>()
      for (const m of entry.models) {
        byId.set(m.id, m)
      }
      const added: string[] = []
      for (const d of models) {
        if (!byId.has(d.id)) {
          byId.set(d.id, { id: d.id, name: d.name })
          added.push(d.id)
        }
      }
      const merged = [...byId.values()]

      const base = (configQuery.data ?? {}) as HermesConfigRecord
      await persist.mutateAsync(upsertCustomProvider(base, { ...entry, models: merged }))

      // Discovered models are inactive. If the provider was default-visible
      // (no explicit keys and no hide-all sentinel), write the sentinel so the
      // whole provider starts hidden; otherwise leave the user's existing
      // visibility untouched (new models simply aren't in the visible set).
      const stored = $visibleModels.get() ?? new Set<string>()
      const prefix = `${slug}::`
      const hasExplicit = [...stored].some((k) => k.startsWith(prefix) && !k.endsWith('::'))
      const hasSentinel = stored.has(emptyProviderSentinelKey(slug))
      if (!hasExplicit && !hasSentinel) {
        const next = new Set(stored)
        next.add(emptyProviderSentinelKey(slug))
        setVisibleModels(next)
      }

      return added
    },
    [customProviders, persist, findEntry, configQuery.data]
  )

  const addModel = useCallback(
    async (slug: string, model: CustomProviderModel) => {
      const entry = findEntry(slug)

      if (!entry) {
        throw new Error(`Unknown custom provider: ${slug}`)
      }

      const exists = entry.models.some((m) => m.id === model.id)
      const merged = exists
        ? entry.models.map((m) => (m.id === model.id ? model : m))
        : [...entry.models, model]

      const base = (configQuery.data ?? {}) as HermesConfigRecord
      await persist.mutateAsync(upsertCustomProvider(base, { ...entry, models: merged }))

      // Manual add is active: drop the hide-all sentinel and mark this model
      // visible so it shows up immediately.
      const stored = $visibleModels.get() ?? new Set<string>()
      const next = new Set(stored)
      next.delete(emptyProviderSentinelKey(slug))
      next.add(modelVisibilityKey(slug, model.id))
      setVisibleModels(next)
    },
    [customProviders, persist, findEntry, configQuery.data]
  )

  const testProviderConnection = useCallback(
    async (slug: string): Promise<{ ok: boolean; latencyMs?: number; error?: string }> => {
      const entry = findEntry(slug)

      if (!entry) {
        throw new Error(`Unknown custom provider: ${slug}`)
      }

      return testCustomProviderConnection({
        baseUrl: entry.base_url,
        apiKey: entry.api_key,
        apiMode: entry.api_mode
      })
    },
    [findEntry]
  )

  const refreshCatalog = useCallback(async () => {
    await getGlobalModelOptions({
      includeUnconfigured: true,
      explicitOnly: false,
      refresh: true
    })
    qc.invalidateQueries({ queryKey: CATALOG_KEY })
  }, [qc])

  const saveBuiltInCredentials = useCallback(
    async (keyEnv: string, apiKey: string, baseUrl?: string, slug?: string) => {
      if (apiKey) {
        await setEnvVar(keyEnv, apiKey)
      }
      if (baseUrl && slug) {
        const baseUrlEnv = `${slug.toUpperCase().replace(/-/g, '_')}_BASE_URL`
        await setEnvVar(baseUrlEnv, baseUrl)
      }
      qc.invalidateQueries({ queryKey: CATALOG_KEY })
      qc.invalidateQueries({ queryKey: CONFIG_KEY })
    },
    [qc]
  )

  return {
    customProviders,
    isLoading: configQuery.isPending,
    isError: configQuery.isError,
    isSaving: persist.isPending,
    saveCustomProvider,
    deleteCustomProvider,
    setEnabled,
    discoverModels,
    addModel,
    testProviderConnection,
    refreshCatalog,
    saveBuiltInCredentials
  }
}
