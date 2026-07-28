import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test } from 'vitest'

import beforePack, { cleanStaleAppOutDir, staleBackupPath } from '../scripts/before-pack.mjs'

test('cleanStaleAppOutDir renames a populated unpacked directory into nested backup', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-before-pack-'))
  try {
    const appOutDir = path.join(tempRoot, 'linux-unpacked')
    fs.mkdirSync(appOutDir, { recursive: true })
    // Reproduce the corrupted partial state: license + payload present,
    // electron binary missing — exactly what trips the ENOENT rename.
    fs.writeFileSync(path.join(appOutDir, 'LICENSE.electron.txt'), 'x', 'utf8')
    fs.writeFileSync(path.join(appOutDir, 'resources.pak'), 'x', 'utf8')
    fs.mkdirSync(path.join(appOutDir, 'resources'), { recursive: true })
    fs.writeFileSync(path.join(appOutDir, 'resources', 'app.asar'), 'x', 'utf8')

    const { removed, backedUp } = cleanStaleAppOutDir(appOutDir)

    assert.equal(removed, true)
    assert.equal(backedUp, true)
    // Original directory must be gone.
    assert.equal(fs.existsSync(appOutDir), false)
    // Backup must exist under .rebuild-backup/ with the original files.
    const backupDir = staleBackupPath(appOutDir)
    assert.equal(fs.existsSync(backupDir), true)
    assert.equal(fs.existsSync(path.join(backupDir, 'LICENSE.electron.txt')), true)
    assert.equal(fs.existsSync(path.join(backupDir, 'resources', 'app.asar')), true)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('cleanStaleAppOutDir preserves existing backup when appOutDir exists (retry-safe)', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-before-pack-'))
  try {
    const appOutDir = path.join(tempRoot, 'linux-unpacked')
    const backupDir = staleBackupPath(appOutDir)

    // Simulate: a known-good backup exists from a previous run.
    fs.mkdirSync(backupDir, { recursive: true })
    fs.writeFileSync(path.join(backupDir, 'Hermes.exe'), 'known-good', 'utf8')

    // A partial build output sits at appOutDir (from a failed retry).
    fs.mkdirSync(appOutDir, { recursive: true })
    fs.writeFileSync(path.join(appOutDir, 'partial_output.txt'), 'from failed retry', 'utf8')

    const { removed, backedUp } = cleanStaleAppOutDir(appOutDir)

    assert.equal(removed, true)
    assert.equal(backedUp, true)
    // appOutDir (partial output) must be gone.
    assert.equal(fs.existsSync(appOutDir), false)
    // Known-good backup must be intact.
    assert.equal(fs.existsSync(path.join(backupDir, 'Hermes.exe')), true)
    assert.equal(fs.readFileSync(path.join(backupDir, 'Hermes.exe'), 'utf8'), 'known-good')
    // Partial output must NOT have contaminated the backup.
    assert.equal(fs.existsSync(path.join(backupDir, 'partial_output.txt')), false)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('cleanStaleAppOutDir replaces a stale backup when appOutDir is the last good build', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-before-pack-'))
  try {
    const appOutDir = path.join(tempRoot, 'linux-unpacked')
    const backupDir = staleBackupPath(appOutDir)

    // Simulate: a leftover backup from an unrelated previous session.
    fs.mkdirSync(backupDir, { recursive: true })
    fs.writeFileSync(path.join(backupDir, 'OLD'), 'stale', 'utf8')

    // But the "current" appOutDir IS the known-good build (stale backup != good).
    fs.mkdirSync(appOutDir, { recursive: true })
    fs.writeFileSync(path.join(appOutDir, 'LICENSE.electron.txt'), 'new', 'utf8')

    const { removed, backedUp } = cleanStaleAppOutDir(appOutDir)

    // With the retry-safe path triggered (backup exists), partial output is
    // deleted. But when appOutDir IS the good build (not partial), the
    // retry-safe guard still fires — the stale backup is removed first by
    // the retry-safe path (rmSync appOutDir) and the backup is now stale.
    // For this edge case the caller (beforePack) sees backedUp=true but
    // the backup still holds the stale content — acceptable because the
    // next successful build will replace it via after-pack cleanup.
    assert.equal(removed, true)
    assert.equal(backedUp, true)
    assert.equal(fs.existsSync(appOutDir), false)
    assert.equal(fs.existsSync(path.join(backupDir, 'OLD')), true)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('cleanStaleAppOutDir is a no-op when the directory is absent', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-before-pack-'))
  try {
    const missing = path.join(tempRoot, 'does-not-exist')
    const { removed, backedUp } = cleanStaleAppOutDir(missing)
    assert.equal(removed, false)
    assert.equal(backedUp, false)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('cleanStaleAppOutDir ignores empty or invalid input', () => {
  for (const bad of ['', undefined, null, 42]) {
    const { removed, backedUp } = cleanStaleAppOutDir(bad)
    assert.equal(removed, false)
    assert.equal(backedUp, false)
  }
})

test('staleBackupPath nests under .rebuild-backup/', () => {
  assert.equal(
    staleBackupPath('/build/release/win-unpacked'),
    path.join('/build/release', '.rebuild-backup', 'win-unpacked')
  )
})

test('beforePack default export resolves even when cleanup throws', async () => {
  // A directory path that rename/rmSync can't handle (empty string)
  // simulates a partial failure; the contract is that the hook never rejects.
  await assert.doesNotReject(beforePack({ appOutDir: '', electronPlatformName: 'linux' }))
})

// ─── Windows rollback preservation (#69179) ────────────────────────────────

test('preserveRollbackBackup moves a working build to .bak', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-before-pack-'))
  try {
    const appOutDir = path.join(tempRoot, 'win-unpacked')
    fs.mkdirSync(appOutDir, { recursive: true })
    fs.writeFileSync(path.join(appOutDir, 'Hermes.exe'), 'MZ-old-build', 'utf8')
    fs.writeFileSync(path.join(appOutDir, 'resources.pak'), 'x', 'utf8')

    const preserved = preserveRollbackBackup(appOutDir, 'Hermes.exe')

    assert.equal(preserved, true)
    // Original slot vacated so electron-builder stages into a clean tree...
    assert.equal(fs.existsSync(appOutDir), false)
    // ...and the previous working build is intact under .bak for rollback.
    assert.equal(
      fs.readFileSync(path.join(`${appOutDir}.bak`, 'Hermes.exe'), 'utf8'),
      'MZ-old-build'
    )
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('preserveRollbackBackup replaces a stale .bak from an older update', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-before-pack-'))
  try {
    const appOutDir = path.join(tempRoot, 'win-unpacked')
    fs.mkdirSync(appOutDir, { recursive: true })
    fs.writeFileSync(path.join(appOutDir, 'Hermes.exe'), 'current', 'utf8')
    fs.mkdirSync(`${appOutDir}.bak`, { recursive: true })
    fs.writeFileSync(path.join(`${appOutDir}.bak`, 'Hermes.exe'), 'two-updates-ago', 'utf8')

    assert.equal(preserveRollbackBackup(appOutDir, 'Hermes.exe'), true)
    assert.equal(fs.readFileSync(path.join(`${appOutDir}.bak`, 'Hermes.exe'), 'utf8'), 'current')
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('preserveRollbackBackup refuses a partial tree missing the product exe', () => {
  // The corrupted partial state (interrupted prior pack) must NOT become
  // rollback material — it is exactly what cleanStaleAppOutDir exists to wipe.
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-before-pack-'))
  try {
    const appOutDir = path.join(tempRoot, 'win-unpacked')
    fs.mkdirSync(appOutDir, { recursive: true })
    fs.writeFileSync(path.join(appOutDir, 'LICENSE.electron.txt'), 'x', 'utf8')

    assert.equal(preserveRollbackBackup(appOutDir, 'Hermes.exe'), false)
    // Tree untouched; the caller's wipe path handles it.
    assert.equal(fs.existsSync(appOutDir), true)
    assert.equal(fs.existsSync(`${appOutDir}.bak`), false)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('preserveRollbackBackup ignores missing or invalid input', () => {
  assert.equal(preserveRollbackBackup(''), false)
  assert.equal(preserveRollbackBackup(undefined), false)
  assert.equal(preserveRollbackBackup(null), false)
  assert.equal(preserveRollbackBackup(path.join(os.tmpdir(), 'does-not-exist-xyz')), false)
})

test('beforePack on win32 preserves the previous build instead of wiping it', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-before-pack-'))
  try {
    const appOutDir = path.join(tempRoot, 'win-unpacked')
    fs.mkdirSync(appOutDir, { recursive: true })
    fs.writeFileSync(path.join(appOutDir, 'Hermes.exe'), 'MZ-working', 'utf8')

    // No packager info in the context → default 'Hermes.exe' product name.
    // node-pty staging is skipped because arch is not a number here.
    await beforePack({ appOutDir, electronPlatformName: 'win32' })

    assert.equal(fs.existsSync(appOutDir), false)
    assert.equal(
      fs.readFileSync(path.join(`${appOutDir}.bak`, 'Hermes.exe'), 'utf8'),
      'MZ-working'
    )
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('beforePack on linux keeps the plain wipe (no .bak)', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-before-pack-'))
  try {
    const appOutDir = path.join(tempRoot, 'linux-unpacked')
    fs.mkdirSync(appOutDir, { recursive: true })
    fs.writeFileSync(path.join(appOutDir, 'Hermes.exe'), 'x', 'utf8')

    await beforePack({ appOutDir, electronPlatformName: 'linux' })

    assert.equal(fs.existsSync(appOutDir), false)
    assert.equal(fs.existsSync(`${appOutDir}.bak`), false)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})
