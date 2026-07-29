import { describe, it, expect } from 'vitest'
import {
  generateProviderId,
  normalizeProviderName,
  readCustomProviders,
  upsertCustomProvider,
  removeCustomProvider,
  setProviderEnabled,
  isProviderEnabled,
  type CustomProviderEntry
} from './custom-provider-config'

const entry = (over: Partial<CustomProviderEntry> = {}): CustomProviderEntry => ({
  name: 'My Provider',
  base_url: 'https://my.host/v1',
  api_mode: 'chat_completions',
  models: [{ id: 'alpha' }, { id: 'beta' }],
  ...over
})

describe('normalizeProviderName', () => {
  it('lowercases and replaces spaces with dashes', () => {
    expect(normalizeProviderName('My Cool Provider')).toBe('my-cool-provider')
  })

  it('trims surrounding whitespace', () => {
    expect(normalizeProviderName('  Open AI ')).toBe('open-ai')
  })
})

describe('generateProviderId', () => {
  it('normalizes a simple name into a clean id', () => {
    expect(generateProviderId('llama', [])).toBe('llama')
  })

  it('lowercases and replaces spaces with dashes', () => {
    expect(generateProviderId('My Llama', [])).toBe('my-llama')
  })

  it('appends a numeric suffix on collision', () => {
    expect(generateProviderId('llama', ['llama'])).toBe('llama-2')
  })

  it('finds the next free suffix when several collide', () => {
    expect(generateProviderId('llama', ['llama', 'llama-2'])).toBe('llama-3')
  })

  it('falls back to "provider" when the name normalizes to nothing', () => {
    expect(generateProviderId('   ', [])).toBe('provider')
  })

  it('strips special characters', () => {
    expect(generateProviderId('Llama@#!', [])).toBe('llama')
  })

  it('detects collisions after normalization', () => {
    expect(generateProviderId('My Llama', ['my-llama'])).toBe('my-llama-2')
  })
})

describe('readCustomProviders', () => {
  it('returns an empty list when absent', () => {
    expect(readCustomProviders({})).toEqual([])
  })

  it('parses list entries and converts models dict to a list', () => {
    const cfg = {
      custom_providers: [
        {
          name: 'Local',
          base_url: 'https://local/v1',
          api_mode: 'chat_completions',
          models: { alpha: {}, beta: {} }
        }
      ]
    }
    const out = readCustomProviders(cfg)
    expect(out).toHaveLength(1)
    expect(out[0].name).toBe('Local')
    expect(out[0].models).toEqual([{ id: 'alpha' }, { id: 'beta' }])
  })

  it('parses model name + advanced from the dict form', () => {
    const cfg = {
      custom_providers: [
        {
          name: 'Local',
          base_url: 'https://local/v1',
          models: { alpha: { name: 'Alpha Label' }, beta: { name: 'Beta', advanced: { temp: 0.5 } } }
        }
      ]
    }
    const out = readCustomProviders(cfg)
    expect(out[0].models).toEqual([
      { id: 'alpha', name: 'Alpha Label' },
      { id: 'beta', name: 'Beta', advanced: { temp: 0.5 } }
    ])
  })

  it('ignores malformed entries', () => {
    const cfg = { custom_providers: [null, { base_url: 'x' }, { name: 'ok', base_url: 'y', models: ['m'] }] }
    expect(readCustomProviders(cfg).map((p) => p.name)).toEqual(['ok'])
  })
})

