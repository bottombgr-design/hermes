// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $visibleModels, emptyProviderSentinelKey } from '@/store/model-visibility'
import type { ModelOptionProvider } from '@/types/hermes'
import { saveHermesConfig } from '@/hermes'

import { ProviderModelManager } from './provider-model-manager'

const providers: ModelOptionProvider[] = [
  { slug: 'openai', name: 'OpenAI', models: ['gpt-4o'], enabled: true },
  { slug: 'custom:lab', name: 'Lab', models: ['a'], is_user_defined: true, enabled: true }
]

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof import('@/hermes')>()),
  getGlobalModelOptions: vi.fn(() => ({ providers })),
  getHermesConfigRecord: vi.fn(() => ({
    custom_providers: [{ name: 'Lab', base_url: 'https://lab/v1', models: { a: {} } }]
  })),
  saveHermesConfig: vi.fn(() => ({ ok: true })),
  discoverProviderModels: vi.fn(() => Promise.resolve({ models: [{ id: 'b', name: 'B' }] }))
}))

function renderManager() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProviderModelManager />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('ProviderModelManager integration', () => {
  beforeEach(() => {
    $visibleModels.set(null)
    vi.mocked(saveHermesConfig).mockClear()
  })

  it('adds a new custom provider via the dialog and hides its models by default', async () => {
    renderManager()

    // Open the add dialog from the nav header.
    fireEvent.click(screen.getByRole('button', { name: 'Add provider…' }))
    expect(screen.getByLabelText('Name')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Fresh' } })
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://fresh/v1' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(saveHermesConfig).toHaveBeenCalled())
    const saved = vi.mocked(saveHermesConfig).mock.calls[0][0] as any
    // The stored identity is the generated id (normalized from the friendly
    // name), not the raw display name.
    expect(saved.custom_providers.some((c: any) => c.name === 'fresh')).toBe(true)
    // New provider starts with every model hidden.
    expect($visibleModels.get()?.has(emptyProviderSentinelKey('custom:fresh'))).toBe(true)
  })

  it('toggles a custom provider’s activation and persists it', async () => {
    renderManager()

    // Select the custom provider, then flip its activation switch.
    fireEvent.click(await screen.findByText('Lab'))
    const toggle = await screen.findByLabelText('Disable provider')
    fireEvent.click(toggle)

    await waitFor(() => expect(saveHermesConfig).toHaveBeenCalled())
    const saved = vi.mocked(saveHermesConfig).mock.calls[0][0] as any
    const lab = saved.custom_providers?.find((c: any) => c.name === 'Lab')
    expect(lab?.enabled).toBe(false)
  })

  it('discovers models and adds them as inactive', async () => {
    renderManager()

    fireEvent.click(await screen.findByText('Lab'))
    // Lab already has 1 model → the button reads "Update list" (not "Discover models").
    fireEvent.click(await screen.findByRole('button', { name: 'Update list' }))

    await waitFor(() => expect(saveHermesConfig).toHaveBeenCalled())
    const saved = vi.mocked(saveHermesConfig).mock.calls[0][0] as any
    const lab = saved.custom_providers?.find((c: any) => c.name === 'Lab')
    // Discovered model 'b' is merged into the config.
    expect(lab?.models?.b).toBeDefined()
    // Provider starts hidden (sentinel) since it was default-visible.
    expect($visibleModels.get()?.has(emptyProviderSentinelKey('custom:lab'))).toBe(true)
  })

  it('manually adds a model and marks it active', async () => {
    renderManager()

    fireEvent.click(await screen.findByText('Lab'))
    fireEvent.click(await screen.findByRole('button', { name: 'Add model' }))

    fireEvent.change(await screen.findByLabelText('Model ID'), { target: { value: 'c' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(saveHermesConfig).toHaveBeenCalled())
    const saved = vi.mocked(saveHermesConfig).mock.calls[0][0] as any
    const lab = saved.custom_providers?.find((c: any) => c.name === 'Lab')
    expect(lab?.models?.c).toBeDefined()
    // Manually added model is active (visible).
    expect($visibleModels.get()?.has('custom:lab::c')).toBe(true)
  })
})
