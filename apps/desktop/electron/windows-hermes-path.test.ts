// Unit tests for the pure Windows `hermes` resolution helpers extracted from
// main.ts's findOnPath(), handOffWindowsBootstrapRecovery(), and
// unwrapWindowsVenvHermesCommand(). These pin the two Windows resolution bugs
// that caused desktop reinstall loops:
//   1. buildPathExtCandidates() — PATHEXT extensions must be tried BEFORE the
//      empty extension, or an extensionless Git-Bash `hermes` shim shadows
//      the real hermes.cmd/hermes.exe.
//   2. chooseUpdaterArgs() — must gate on haveRealInstall (any real-install
//      signal), not just the hermes.exe console-script shim, or healthy
//      installs get forced into a destructive --repair.
//   3. resolveVenvHermesCommand() — must probe the venv python via
//      canImportHermesCli() before trusting it, or a broken venv gets
//      re-selected forever instead of falling through to bootstrap.

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'

import { test } from 'vitest'

import {
  buildPathExtCandidates,
  chooseUpdaterArgs,
  getVenvSitePackagesEntries,
  resolveVenvHermesCommand
} from './windows-hermes-path'

test('buildPathExtCandidates: Windows tries PATHEXT extensions before the empty extension', () => {
  const extensions = buildPathExtCandidates('.COM;.EXE;.BAT;.CMD', true)

  assert.deepEqual(extensions, ['.COM', '.EXE', '.BAT', '.CMD', ''])
  assert.equal(extensions[extensions.length - 1], '', 'empty extension must be last, not first')
  assert.notEqual(extensions[0], '', 'the buggy empty-extension-first order must not return')
})

test('buildPathExtCandidates: defaults to .COM;.EXE;.BAT;.CMD when PATHEXT is unset on Windows', () => {
  assert.deepEqual(buildPathExtCandidates(undefined, true), ['.COM', '.EXE', '.BAT', '.CMD', ''])
})

test('buildPathExtCandidates: respects a custom PATHEXT, still empty-last', () => {
  assert.deepEqual(buildPathExtCandidates('.EXE;.PS1', true), ['.EXE', '.PS1', ''])
})

test('buildPathExtCandidates: non-Windows only tries the bare name', () => {
  assert.deepEqual(buildPathExtCandidates('.COM;.EXE;.BAT;.CMD', false), [''])
  assert.deepEqual(buildPathExtCandidates(undefined, false), [''])
})

test('chooseUpdaterArgs: gentle --update when a real-install signal is present', () => {
  assert.deepEqual(chooseUpdaterArgs(true, 'main'), ['--update', '--branch', 'main'])
})

test('chooseUpdaterArgs: destructive --repair only when NO real-install signal is present', () => {
  assert.deepEqual(chooseUpdaterArgs(false, 'main'), ['--repair', '--branch', 'main'])
})

test('chooseUpdaterArgs: passes the branch through unchanged in both cases', () => {
  assert.deepEqual(chooseUpdaterArgs(true, 'release/1.2'), ['--update', '--branch', 'release/1.2'])
  assert.deepEqual(chooseUpdaterArgs(false, 'release/1.2'), ['--repair', '--branch', 'release/1.2'])
})

function makeDeps(overrides: Partial<Parameters<typeof resolveVenvHermesCommand>[2]> = {}) {
  return {
    isWindows: true,
    isCommandScript: () => false,
    fileExists: () => true,
    directoryExists: () => false,
    canImportHermesCli: () => true,
    getVenvPython: (venvRoot: string) => `${venvRoot}/Scripts/python.exe`,
    getVenvSitePackagesEntries: () => [],
    buildDesktopBackendEnv: () => ({ FAKE_ENV: '1' }),
    hermesHome: '/fake/hermes-home',
    resolvePath: (...segments: string[]) => segments.join('/').replace(/\/+/g, '/'),
    dirname: (p: string) => p.slice(0, p.lastIndexOf('/')) || '/',
    basename: (p: string) => p.slice(p.lastIndexOf('/') + 1),
    rememberLog: () => {},
    ...overrides
  }
}

test('resolveVenvHermesCommand: returns null off Windows', () => {
  const deps = makeDeps({ isWindows: false })

  assert.equal(resolveVenvHermesCommand('/root/venv/Scripts/hermes.exe', [], deps), null)
})

