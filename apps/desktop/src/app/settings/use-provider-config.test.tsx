// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getGlobalModelOptions, getHermesConfigRecord, saveHermesConfig } from '@/hermes'

import { $visibleModels, emptyProviderSentinelKey } from '@/store/model-visibility'

import { useProviderConfig } from './use-provider-config'

vi.mock('@/hermes', () => ({
  getGlobalModelOptions: vi.fn().mockResolvedValue({ providers: [] }),
  getHermesConfigRecord: vi.fn(),
  saveHermesConfig: vi.fn().mockResolvedValue({ ok: true })
}))

const Wrapper = ({ children }: { children: React.ReactNode }) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const baseConfig = () => ({
  custom_providers: [{ name: 'Lab', base_url: 'https://lab/v1', models: [{ id: 'a' }] }]
})

describe('useProviderConfig', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loads config and parses custom providers', async () => {
    vi.mocked(getHermesConfigRecord).mockResolvedValue(baseConfig() as any)

    const { result } = renderHook(() => useProviderConfig(), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.customProviders).toHaveLength(1)
    expect(result.current.customProviders[0].name).toBe('Lab')
  })

  it('saveCustomProvider persists the upserted config and re-probes', async () => {
    vi.mocked(getHermesConfigRecord).mockResolvedValue(baseConfig() as any)

    const { result } = renderHook(() => useProviderConfig(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    await result.current.saveCustomProvider({
      name: 'New One',
      base_url: 'https://new/v1',
      api_mode: 'chat_completions',
      models: [{ id: 'x' }]
    })

    expect(saveHermesConfig).toHaveBeenCalledTimes(1)
    const saved = vi.mocked(saveHermesConfig).mock.calls[0][0] as any
    expect(saved.custom_providers).toHaveLength(2)
    expect(saved.custom_providers[1].name).toBe('New One')
    expect(getGlobalModelOptions).toHaveBeenCalledWith({
      includeUnconfigured: true,
      explicitOnly: false,
      refresh: true
    })
  })

  it('setEnabled disables a built-in provider via model.disabled_providers', async () => {
    vi.mocked(getHermesConfigRecord).mockResolvedValue({ model: { provider: 'openai' } } as any)

    const { result } = renderHook(() => useProviderConfig(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    await result.current.setEnabled('openai', false)

    const saved = vi.mocked(saveHermesConfig).mock.calls[0][0] as any
    expect(saved.model.disabled_providers).toEqual(['openai'])
  })

  it('setEnabled disables a custom provider via the enabled flag', async () => {
    vi.mocked(getHermesConfigRecord).mockResolvedValue(baseConfig() as any)

    const { result } = renderHook(() => useProviderConfig(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    await result.current.setEnabled('custom:lab', false)

    const saved = vi.mocked(saveHermesConfig).mock.calls[0][0] as any
    expect(saved.custom_providers[0].enabled).toBe(false)
  })

  it('deleteCustomProvider removes the entry by name', async () => {
    vi.mocked(getHermesConfigRecord).mockResolvedValue(baseConfig() as any)

    const { result } = renderHook(() => useProviderConfig(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    await result.current.deleteCustomProvider('lab')

    const saved = vi.mocked(saveHermesConfig).mock.calls[0][0] as any
    expect(saved.custom_providers).toHaveLength(0)
  })

  it('a new provider starts with all models hidden (hide-all sentinel)', async () => {
    vi.mocked(getHermesConfigRecord).mockResolvedValue({} as any)
    $visibleModels.set(null)

    const { result } = renderHook(() => useProviderConfig(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    await result.current.saveCustomProvider({
      name: 'Fresh Provider',
      base_url: 'https://fresh/v1',
      api_mode: 'chat_completions',
      models: [{ id: 'a' }, { id: 'b' }]
    })

    const stored = $visibleModels.get()
    expect(stored?.has(emptyProviderSentinelKey('custom:fresh-provider'))).toBe(true)
  })

  it('editing an existing provider does not change its visibility', async () => {
    vi.mocked(getHermesConfigRecord).mockResolvedValue(baseConfig() as any)
    $visibleModels.set(null)

    const { result } = renderHook(() => useProviderConfig(), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    await result.current.saveCustomProvider({
      name: 'Lab',
      base_url: 'https://lab/v2',
      api_mode: 'chat_completions',
      models: [{ id: 'a' }, { id: 'b' }, { id: 'c' }]
    })

    // No sentinel written for an existing provider.
    expect($visibleModels.get()?.has(emptyProviderSentinelKey('custom:lab'))).toBeFalsy()
  })

  describe('refreshCatalog', () => {
    const makeWrapper = () => {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
      })
      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      )
      return { client, wrapper }
    }

    it('calls getGlobalModelOptions with refresh: true', async () => {
      vi.mocked(getHermesConfigRecord).mockResolvedValue({} as any)
      const { wrapper } = makeWrapper()

      const { result } = renderHook(() => useProviderConfig(), { wrapper })
      await waitFor(() => expect(result.current.isLoading).toBe(false))

      vi.mocked(getGlobalModelOptions).mockClear()
      await result.current.refreshCatalog()

      expect(getGlobalModelOptions).toHaveBeenCalledWith({
        includeUnconfigured: true,
        explicitOnly: false,
        refresh: true
      })
    })

    it('invalidates the catalog query key', async () => {
      vi.mocked(getHermesConfigRecord).mockResolvedValue({} as any)
      const { client, wrapper } = makeWrapper()
      const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

      const { result } = renderHook(() => useProviderConfig(), { wrapper })
      await waitFor(() => expect(result.current.isLoading).toBe(false))

      invalidateSpy.mockClear()
      await result.current.refreshCatalog()

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ['provider-model-manager', 'catalog']
      })
    })

    it('propagates errors from the RPC call', async () => {
      vi.mocked(getHermesConfigRecord).mockResolvedValue({} as any)
      const { wrapper } = makeWrapper()

      const { result } = renderHook(() => useProviderConfig(), { wrapper })
      await waitFor(() => expect(result.current.isLoading).toBe(false))

      vi.mocked(getGlobalModelOptions).mockRejectedValueOnce(new Error('backend down'))

      await expect(result.current.refreshCatalog()).rejects.toThrow('backend down')
    })
  })
})
