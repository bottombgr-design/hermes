import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { $repoStatus, $repoWorktrees } from '@/store/coding-status'

import { CodingStatusRow } from './coding-row'

describe('CodingStatusRow branch actions', () => {
  afterEach(() => {
    cleanup()
    $repoStatus.set(null)
    $repoWorktrees.set([])
  })

  it('opens the default worktree instead of offering an impossible branch switch', async () => {
    $repoStatus.set({
      added: 0,
      ahead: 0,
      behind: 0,
      branch: 'fix/example',
      changed: 0,
      conflicted: 0,
      defaultBranch: 'main',
      detached: false,
      files: [],
      removed: 0,
      staged: 0,
      unstaged: 0,
      untracked: 0
    })
    $repoWorktrees.set([
      { branch: 'fix/example', detached: false, isMain: false, locked: false, path: '/repo/.worktrees/example' },
      { branch: 'main', detached: false, isMain: true, locked: false, path: '/repo' }
    ])

    const onOpenWorktree = vi.fn()
    const onSwitchBranch = vi.fn(async () => undefined)

    render(
      <CodingStatusRow
        onBranchOff={vi.fn(async () => undefined)}
        onOpenWorktree={onOpenWorktree}
        onSwitchBranch={onSwitchBranch}
        repoPath="/repo/.worktrees/example"
      />
    )

    fireEvent.pointerDown(screen.getByRole('button', { name: 'New branch' }), {
      button: 0,
      ctrlKey: false,
      pointerType: 'mouse'
    })

    fireEvent.click(await screen.findByRole('menuitem', { name: 'Switch to main' }))

    expect(onOpenWorktree).toHaveBeenCalledWith('/repo')
    expect(onSwitchBranch).not.toHaveBeenCalled()
  })

  it('switches normally when no worktree owns the default branch', async () => {
    $repoStatus.set({
      added: 0,
      ahead: 0,
      behind: 0,
      branch: 'fix/example',
      changed: 0,
      conflicted: 0,
      defaultBranch: 'main',
      detached: false,
      files: [],
      removed: 0,
      staged: 0,
      unstaged: 0,
      untracked: 0
    })
    $repoWorktrees.set([])

    const onOpenWorktree = vi.fn()
    const onSwitchBranch = vi.fn(async () => undefined)

    render(
      <CodingStatusRow
        onBranchOff={vi.fn(async () => undefined)}
        onOpenWorktree={onOpenWorktree}
        onSwitchBranch={onSwitchBranch}
        repoPath="/repo/.worktrees/example"
      />
    )

    fireEvent.pointerDown(screen.getByRole('button', { name: 'New branch' }), {
      button: 0,
      ctrlKey: false,
      pointerType: 'mouse'
    })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Switch to main' }))

    expect(onSwitchBranch).toHaveBeenCalledWith('main')
    expect(onOpenWorktree).not.toHaveBeenCalled()
  })
})
