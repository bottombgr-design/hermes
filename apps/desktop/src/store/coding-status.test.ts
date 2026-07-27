import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { applyRuntimeInfo } from '@/app/session/hooks/use-session-actions/utils'
import type { HermesGitWorktree, HermesRepoStatus } from '@/global'

import { $repoStatus, $repoStatusLoading, $repoWorktrees, refreshRepoStatus } from './coding-status'
import { clearNotifications } from './notifications'
import {
  $busy,
  $currentCwd,
  $selectedStoredSessionId,
  $workspaceCwdOwner,
  releaseWorkspaceCwdOwner,
  setSessions,
  setWorkspaceCwdOwner,
  workspaceCwdBelongsToSelectedSession
} from './session'

const sampleStatus: HermesRepoStatus = {
  branch: 'feature/login',
  defaultBranch: 'main',
  detached: false,
  ahead: 1,
  behind: 0,
  staged: 1,
  unstaged: 2,
  untracked: 0,
  conflicted: 0,
  changed: 3,
  added: 12,
  removed: 4,
  files: []
}

// A second repo's facts, so "the new conversation's repo published" is provable
// by identity rather than by "something non-null landed".
const otherRepoStatus: HermesRepoStatus = { ...sampleStatus, branch: 'main', staged: 0, unstaged: 0, changed: 0 }

const sampleWorktree: HermesGitWorktree = {
  path: '/repo-a',
  branch: 'feature/login',
  isMain: true,
  detached: false,
  locked: false
}

// `vi.runAllTicks()` only advances the process-tick queue; the probe's publish
// hangs off a promise chain, so a resolved probe needs real microtask hops
// before the atom settles. Draining generously keeps the "never publishes"
// assertions honest: they must fail on a publish, not merely outrun it.
async function drainProbeMicrotasks(): Promise<void> {
  for (let hop = 0; hop < 20; hop++) {
    await Promise.resolve()
  }
}

// The bridge stub. `worktreeList` is optional because most tests only care about
// the status probe; the ownership contract for the worktree menu needs both, so
// they live in one helper instead of two competing window assignments.
function stubProbe(
  impl: (cwd: string) => Promise<HermesRepoStatus | null>,
  worktreeList?: (repoPath: string) => Promise<HermesGitWorktree[]>
) {
  ;(window as unknown as { hermesDesktop?: unknown }).hermesDesktop = {
    git: worktreeList ? { repoStatus: impl, worktreeList } : { repoStatus: impl }
  }
}

