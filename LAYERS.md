# Layers from issue #74561
1. Config load-time resolution: `_load_config_impl()` in `hermes_cli/config.py` → after `_expand_env_vars()`, resolve `model.key_env` / `model.api_key_env` into `model.api_key`
2. Credential pool defense-in-depth: `credential_pool.py` model-config seed path (~L2717) → also resolve `key_env` so all paths are consistent
3. Edge case: `key_env` set but env var missing → leave `api_key` empty (same behavior as other paths)
4. Edge case: both `key_env` and inline `api_key` set → inline `api_key` wins (don't overwrite explicit key)
5. Edge case: `api_key_env` alias → already normalized to `key_env` by `_normalize_root_model_keys()`
