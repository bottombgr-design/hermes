from __future__ import annotations

from pathlib import Path

from control_centre.registry import load_product_registry


def write_registry(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_missing_registry_returns_empty_state_with_visible_warning(tmp_path):
    result = load_product_registry(tmp_path / "missing.yaml", observed_at=10.0)

    assert result.products == ()
    assert len(result.errors) == 1
    assert result.errors[0].severity == "warning"
    assert "not configured" in result.errors[0].message.lower()
    assert result.errors[0].recovery


def test_valid_and_incomplete_products_preserve_stable_ids(tmp_path):
    repo = tmp_path / "repos" / "hermes"
    repo.mkdir(parents=True)
    registry = write_registry(
        tmp_path / "products.yaml",
        f"""
apiVersion: control-centre/v1
products:
  - id: hermes-agent
    name: Hermes Agent
    repositories:
      - id: hermes-agent
        path: {repo}
        remote: origin
    kanban:
      board: default
      tenant: hermes-agent
    knowledge_roots:
      - {tmp_path / 'knowledge'}
    environments:
      - name: development
        production: false
  - id: helm-desktop
    name: Helm Desktop
    configuration_state: incomplete
""",
    )

    result = load_product_registry(
        registry,
        observed_at=10.0,
        allowed_roots=(tmp_path,),
    )

    assert result.errors == ()
    assert [product.product_id for product in result.products] == [
        "helm-desktop",
        "hermes-agent",
    ]
    hermes = next(p for p in result.products if p.product_id == "hermes-agent")
    assert hermes.repositories[0]["id"] == "hermes-agent"
    assert hermes.repositories[0]["path"] == str(repo)
    assert hermes.kanban_href == "/kanban?board=default&tenant=hermes-agent"
    incomplete = next(p for p in result.products if p.product_id == "helm-desktop")
    assert incomplete.configuration_state == "incomplete"
    assert incomplete.repositories == ()
    assert incomplete.recovery == "Complete this product in products.yaml"


def test_one_invalid_product_does_not_hide_valid_product(tmp_path):
    registry = write_registry(
        tmp_path / "products.yaml",
        """
apiVersion: control-centre/v1
products:
  - id: valid-product
    name: Valid
    configuration_state: incomplete
  - id: invalid-product
    name: Invalid
    surprise: true
""",
    )

    result = load_product_registry(registry, observed_at=10.0)

    assert [product.product_id for product in result.products] == ["valid-product"]
    assert len(result.errors) == 1
    assert result.errors[0].source.source_id == "invalid-product"
    assert "unknown field" in result.errors[0].message.lower()


def test_duplicate_product_id_rejects_duplicate_only(tmp_path):
    registry = write_registry(
        tmp_path / "products.yaml",
        """
apiVersion: control-centre/v1
products:
  - id: repeated
    name: First
    configuration_state: incomplete
  - id: repeated
    name: Second
    configuration_state: incomplete
""",
    )

    result = load_product_registry(registry, observed_at=10.0)

    assert [product.name for product in result.products] == ["First"]
    assert len(result.errors) == 1
    assert "duplicate" in result.errors[0].message.lower()


def test_relative_and_outside_paths_are_rejected_per_product(tmp_path):
    outside = tmp_path.parent / "outside-control-centre"
    registry = write_registry(
        tmp_path / "products.yaml",
        f"""
apiVersion: control-centre/v1
products:
  - id: relative
    name: Relative
    repositories:
      - id: repo
        path: relative/repo
  - id: outside
    name: Outside
    knowledge_roots:
      - {outside}
  - id: valid
    name: Valid
    configuration_state: incomplete
""",
    )

    result = load_product_registry(
        registry,
        observed_at=10.0,
        allowed_roots=(tmp_path,),
    )

    assert [product.product_id for product in result.products] == ["valid"]
    assert {error.source.source_id for error in result.errors} == {
        "outside",
        "relative",
    }
    assert all("path" in error.message.lower() for error in result.errors)


def test_secret_shaped_keys_are_rejected_recursively_without_echoing_value(tmp_path):
    secret_value = "do-not-echo-this-value"
    registry = write_registry(
        tmp_path / "products.yaml",
        f"""
apiVersion: control-centre/v1
products:
  - id: unsafe
    name: Unsafe
    metadata:
      nested:
        api_token: {secret_value}
  - id: safe
    name: Safe
    configuration_state: incomplete
""",
    )

    result = load_product_registry(registry, observed_at=10.0)

    assert [product.product_id for product in result.products] == ["safe"]
    message = result.errors[0].message
    assert "secret-shaped" in message.lower()
    assert secret_value not in message


def test_yaml_parse_error_returns_recoverable_source_error(tmp_path):
    registry = write_registry(tmp_path / "products.yaml", "products: [unterminated")

    result = load_product_registry(registry, observed_at=10.0)

    assert result.products == ()
    assert len(result.errors) == 1
    assert result.errors[0].source.source_id == "products.yaml"
    assert "yaml" in result.errors[0].message.lower()
    assert "fix" in result.errors[0].recovery.lower()
