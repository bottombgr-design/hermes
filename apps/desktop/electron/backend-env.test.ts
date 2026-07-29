import assert from 'node:assert/strict'
import path from 'node:path'

import { test } from 'vitest'

import {
  appendUniquePathEntries,
  buildDesktopBackendChildEnv,
  buildDesktopBackendEnv,
  buildDesktopBackendPath,
  buildDesktopPythonBackend,
  normalizeHermesHomeRoot,
  pathEnvKey,
  POSIX_SANE_PATH_ENTRIES
} from './backend-env'

test('desktop backend PATH adds Hermes-managed bins and missing POSIX sane entries', () => {
  const result = buildDesktopBackendPath({
    hermesHome: '/Users/test/.hermes',
    venvRoot: '/Users/test/.hermes/hermes-agent/venv',
    currentPath: '/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin',
    platform: 'darwin',
    pathModule: path.posix
  })

  const entries = result.split(':')
  assert.equal(entries[0], '/Users/test/.hermes/node/bin')
  assert.equal(entries[1], '/Users/test/.hermes/hermes-agent/venv/bin')
  assert.ok(entries.includes('/opt/homebrew/bin'), 'Apple Silicon Homebrew bin is added')
  assert.ok(entries.includes('/opt/homebrew/sbin'), 'Apple Silicon Homebrew sbin is added')
  assert.ok(entries.includes('/usr/local/sbin'), 'missing standard sbin is added')

  for (const expected of POSIX_SANE_PATH_ENTRIES) {
    assert.ok(entries.includes(expected), `${expected} should be present`)
  }
})

test('desktop backend PATH preserves first occurrence and avoids duplicates', () => {
  const result = buildDesktopBackendPath({
    hermesHome: '/Users/test/.hermes',
    venvRoot: '/Users/test/.hermes/hermes-agent/venv',
    currentPath: '/opt/homebrew/bin:/usr/bin:/opt/homebrew/bin:/bin',
    platform: 'darwin',
    pathModule: path.posix
  })

  const entries = result.split(':')
  assert.equal(entries.filter(entry => entry === '/opt/homebrew/bin').length, 1)
  assert.ok(
    entries.indexOf('/opt/homebrew/bin') < entries.indexOf('/opt/homebrew/sbin'),
    'existing Homebrew bin keeps its precedence over appended missing sane entries'
  )
})

test('buildDesktopBackendEnv replaces inherited PYTHONPATH and extends backend PATH', () => {
  const env = buildDesktopBackendEnv({
    hermesHome: '/Users/test/.hermes',
    pythonPathEntries: ['/repo/hermes-agent'],
    venvRoot: '/Users/test/.hermes/hermes-agent/venv',
    currentEnv: {
      PATH: '/usr/bin:/bin',
      PYTHONPATH: '/existing/pythonpath'
    },
    platform: 'darwin',
    pathModule: path.posix
  })

  assert.equal(env.PYTHONPATH, '/repo/hermes-agent')
  assert.equal(env.VIRTUAL_ENV, '/Users/test/.hermes/hermes-agent/venv')
  assert.ok(env.PATH.startsWith('/Users/test/.hermes/node/bin:/Users/test/.hermes/hermes-agent/venv/bin:'))
  assert.ok(env.PATH.includes('/opt/homebrew/bin'))
})

test('actual system-Python resolver wiring strips inherited virtualenv state', () => {
  const foreignVenv = '/tmp/foreign/.venv'

  const currentEnv = {
    PATH: `${foreignVenv}/bin:/custom/bin:/usr/bin`,
    PYTHONHOME: '/tmp/foreign/python-home',
    PYTHONPATH: `${foreignVenv}/lib/python3.11/site-packages`,
    SAFE_PARENT_VALUE: 'preserved',
    VIRTUAL_ENV: foreignVenv
  }

  const backend = buildDesktopPythonBackend({
    root: '/repo/hermes-agent',
    label: 'Hermes system Python fallback',
    backendArgs: ['serve', '--port', '0'],
    command: '/usr/bin/python3',
    runtimeVenvRoot: null,
    sitePackagesEntries: [],
    hermesHome: '/home/test/.hermes',
    currentEnv,
    platform: 'linux',
    pathModule: path.posix
  })

  assert.equal(backend.command, '/usr/bin/python3')
  assert.deepEqual(backend.args, ['-m', 'hermes_cli.main', 'serve', '--port', '0'])
  assert.equal(backend.env.VIRTUAL_ENV, undefined)
  assert.equal(backend.env.PYTHONPATH, '/repo/hermes-agent')
  assert.equal(backend.env.PATH.split(':').includes(`${foreignVenv}/bin`), false)
  assert.ok(backend.env.PATH.split(':').includes('/custom/bin'))

  const childEnv = buildDesktopBackendChildEnv({
    currentEnv,
    backendEnv: backend.env,
    overrides: { HERMES_HOME: '/home/test/.hermes' }
  })

  assert.equal(childEnv.VIRTUAL_ENV, undefined)
  assert.equal(childEnv.PYTHONHOME, undefined)
  assert.equal(childEnv.PYTHONPATH, '/repo/hermes-agent')
  assert.equal(childEnv.PATH.split(':').includes(`${foreignVenv}/bin`), false)
  assert.equal(childEnv.SAFE_PARENT_VALUE, 'preserved')
  assert.equal(childEnv.HERMES_HOME, '/home/test/.hermes')
})

