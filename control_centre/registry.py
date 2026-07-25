"""Safe, fail-soft loader for the Control Centre product registry."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import yaml

from control_centre.schema import Freshness, ProductSummary, SourceError, SourceRef

_API_VERSION = "control-centre/v1"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SECRET_KEY_PARTS = {"token", "password", "secret", "private_key", "dsn"}
_PRODUCT_FIELDS = {
    "id",
    "name",
    "repositories",
    "kanban",
    "knowledge_roots",
    "environments",
    "configuration_state",
}
_REPOSITORY_FIELDS = {"id", "path", "remote", "default_branch"}
_KANBAN_FIELDS = {"board", "tenant"}
_ENVIRONMENT_FIELDS = {"name", "production"}


@dataclass(frozen=True)
class RegistryResult:
    products: tuple[ProductSummary, ...] = ()
    errors: tuple[SourceError, ...] = ()


def _source(source_id: str, observed_at: float) -> SourceRef:
    return SourceRef(
        source="registry",
        source_id=source_id,
        observed_at=observed_at,
        freshness=Freshness.UNAVAILABLE,
    )


def _error(
    source_id: str,
    observed_at: float,
    message: str,
    recovery: str,
    *,
    severity: str = "error",
) -> SourceError:
    return SourceError(
        source=_source(source_id, observed_at),
        message=message,
        recovery=recovery,
        severity=severity,
    )


def _secret_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(
                normalized == part or normalized.endswith(f"_{part}")
                for part in _SECRET_KEY_PARTS
            ):
                return str(key)
            nested = _secret_key(child)
            if nested:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _secret_key(child)
            if nested:
                return nested
    return None


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _reject_unknown(mapping: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unknown field: {unknown[0]}")


def _validate_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a stable lowercase identifier")
    return value


def _validated_path(value: Any, allowed_roots: tuple[Path, ...]) -> str:
    if not isinstance(value, str):
        raise ValueError("path must be a string")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    resolved = path.resolve(strict=False)
    if not allowed_roots or not any(
        resolved == root or resolved.is_relative_to(root) for root in allowed_roots
    ):
        raise ValueError("path is outside configured roots")
    return str(resolved)


def _parse_product(
    raw: Any,
    observed_at: float,
    allowed_roots: tuple[Path, ...],
) -> ProductSummary:
    product = _require_mapping(raw, "product")
    source_id = str(product.get("id") or "invalid-product")
    secret_key = _secret_key(product)
    if secret_key:
        raise ValueError(f"product contains secret-shaped key: {secret_key}")
    _reject_unknown(product, _PRODUCT_FIELDS, "product")

    product_id = _validate_id(product.get("id"), "product id")
    name = product.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("product name must be a non-empty string")
    configuration_state = product.get("configuration_state", "configured")
    if configuration_state not in {"configured", "incomplete"}:
        raise ValueError("configuration_state must be configured or incomplete")

    repositories: list[dict[str, Any]] = []
    raw_repositories = product.get("repositories", [])
    if not isinstance(raw_repositories, list):
        raise ValueError("repositories must be a list")
    repository_ids: set[str] = set()
    for raw_repository in raw_repositories:
        repository = _require_mapping(raw_repository, "repository")
        _reject_unknown(repository, _REPOSITORY_FIELDS, "repository")
        repository_id = _validate_id(repository.get("id"), "repository id")
        if repository_id in repository_ids:
            raise ValueError(f"duplicate repository id: {repository_id}")
        repository_ids.add(repository_id)
        parsed_repository: dict[str, Any] = {
            "id": repository_id,
            "path": _validated_path(repository.get("path"), allowed_roots),
        }
        for field in ("remote", "default_branch"):
            value = repository.get(field)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"repository {field} must be a non-empty string")
                parsed_repository[field] = value
        repositories.append(parsed_repository)

    knowledge_roots_raw = product.get("knowledge_roots", [])
    if not isinstance(knowledge_roots_raw, list):
        raise ValueError("knowledge_roots must be a list")
    knowledge_roots = tuple(
        _validated_path(value, allowed_roots) for value in knowledge_roots_raw
    )

    environments: list[dict[str, Any]] = []
    raw_environments = product.get("environments", [])
    if not isinstance(raw_environments, list):
        raise ValueError("environments must be a list")
    for raw_environment in raw_environments:
        environment = _require_mapping(raw_environment, "environment")
        _reject_unknown(environment, _ENVIRONMENT_FIELDS, "environment")
        env_name = environment.get("name")
        production = environment.get("production", False)
        if not isinstance(env_name, str) or not env_name.strip():
            raise ValueError("environment name must be a non-empty string")
        if not isinstance(production, bool):
            raise ValueError("environment production must be boolean")
        environments.append({"name": env_name, "production": production})

    kanban_href = None
    raw_kanban = product.get("kanban")
    if raw_kanban is not None:
        kanban = _require_mapping(raw_kanban, "kanban")
        _reject_unknown(kanban, _KANBAN_FIELDS, "kanban")
        query: dict[str, str] = {}
        for key in ("board", "tenant"):
            value = kanban.get(key)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"kanban {key} must be a non-empty string")
                query[key] = value
        kanban_href = f"/kanban?{urlencode(query)}" if query else "/kanban"

    return ProductSummary(
        source=SourceRef(
            source="registry",
            source_id=product_id,
            observed_at=observed_at,
            freshness=Freshness.LIVE,
            href=f"/control-centre?product={product_id}",
        ),
        product_id=product_id,
        name=name,
        configuration_state=configuration_state,
        repositories=tuple(repositories),
        kanban_href=kanban_href,
        chat_href=f"/chat?product={product_id}",
        sessions_href=f"/sessions?product={product_id}",
        knowledge_roots=knowledge_roots,
        environments=tuple(environments),
        recovery=(
            "Complete this product in products.yaml"
            if configuration_state == "incomplete"
            else None
        ),
    )


def load_product_registry(
    path: Path,
    *,
    observed_at: float | None = None,
    allowed_roots: Iterable[Path] = (),
) -> RegistryResult:
    """Load valid products while preserving a visible error for each bad row."""

    now = time.time() if observed_at is None else observed_at
    path = Path(path)
    roots = tuple(Path(root).resolve(strict=False) for root in allowed_roots)
    if not path.exists():
        return RegistryResult(
            errors=(
                _error(
                    "products.yaml",
                    now,
                    "Product registry is not configured",
                    "Create products.yaml to register products",
                    severity="warning",
                ),
            )
        )

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return RegistryResult(
            errors=(
                _error(
                    "products.yaml",
                    now,
                    "Product registry YAML is invalid or unreadable",
                    "Fix products.yaml and retry",
                ),
            )
        )

    if not isinstance(data, dict):
        return RegistryResult(
            errors=(
                _error(
                    "products.yaml",
                    now,
                    "Product registry must be a mapping",
                    "Fix products.yaml and retry",
                ),
            )
        )
    if set(data) - {"apiVersion", "products"}:
        return RegistryResult(
            errors=(
                _error(
                    "products.yaml",
                    now,
                    "Product registry contains an unknown top-level field",
                    "Remove unsupported fields from products.yaml",
                ),
            )
        )
    if data.get("apiVersion") != _API_VERSION:
        return RegistryResult(
            errors=(
                _error(
                    "products.yaml",
                    now,
                    f"Product registry apiVersion must be {_API_VERSION}",
                    "Update products.yaml to the supported apiVersion",
                ),
            )
        )
    raw_products = data.get("products", [])
    if not isinstance(raw_products, list):
        return RegistryResult(
            errors=(
                _error(
                    "products.yaml",
                    now,
                    "Product registry products must be a list",
                    "Fix products.yaml and retry",
                ),
            )
        )

    products: list[ProductSummary] = []
    errors: list[SourceError] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_products):
        source_id = (
            str(raw.get("id"))
            if isinstance(raw, dict) and raw.get("id")
            else f"product-{index + 1}"
        )
        if source_id in seen:
            errors.append(
                _error(
                    source_id,
                    now,
                    f"Duplicate product id: {source_id}",
                    "Assign every product a unique id",
                )
            )
            continue
        try:
            parsed = _parse_product(raw, now, roots)
        except (TypeError, ValueError) as exc:
            errors.append(
                _error(
                    source_id,
                    now,
                    str(exc),
                    f"Fix product {source_id} in products.yaml",
                )
            )
            continue
        seen.add(parsed.product_id)
        products.append(parsed)

    return RegistryResult(
        products=tuple(sorted(products, key=lambda item: item.product_id)),
        errors=tuple(sorted(errors, key=lambda item: item.source.source_id)),
    )
