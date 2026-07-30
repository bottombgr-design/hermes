# Layers from issue #62792

1. **Desktop backend resolves to venv python.exe** — `createActiveBackend()` at `apps/desktop/electron/main.ts:3788` calls `getVenvPython(VENV_ROOT)` which returns `venv/Scripts/python.exe`. This is the venv launcher that loads native .pyd extensions from the venv.
   → `apps/desktop/electron/main.ts:3787-3813`

2. **ensureRuntime unconditionally overwrites backend.command** — After `createActiveBackend()` returns, `ensureRuntime()` at line 4139 unconditionally sets `backend.command = getVenvPython(VENV_ROOT)`, discarding any base Python resolution done earlier.
   → `apps/desktop/electron/main.ts:3984-4152`

3. **Venal Python holds .pyd locks blocking updates** — Because the Desktop backend process runs from `venv/Scripts/python.exe`, Windows locks the native .pyd extensions loaded from the venv. When `hermes update` runs `uv pip install -e .`, it fails with access-denied on the locked .pyd files, leaving the install half-updated.
   → root cause: process uses venv interpreter instead of system/base Python

4. **[Edge case] Missing pyvenv.cfg or base Python** — `resolveBasePythonFromVenvCfg()` must return `null` (fail closed) when pyvenv.cfg is missing, malformed, has no `home` key, or the resolved base Python doesn't exist. The caller falls back to `venvPython`.
   → `apps/desktop/electron/main.ts` (new helper function)

5. **[Edge case] Non-Windows platforms unchanged** — The base Python resolution must be gated on `IS_WINDOWS`. On macOS/Linux, behavior is unchanged — continue using the venv interpreter.
   → `apps/desktop/electron/main.ts:3626` (`IS_WINDOWS ? resolveBasePythonFromVenvCfg(VENV_ROOT) : null`)

6. **[Edge case] startup path must pass through ensureRuntime** — The local backend startup in `runPrimaryBackendStartup()` calls `ensureLocalRuntime(backend)` which maps to `ensureRuntime()`. The fix must be placed in `ensureRuntime()` (the final pre-spawn path) so it applies regardless of which code path creates the backend.
   → `apps/desktop/electron/primary-backend-startup.ts:65`