test('buildDesktopBackendEnv forces PYTHONUTF8 unless the user set it explicitly', () => {
  const defaulted = buildDesktopBackendEnv({
    hermesHome: '/Users/test/.hermes',
    currentEnv: { PATH: '/usr/bin' },
    platform: 'darwin',
    pathModule: path.posix
  })

  assert.equal(defaulted.PYTHONUTF8, '1')

  const optedOut = buildDesktopBackendEnv({
    hermesHome: '/Users/test/.hermes',
    currentEnv: { PATH: '/usr/bin', PYTHONUTF8: '0' },
    platform: 'darwin',
    pathModule: path.posix
  })

  assert.equal(optedOut.PYTHONUTF8, '0')
})

test('normalizeHermesHomeRoot maps profile homes back to the global Hermes root', () => {
  assert.equal(
    normalizeHermesHomeRoot('/Users/test/.hermes/profiles/oracle', { pathModule: path.posix }),
    '/Users/test/.hermes'
  )
  assert.equal(
    normalizeHermesHomeRoot('C:\\Users\\test\\AppData\\Local\\hermes\\profiles\\oracle', { pathModule: path.win32 }),
    'C:\\Users\\test\\AppData\\Local\\hermes'
  )
  assert.equal(normalizeHermesHomeRoot('/Users/test/.hermes', { pathModule: path.posix }), '/Users/test/.hermes')
})

test('Windows PATH casing and delimiter are preserved without POSIX sane entries', () => {
  const foreignVenv = 'C:\\Users\\test\\legacy-venv'

  const env = buildDesktopBackendEnv({
    hermesHome: 'C:\\Users\\test\\AppData\\Local\\hermes',
    pythonPathEntries: ['C:\\repo\\hermes-agent'],
    venvRoot: 'C:\\Users\\test\\AppData\\Local\\hermes\\hermes-agent\\venv',
    currentEnv: {
      Path: `${foreignVenv}\\Scripts;C:\\Windows\\System32;C:\\Windows`,
      PYTHONPATH: 'C:\\existing\\pythonpath',
      virtual_env: foreignVenv
    },
    platform: 'win32',
    pathModule: path.win32
  })

  assert.equal(pathEnvKey({ Path: 'x' }, 'win32'), 'Path')

  assert.equal(env.PATH, undefined)
  assert.ok(env.Path.startsWith('C:\\Users\\test\\AppData\\Local\\hermes\\node\\bin;'))
  assert.ok(env.Path.includes('\\venv\\Scripts;'))
  assert.ok(env.Path.includes(';C:\\Windows\\System32;C:\\Windows'))
  assert.equal(env.Path.toLowerCase().includes(`${foreignVenv}\\scripts`.toLowerCase()), false)
  assert.equal(env.Path.includes('/opt/homebrew/bin'), false)
  assert.equal(env.VIRTUAL_ENV, 'C:\\Users\\test\\AppData\\Local\\hermes\\hermes-agent\\venv')

  const childEnv = buildDesktopBackendChildEnv({
    currentEnv: {
      Path: `${foreignVenv}\\SCRIPTS;C:\\Windows\\System32`,
      virtual_env: foreignVenv
    },
    platform: 'win32',
    pathModule: path.win32
  })

  assert.equal(childEnv.virtual_env, undefined)
  assert.equal(childEnv.Path.toLowerCase().includes(`${foreignVenv}\\scripts`.toLowerCase()), false)
})

test('appendUniquePathEntries drops empty entries and keeps first occurrence', () => {
  assert.equal(appendUniquePathEntries([':/a::/b', ['/a', '/c']], { delimiter: ':' }), '/a:/b:/c')
})