test('resolveVenvHermesCommand: returns null for a .cmd/.bat script command', () => {
  const deps = makeDeps({ isCommandScript: () => true })

  assert.equal(resolveVenvHermesCommand('/root/venv/Scripts/hermes.cmd', [], deps), null)
})

test('resolveVenvHermesCommand: returns null when the basename is not hermes/hermes.exe', () => {
  const deps = makeDeps()

  assert.equal(resolveVenvHermesCommand('/root/venv/Scripts/python.exe', [], deps), null)
})

test('resolveVenvHermesCommand: returns null when the parent dir is not Scripts', () => {
  const deps = makeDeps()

  assert.equal(resolveVenvHermesCommand('/root/venv/bin/hermes.exe', [], deps), null)
})

test('resolveVenvHermesCommand: returns null when the venv python does not exist on disk', () => {
  const deps = makeDeps({ fileExists: () => false })

  assert.equal(resolveVenvHermesCommand('/root/venv/Scripts/hermes.exe', [], deps), null)
})

test('resolveVenvHermesCommand: probes the venv python before trusting it (returns null on failed probe)', () => {
  let probed = false

  const deps = makeDeps({
    canImportHermesCli: (python: string) => {
      probed = true
      assert.equal(python, '/root/venv/Scripts/python.exe')

      return false
    }
  })

  const result = resolveVenvHermesCommand('/root/venv/Scripts/hermes.exe', ['serve'], deps)

  assert.equal(probed, true, 'must probe the venv interpreter; a broken venv must not be re-selected forever')
  assert.equal(result, null, 'a failed probe must fall through (return null) so the resolver reaches bootstrap')
})

test('resolveVenvHermesCommand: returns the resolved python backend descriptor when the probe passes', () => {
  const deps = makeDeps()
  const result = resolveVenvHermesCommand('/root/venv/Scripts/hermes.exe', ['serve', '--port', '0'], deps)

  assert.ok(result, 'a passing probe must return a backend descriptor, not null')
  assert.equal(result.command, '/root/venv/Scripts/python.exe')
  assert.deepEqual(result.args, ['-m', 'hermes_cli.main', 'serve', '--port', '0'])
  assert.equal(result.bootstrap, false)
  assert.equal(result.kind, 'python')
  assert.equal(result.shell, false)
  assert.deepEqual(result.env, { FAKE_ENV: '1' })
})

test('resolveVenvHermesCommand: is case-insensitive on hermes.exe and the Scripts dir name', () => {
  const deps = makeDeps()

  assert.ok(resolveVenvHermesCommand('/root/venv/Scripts/HERMES.EXE', [], deps))
  assert.ok(resolveVenvHermesCommand('/root/venv/SCRIPTS/hermes.exe', [], deps))
})

// ── getVenvSitePackagesEntries ─────────────────────────────────────────────

test('getVenvSitePackagesEntries: returns Lib/site-packages on Windows when it exists', () => {
  const expected = path.join('C:\\venv', 'Lib', 'site-packages')

  const result = getVenvSitePackagesEntries('C:\\venv', {
    isWindows: true,
    directoryExists: p => p === expected
  })

  assert.deepEqual(result, [expected])
})

test('getVenvSitePackagesEntries: returns empty on Windows when site-packages does not exist', () => {
  const result = getVenvSitePackagesEntries('C:\\venv', {
    isWindows: true,
    directoryExists: () => false
  })

  assert.deepEqual(result, [])
})

test('getVenvSitePackagesEntries: reads pyvenv.cfg version on POSIX and resolves lib/pythonX.Y/site-packages', () => {
  const result = getVenvSitePackagesEntries('/venv', {
    isWindows: false,
    directoryExists: p => p === '/venv/lib/python3.12/site-packages',
    readFile: () => 'version_info = 3.12.1\n'
  })

  assert.deepEqual(result, ['/venv/lib/python3.12/site-packages'])
})

test('getVenvSitePackagesEntries: returns empty on POSIX when pyvenv.cfg is missing', () => {
  const result = getVenvSitePackagesEntries('/venv', {
    isWindows: false,
    directoryExists: () => true,
    readFile: () => undefined
  })

  assert.deepEqual(result, [])
})

test('getVenvSitePackagesEntries: returns empty on POSIX when pyvenv.cfg has no version_info', () => {
  const result = getVenvSitePackagesEntries('/venv', {
    isWindows: false,
    directoryExists: () => true,
    readFile: () => 'home = /usr/bin\n'
  })

  assert.deepEqual(result, [])
})

