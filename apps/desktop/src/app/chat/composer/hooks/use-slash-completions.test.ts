import { describe, expect, it } from 'vitest'

import { slashCompletionGroup } from './use-slash-completions'

describe('slashCompletionGroup', () => {
  it('normalizes extension commands into the Skills group for inline lookup', () => {
    expect(slashCompletionGroup('/auto-skill-router', 'Tools & Skills')).toBe('Skills')
  })

  it('preserves the catalog section for built-in app commands', () => {
    expect(slashCompletionGroup('/new', 'Session')).toBe('Session')
  })
})