describe('refreshRepoStatus', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    $repoStatus.set(null)
    $repoStatusLoading.set(false)
    $repoWorktrees.set([])
    $currentCwd.set('')
    $selectedStoredSessionId.set(null)
    // The cwd-ownership marker is module state shared by every test: a leftover
    // owner id would silently withhold (or wrongly allow) the next test's
    // defaulted refresh.
    $workspaceCwdOwner.set(null)
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  })

  it('populates $repoStatus from the probe for an explicit cwd', async () => {
    stubProbe(async () => sampleStatus)
    await refreshRepoStatus('/repo')
    expect($repoStatus.get()).toEqual(sampleStatus)
  })

  it('falls back to the active session cwd when none is passed', async () => {
    const probe = vi.fn(async () => sampleStatus)
    stubProbe(probe)
    $currentCwd.set('/active/repo')
    await refreshRepoStatus()
    expect(probe).toHaveBeenCalledWith('/active/repo')
  })

  it('clears status when there is no cwd', async () => {
    stubProbe(async () => sampleStatus)
    $repoStatus.set(sampleStatus)
    await refreshRepoStatus('   ')
    expect($repoStatus.get()).toBeNull()
  })

  it('clears status when the probe is unavailable (remote backend)', async () => {
    $repoStatus.set(sampleStatus)
    await refreshRepoStatus('/repo')
    expect($repoStatus.get()).toBeNull()
  })

  it('clears status when the probe throws', async () => {
    stubProbe(async () => {
      throw new Error('not a repo')
    })
    $repoStatus.set(sampleStatus)
    await refreshRepoStatus('/repo')
    expect($repoStatus.get()).toBeNull()
  })

  it('never publishes an old worktree status after the active cwd moves', async () => {
    let resolveOld!: (status: HermesRepoStatus | null) => void
    stubProbe(
      () =>
        new Promise(resolve => {
          resolveOld = resolve
        })
    )

    $currentCwd.set('/repo-a')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()

    // The first probe is still in flight when the user switches sessions. The
    // new cwd's probe is intentionally debounced, so this is the exact window
    // where Ctrl+Shift+B used to see the old branch in the coding rail.
    $currentCwd.set('/repo-b')
    expect($repoStatus.get()).toBeNull()

    resolveOld(sampleStatus)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toBeNull()
  })

  it('runs one probe at a time and coalesces overlap into one trailing refresh', async () => {
    const resolvers: Array<(status: HermesRepoStatus | null) => void> = []
    const calls: string[] = []
    let active = 0
    let maxActive = 0

    stubProbe(
      cwd =>
        new Promise(resolve => {
          calls.push(cwd)
          active++
          maxActive = Math.max(maxActive, active)
          resolvers.push(status => {
            active--
            resolve(status)
          })
        })
    )

    const first = refreshRepoStatus('/repo-a')
    const second = refreshRepoStatus('/repo-b')
    const third = refreshRepoStatus('/repo-c')

    expect(calls).toEqual(['/repo-a'])
    expect(maxActive).toBe(1)
    expect($repoStatusLoading.get()).toBe(true)

    resolvers.shift()?.(sampleStatus)
    await Promise.resolve()
    await Promise.resolve()

    expect(calls).toEqual(['/repo-a', '/repo-c'])
    expect(maxActive).toBe(1)
    expect($repoStatus.get()).toBeNull()

    resolvers.shift()?.(sampleStatus)
    await Promise.all([first, second, third])

    expect(maxActive).toBe(1)
    expect($repoStatus.get()).toEqual(sampleStatus)
    expect($repoStatusLoading.get()).toBe(false)
  })

  it('refreshes when the stored session id changes even if the cwd is unchanged', async () => {
    const probe = vi.fn(async () => sampleStatus)
    stubProbe(probe)

    $currentCwd.set('/repo')
    $selectedStoredSessionId.set('session-a')
    // Session A's resume settled on this folder, so A owns the live cwd.
    setWorkspaceCwdOwner('session-a')
    // The cwd subscription fires on the set above; drain the debounced refresh.
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    probe.mockClear()

    // Switch to a different session in the SAME repo dir. The cwd atom value is
    // identical, so its subscription would not re-fire — but the stored-session
    // id did change, which must still trigger a probe so the branch label
    // tracks the new session's checked-out branch.
    $selectedStoredSessionId.set('session-b')
    // Session B resumes into the same folder: the path never moves, ownership
    // transfers. This is the whole point of marking ownership instead of
    // clearing the path — the same-folder case still re-probes.
    setWorkspaceCwdOwner('session-b')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect(probe).toHaveBeenCalledWith('/repo')
  })

  it('hides the previous conversation repo facts the moment the stored session id switches', async () => {
    stubProbe(async () => sampleStatus)

    $currentCwd.set('/repo-a')
    $selectedStoredSessionId.set('session-a')
    setWorkspaceCwdOwner('session-a')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(sampleStatus)
    $repoWorktrees.set([sampleWorktree])

    // A conversation switch publishes the new stored-session id first; the new
    // cwd only lands when the resume RPC returns. Nothing may stay painted in
    // that window, so the rail cannot show the previous repo's branch label.
    $selectedStoredSessionId.set('session-b')

    expect($repoStatus.get()).toBeNull()
    expect($repoWorktrees.get()).toEqual([])
  })

  it('never publishes a probe owned by a conversation that is no longer selected', async () => {
    let resolveStale!: (status: HermesRepoStatus | null) => void
    stubProbe(
      () =>
        new Promise(resolve => {
          resolveStale = resolve
        })
    )

    $currentCwd.set('/repo-a')
    $selectedStoredSessionId.set('session-a')
    setWorkspaceCwdOwner('session-a')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()

    // Switch conversations while session A's probe is still in flight, and leave
    // $currentCwd alone: mid-switch the previous conversation's path really is
    // still the active one, so the cwd guard reads the stale answer as valid
    // (#71254). Only the requesting-conversation guard can reject it.
    $selectedStoredSessionId.set('session-b')

    resolveStale(sampleStatus)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toBeNull()
  })

  it('publishes a probe that starts and finishes while the same conversation stays selected', async () => {
    let resolveCurrent!: (status: HermesRepoStatus | null) => void
    stubProbe(
      () =>
        new Promise(resolve => {
          resolveCurrent = resolve
        })
    )

    $currentCwd.set('/repo-a')
    $selectedStoredSessionId.set('session-a')
    setWorkspaceCwdOwner('session-a')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()

    resolveCurrent(sampleStatus)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(sampleStatus)
  })

  it('publishes for a new chat window that has no stored session id yet', async () => {
    let resolveDraft!: (status: HermesRepoStatus | null) => void
    stubProbe(
      () =>
        new Promise(resolve => {
          resolveDraft = resolve
        })
    )

    // A fresh draft has no stored session id. The cwd subscription passes the
    // path it just committed, and an explicit target is trusted on its own, so
    // the rail paints without waiting for any ownership claim.
    expect($selectedStoredSessionId.get()).toBeNull()

    $currentCwd.set('/repo-draft')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()

    resolveDraft(sampleStatus)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(sampleStatus)
  })

  it('withholds the debounced re-probe after a switch instead of republishing the previous repo', async () => {
    const probe = vi.fn(async () => sampleStatus)
    stubProbe(probe)

    $currentCwd.set('/repo-a')
    $selectedStoredSessionId.set('session-a')
    setWorkspaceCwdOwner('session-a')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(sampleStatus)
    probe.mockClear()

    // The switch publishes session B's id first; B's resume has not returned, so
    // $currentCwd still points at A's folder and nobody has claimed it for B.
    // Dropping late results is not enough here — the switch schedules a FRESH
    // refresh, and that new probe would read A's path, tag it with B's id, and
    // publish A's repo as though it were B's (#71254).
    $selectedStoredSessionId.set('session-b')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toBeNull()
    expect(probe).not.toHaveBeenCalled()
  })

  it('populates the rail once the newly selected conversation claims the workspace', async () => {
    const probe = vi.fn(async (cwd: string) => (cwd === '/repo-b' ? otherRepoStatus : sampleStatus))
    stubProbe(probe)

    $currentCwd.set('/repo-a')
    $selectedStoredSessionId.set('session-a')
    setWorkspaceCwdOwner('session-a')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    $selectedStoredSessionId.set('session-b')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toBeNull()

    // B's resume settles: the workspace moves to B's folder and B claims it.
    // Withholding must be a pause, not a dead end — the rail has to come back.
    $currentCwd.set('/repo-b')
    setWorkspaceCwdOwner('session-b')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(otherRepoStatus)
  })

  it('probes a defaulted target for a fresh draft where no conversation owns the workspace', async () => {
    const probe = vi.fn(async () => sampleStatus)
    stubProbe(probe)

    // Detached/new-chat state: selected id null, owner null. Ownership matches,
    // so a refresh with NO argument (turn settle, window focus, worktree token)
    // must still probe — the withhold gate cannot strand a draft's rail.
    expect($selectedStoredSessionId.get()).toBeNull()
    expect($workspaceCwdOwner.get()).toBeNull()

    $currentCwd.set('/repo-draft')
    await refreshRepoStatus()
    await drainProbeMicrotasks()

    expect(probe).toHaveBeenCalledWith('/repo-draft')
    expect($repoStatus.get()).toEqual(sampleStatus)
  })

  it('trusts an explicit cwd argument even while ownership does not match', async () => {
    const probe = vi.fn(async () => otherRepoStatus)
    stubProbe(probe)

    // Mid-switch: session B is selected but ownership still names A, so any
    // DEFAULTED refresh is withheld.
    $selectedStoredSessionId.set('session-b')
    setWorkspaceCwdOwner('session-a')

    // The user picks a folder. The pick commits the path and then names it
    // explicitly, which is a caller stating which workspace to read rather than
    // an inference from a possibly-stale atom — so it must not be withheld.
    $currentCwd.set('/picked-repo')
    await refreshRepoStatus('/picked-repo')
    await drainProbeMicrotasks()

    expect(probe).toHaveBeenCalledWith('/picked-repo')
    expect($repoStatus.get()).toEqual(otherRepoStatus)

    // Same unchanged ownership state, defaulted target: withheld. The contrast
    // is the contract — explicitness, not ownership, is what carries the pick.
    probe.mockClear()
    await refreshRepoStatus()
    await drainProbeMicrotasks()

    expect(probe).not.toHaveBeenCalled()
  })

  it('never leaves the previous conversation worktrees behind when worktreeList resolves late', async () => {
    let resolveWorktrees!: (worktrees: HermesGitWorktree[]) => void
    stubProbe(
      async () => sampleStatus,
      () =>
        new Promise(resolve => {
          resolveWorktrees = resolve
        })
    )

    $currentCwd.set('/repo-a')
    $selectedStoredSessionId.set('session-a')
    setWorkspaceCwdOwner('session-a')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(sampleStatus)

    // `git worktree list` is slower than `git status`; the user switches while it
    // is still running. Its answer describes A's repo, so the worktree menu must
    // not offer it under B.
    $selectedStoredSessionId.set('session-b')

    resolveWorktrees([sampleWorktree])
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoWorktrees.get()).toEqual([])
  })

  it('does not latch $repoStatusLoading when a mid-switch refresh is withheld', async () => {
    stubProbe(async () => sampleStatus)

    $currentCwd.set('/repo-a')
    $selectedStoredSessionId.set('session-a')
    setWorkspaceCwdOwner('session-a')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatusLoading.get()).toBe(false)

    $selectedStoredSessionId.set('session-b')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    // The withheld branch returns early. If it left the spinner true, the rail
    // would show a permanent loading state until the next real probe.
    expect($repoStatusLoading.get()).toBe(false)
  })

  it('re-arms a withheld refresh when a same-folder conversation claims the workspace after the debounce', async () => {
    // The same repo folder, re-read after the switch. Serving a DIFFERENT status
    // for the post-claim probe is what makes the final assertion provable: the
    // rail can only hold this value if a probe actually ran after ownership
    // transferred, not because an earlier publish was left behind.
    let nextStatus = sampleStatus
    const probe = vi.fn(async () => nextStatus)
    stubProbe(probe)

    // Both conversations live in ONE folder (#68208), so the path is set once
    // here and never moves again. Re-setting it later would let the cwd
    // subscription re-arm the probe and the test would stop saying anything
    // about ownership.
    $currentCwd.set('/repo-shared')
    $selectedStoredSessionId.set('session-a')
    setWorkspaceCwdOwner('session-a')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(sampleStatus)
    probe.mockClear()

    // The switch publishes B's id first. The debounced refresh fires while
    // ownership still names A and is correctly withheld.
    $selectedStoredSessionId.set('session-b')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect(probe).not.toHaveBeenCalled()
    expect($repoStatus.get()).toBeNull()

    // B's resume returns AFTER that window — the normal case, since a resume RPC
    // is slower than a 100ms debounce. It settles on the same path, so
    // $currentCwd is written with an identical value and nanostores publishes
    // nothing; the claim is the only edge left. If ownership is not a trigger in
    // its own right, the withheld probe is never re-armed and the rail stays
    // blank until an unrelated edge (turn settle, refocus) wanders in.
    nextStatus = otherRepoStatus
    setWorkspaceCwdOwner('session-b')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect(probe).toHaveBeenCalledWith('/repo-shared')
    expect($repoStatus.get()).toEqual(otherRepoStatus)
  })

  it('keeps a picked workspace painted across a turn settle once the pick claims ownership', async () => {
    // A DIFFERENT status for the post-settle probe: the rail can only hold this
    // value if a probe actually ran on the settle edge, so the assertion cannot
    // pass on the pick's earlier publish being left behind.
    let nextStatus = sampleStatus
    const probe = vi.fn(async () => nextStatus)
    stubProbe(probe)

    // Resuming a DETACHED conversation leaves the workspace marked as belonging
    // to nobody, so the leftover path on screen is provably not this
    // conversation's and every DEFAULTED refresh is withheld. Released through the
    // same call the resume path uses: the marker itself is module-private, and a
    // hand-copied literal would keep passing on any mismatching string even if the
    // real marker changed.
    $currentCwd.set('/repo-previous')
    $selectedStoredSessionId.set('session-detached')
    releaseWorkspaceCwdOwner()
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toBeNull()

    // The user picks a folder. A pick commits the path AND hands it to the
    // selected conversation — one operation, in this order. The commit paints
    // via the explicit target the cwd subscription passes; the claim is what
    // keeps it painted.
    $currentCwd.set('/picked-repo')
    setWorkspaceCwdOwner('session-detached')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(sampleStatus)

    // A turn settles — the first DEFAULTED edge after the pick (the same shape as
    // a window refocus or a worktree-token bump). Ownership was claimed with the
    // path, so this edge re-reads the picked repo instead of blanking the rail
    // the user just populated (#71254).
    nextStatus = otherRepoStatus
    probe.mockClear()
    $busy.set(true)
    $busy.set(false)
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect(probe).toHaveBeenCalledWith('/picked-repo')
    expect($repoStatus.get()).toEqual(otherRepoStatus)
  })

  it('blanks a picked workspace on the next turn settle when the pick leaves ownership unclaimed', async () => {
    const probe = vi.fn(async () => sampleStatus)
    stubProbe(probe)

    $currentCwd.set('/repo-previous')
    $selectedStoredSessionId.set('session-detached')
    releaseWorkspaceCwdOwner()
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    // The pick moves the path WITHOUT claiming it. The explicit target still
    // paints, so the bug is invisible at pick time.
    $currentCwd.set('/picked-repo')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(sampleStatus)

    // The negative half of the contract, and the reason ownership has to travel
    // with a pick: the withhold branch CLEARS the rail, so with the mismatch
    // stranded the first defaulted edge turns "hold off until the workspace is
    // re-homed" into a permanent blanking — rail on the pick, gone on turn end.
    probe.mockClear()
    $busy.set(true)
    $busy.set(false)
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect(probe).not.toHaveBeenCalled()
    expect($repoStatus.get()).toBeNull()
  })

  it('keeps withholding after a release while no conversation is selected', async () => {
    // Why the release marker cannot be `null`: a fresh draft's selection is also
    // null, so a null release would COMPARE EQUAL and hand the leftover path to
    // the draft as its own workspace — #71254, one selection over. This is a
    // reachable state, not a hypothetical: applyRuntimeInfo treats a null
    // selection as "describes the selected session", so a settled report carrying
    // no cwd releases while the draft is on screen.
    const probe = vi.fn(async () => sampleStatus)
    stubProbe(probe)

    $currentCwd.set('/repo-previous')
    $selectedStoredSessionId.set(null)
    releaseWorkspaceCwdOwner()
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect(workspaceCwdBelongsToSelectedSession()).toBe(false)
    expect(probe).not.toHaveBeenCalled()
    expect($repoStatus.get()).toBeNull()
  })
})