test('getVenvSitePackagesEntries: returns empty on POSIX when version is present but site-packages dir is absent', () => {
  const result = getVenvSitePackagesEntries('/venv', {
    isWindows: false,
    directoryExists: () => false,
    readFile: () => 'version_info = 3.11\n'
  })

  assert.deepEqual(result, [])
})

test('getVenvSitePackagesEntries: returns empty for a falsy venvRoot', () => {
  assert.deepEqual(getVenvSitePackagesEntries('', { isWindows: true, directoryExists: () => true }), [])
  assert.deepEqual(getVenvSitePackagesEntries(null, { isWindows: true, directoryExists: () => true }), [])
  assert.deepEqual(getVenvSitePackagesEntries(undefined, { isWindows: true, directoryExists: () => true }), [])
})

// ── Regression guards for #62792: resolveBasePythonFromVenvCfg + ensureRuntime ──

function readMain() {
  return fs.readFileSync(path.join(__dirname, 'main.ts'), 'utf8').replace(/\r\n/g, '\n')
}

test('main.ts defines resolveBasePythonFromVenvCfg to read pyvenv.cfg home key (#62792 L1)', () => {
  const source = readMain()
  assert.ok(
    source.includes('function resolveBasePythonFromVenvCfg'),
    'resolveBasePythonFromVenvCfg must exist in main.ts'
  )
  // Must parse the `home` key from pyvenv.cfg.
  assert.ok(
    source.includes('cfg.match(/^home\\s*=\\s*(.+)$/im)'),
    'must parse the pyvenv.cfg home key'
  )
  // Must resolve base Python from the home directory.
  assert.ok(
    source.includes("path.join(baseDir, 'python.exe')"),
    'must resolve base Python from home directory'
  )
  // Must fail closed — return null on any error so the venv fallback is always preserved.
  assert.ok(
    source.includes('catch') && source.includes('return null'),
    'must fail closed on any error'
  )
  // Must gate on IS_WINDOWS only.
  assert.ok(
    source.includes('!IS_WINDOWS'),
    'must be gated to Windows only'
  )
})

test('main.ts resolveBasePythonFromVenvCfg verifies resolved interpreter exists (#62792 L1)', () => {
  const fnStart = readMain().indexOf('function resolveBasePythonFromVenvCfg')
  assert.notEqual(fnStart, -1, 'resolveBasePythonFromVenvCfg must exist')
  const source = readMain()
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1)
  const body = source.slice(fnStart, fnEnd === -1 ? undefined : fnEnd)

  assert.ok(
    body.includes('fileExists(basePython)'),
    'must verify the resolved python.exe exists on disk before trusting it'
  )
})

test('ensureRuntime reads pyvenv.cfg base Python on Windows, falls back to venv (#62792 L2)', () => {
  const source = readMain()
  const fnStart = source.indexOf('async function ensureRuntime(')
  assert.notEqual(fnStart, -1, 'ensureRuntime must exist')
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1)
  const body = source.slice(fnStart, fnEnd === -1 ? undefined : fnEnd)

  assert.match(
    body,
    /const basePython = IS_WINDOWS \? resolveBasePythonFromVenvCfg\(VENV_ROOT\) : null/,
    'ensureRuntime must resolve base Python from pyvenv.cfg on Windows'
  )
  assert.match(
    body,
    /backend\.command = basePython \|\| venvPython/,
    'ensureRuntime must prefer basePython, falling back to venvPython'
  )
  // The old unconditional overwrite that causes #62792 must NOT be present.
  assert.doesNotMatch(
    body,
    /backend\.command = getVenvPython\(VENV_ROOT\)/,
    'ensureRuntime must not unconditionally overwrite backend.command with the venv launcher'
  )
})

test('primary-backend-startup.ts passes the backend through ensureLocalRuntime (#62792 L6)', () => {
  const srcPath = path.join(__dirname, 'primary-backend-startup.ts')
  assert.ok(fs.existsSync(srcPath), 'primary-backend-startup.ts must exist')
  const source = fs.readFileSync(srcPath, 'utf8').replace(/\r\n/g, '\n')

  assert.match(
    source,
    /return \{ kind: 'local', backend: await ensureLocalRuntime\(backend\) \}/,
    'the active backend must pass through ensureRuntime before the process is spawned'
  )
})