describe('upsertCustomProvider', () => {
  it('adds a new entry and converts models list to dict', () => {
    const next = upsertCustomProvider({}, entry())
    const list = (next as any).custom_providers as unknown[]
    expect(list).toHaveLength(1)
    expect((list[0] as any).models).toEqual({ alpha: {}, beta: {} })
  })

  it('does not clobber unrelated config sections', () => {
    const base = { display: { theme: 'mono' } }
    const next = upsertCustomProvider(base, entry())
    expect((next as any).display).toEqual({ theme: 'mono' })
  })

  it('edits an existing entry by normalized name', () => {
    const base = upsertCustomProvider({}, entry({ base_url: 'https://old/v1' }))
    const next = upsertCustomProvider(base, entry({ base_url: 'https://new/v1' }))
    const list = (next as any).custom_providers as unknown[]
    expect(list).toHaveLength(1)
    expect((list[0] as any).base_url).toBe('https://new/v1')
  })

  it('preserves the existing api_key when the form submits an empty one', () => {
    const base = upsertCustomProvider({}, entry({ api_key: 'secret' }))
    const next = upsertCustomProvider(base, entry({ api_key: '' }))
    const list = (next as any).custom_providers as unknown[]
    expect((list[0] as any).api_key).toBe('secret')
  })

  it('overwrites the api_key when a new one is supplied', () => {
    const base = upsertCustomProvider({}, entry({ api_key: 'old' }))
    const next = upsertCustomProvider(base, entry({ api_key: 'new' }))
    const list = (next as any).custom_providers as unknown[]
    expect((list[0] as any).api_key).toBe('new')
  })

  it('preserves existing models when editing with an empty models list', () => {
    const base = upsertCustomProvider({}, entry({ models: [{ id: 'a' }, { id: 'b', name: 'Bee' }] }))
    // Simulate the Add/Edit form, which no longer submits models.
    const next = upsertCustomProvider(base, entry({ base_url: 'https://new/v1', models: [] }))
    const list = (next as any).custom_providers as unknown[]
    expect((list[0] as any).models).toEqual({ a: {}, b: { name: 'Bee' } })
  })

  it('replaces models when a non-empty list is supplied', () => {
    const base = upsertCustomProvider({}, entry({ models: [{ id: 'a' }] }))
    const next = upsertCustomProvider(base, entry({ models: [{ id: 'c', name: 'Cee' }] }))
    const list = (next as any).custom_providers as unknown[]
    expect((list[0] as any).models).toEqual({ c: { name: 'Cee' } })
  })
})

describe('removeCustomProvider', () => {
  it('removes by normalized name', () => {
    const base = upsertCustomProvider({}, entry())
    const next = removeCustomProvider(base, 'my-provider')
    expect((next as any).custom_providers).toHaveLength(0)
  })

  it('leaves other entries intact', () => {
    let cfg = upsertCustomProvider({}, entry({ name: 'A' }))
    cfg = upsertCustomProvider(cfg, entry({ name: 'B' }))
    const next = removeCustomProvider(cfg, 'a')
    expect((next as any).custom_providers).toHaveLength(1)
    expect((next as any).custom_providers[0].name).toBe('B')
  })
})

describe('setProviderEnabled / isProviderEnabled (custom)', () => {
  it('defaults custom providers to enabled', () => {
    const cfg = upsertCustomProvider({}, entry())
    expect(isProviderEnabled(cfg, 'custom:my-provider')).toBe(true)
  })

  it('disables a custom provider via the enabled flag', () => {
    const cfg = upsertCustomProvider({}, entry())
    const disabled = setProviderEnabled(cfg, 'custom:my-provider', false)
    expect(isProviderEnabled(disabled, 'custom:my-provider')).toBe(false)
    expect((disabled as any).custom_providers[0].enabled).toBe(false)
  })

  it('re-enables a custom provider by deleting the flag', () => {
    const cfg = upsertCustomProvider({}, entry())
    const disabled = setProviderEnabled(cfg, 'custom:my-provider', false)
    const enabled = setProviderEnabled(disabled, 'custom:my-provider', true)
    expect(isProviderEnabled(enabled, 'custom:my-provider')).toBe(true)
    expect((enabled as any).custom_providers[0].enabled).toBeUndefined()
  })
})

describe('setProviderEnabled / isProviderEnabled (built-in)', () => {
  it('defaults built-in providers to enabled', () => {
    expect(isProviderEnabled({}, 'openai')).toBe(true)
  })

  it('disables a built-in provider via model.disabled_providers', () => {
    const disabled = setProviderEnabled({}, 'openai', false)
    expect(isProviderEnabled(disabled, 'openai')).toBe(false)
    expect((disabled as any).model.disabled_providers).toEqual(['openai'])
  })

  it('re-enables a built-in provider by removing it from the list', () => {
    const disabled = setProviderEnabled({}, 'openai', false)
    const enabled = setProviderEnabled(disabled, 'openai', true)
    expect(isProviderEnabled(enabled, 'openai')).toBe(true)
    expect((enabled as any).model.disabled_providers).toEqual([])
  })
})