// ── A fork must not repaint the parent chat's rail (#71254) ───────────────────
// The user-facing half of the fork fix. `applyRuntimeInfo` gates the OWNERSHIP
// claim for a conversation the user is not looking at, but a claim is not the
// only way the branch's folder can reach the probe: writing it to `$currentCwd`
// makes the cwd subscription re-probe with an EXPLICIT target, and an explicit
// target is trusted unconditionally (`cwd != null` skips the ownership gate). So
// the guard alone is not sufficient — the not-selected path must leave the live
// workspace untouched, which is what this pins.
//
// Modeled here rather than in use-session-actions.test.tsx on purpose: that file
// deliberately runs on real timers, and the rail's 100ms debounce plus the
// probe's promise chain need `vi.useFakeTimers()` + `drainProbeMicrotasks()` to
// be observed at all. Driving the real `applyRuntimeInfo` (the exact function
// forkBranch calls with the branch's stored id) keeps this a behavior contract
// and not a hand-modeled restatement of the store.
describe('refreshRepoStatus across a fork that leaves the parent selected', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    $repoStatus.set(null)
    $repoStatusLoading.set(false)
    $repoWorktrees.set([])
    $currentCwd.set('')
    $selectedStoredSessionId.set(null)
    $workspaceCwdOwner.set(null)
    setSessions([])
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    $repoStatus.set(null)
    $repoStatusLoading.set(false)
    $repoWorktrees.set([])
    $currentCwd.set('')
    $selectedStoredSessionId.set(null)
    $workspaceCwdOwner.set(null)
    setSessions([])
    // applyRuntimeInfo reports the backend contract, which can raise a skew
    // toast; clear it so the notification list does not leak into other files.
    clearNotifications()
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  })

  it('never probes the branch workspace while the parent chat stays selected', async () => {
    // Per-path answers, so "the branch repo published" is provable by identity:
    // the parent's rail can only change if a probe ran on the branch's folder.
    const probe = vi.fn(async (cwd: string) => (cwd === '/repo-parent' ? sampleStatus : otherRepoStatus))
    stubProbe(probe)

    // The parent conversation is settled: it owns the live workspace and its rail
    // is painted with its own repo's facts.
    $currentCwd.set('/repo-parent')
    $selectedStoredSessionId.set('stored-parent')
    setWorkspaceCwdOwner('stored-parent')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(sampleStatus)
    probe.mockClear()

    // The fork's session.create answers with the CHILD's workspace while the
    // parent is still selected. forkBranch names the branch, which is the only
    // reason this info is recognizable as somebody else's.
    setSessions([{ id: 'stored-parent' } as never, { cwd: '/repo-branch', id: 'branch-stored' } as never])

    const patch = applyRuntimeInfo({ cwd: '/repo-branch' }, 'branch-stored')

    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    // The branch's folder never reaches the probe — neither as a defaulted target
    // (ownership still names the parent) nor as an explicit one (nothing wrote it
    // to $currentCwd, so the cwd subscription never fired).
    expect(probe).not.toHaveBeenCalledWith('/repo-branch')
    // And the parent's rail is untouched: still ITS repo's facts, not blanked and
    // not repainted with the branch's.
    expect($repoStatus.get()).toEqual(sampleStatus)
    expect($currentCwd.get()).toBe('/repo-parent')
    // The path is still reported back for the branch's own per-session cache,
    // which is all a background conversation needs.
    expect(patch?.cwd).toBe('/repo-branch')
  })

  it('still repaints when the settling conversation IS the selected one', async () => {
    // The over-correction guard, in the same units as the test above: gating the
    // not-selected write must not gate the legitimate one. A resume settling for
    // the chat on screen goes through commitWorkspaceCwdForSelectedSession, so it
    // moves $currentCwd, claims ownership, and the rail follows.
    const probe = vi.fn(async (cwd: string) => (cwd === '/repo-parent' ? sampleStatus : otherRepoStatus))
    stubProbe(probe)

    $currentCwd.set('/repo-parent')
    $selectedStoredSessionId.set('stored-parent')
    setWorkspaceCwdOwner('stored-parent')
    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($repoStatus.get()).toEqual(sampleStatus)
    probe.mockClear()

    // The selected conversation relocates (the agent moved itself, or a resume
    // settles on a canonicalized path).
    applyRuntimeInfo({ cwd: '/repo-moved' }, 'stored-parent')

    vi.advanceTimersByTime(200)
    await vi.runAllTicks()
    await drainProbeMicrotasks()

    expect($currentCwd.get()).toBe('/repo-moved')
    expect(probe).toHaveBeenCalledWith('/repo-moved')
    expect($repoStatus.get()).toEqual(otherRepoStatus)
    expect(workspaceCwdBelongsToSelectedSession()).toBe(true)
  })
})
