from pathlib import Path
from importlib.resources import files
import tomllib

import pytest

from wendao_bot.config import ConfigError, load_config


def test_load_config_has_safe_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("window:\n  title: 问道\n", encoding="utf-8")

    config = load_config(path)

    assert config.window_title == "问道"
    assert config.min_confidence >= 0.85
    assert config.max_unchanged_actions == 3
    assert config.imessage_recipient == "wendao-owner@example.com"
    assert "payment" in config.blocked_states


@pytest.mark.parametrize(
    "document",
    [
        "[]",
        "false",
        "text",
        "window: []",
        "safety: []",
        "timeouts: text",
        "notification: false",
    ],
)
def test_load_config_rejects_invalid_mapping_containers(
    tmp_path: Path, document: str
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize(
    "document",
    [
        "window:\n  title: false",
        "window:\n  title: 12",
        "window:\n  width: true",
        "window:\n  width: 1.5",
        "window:\n  width: text",
        "window:\n  height: 0",
        "window:\n  height: false",
        "safety:\n  min_confidence: false",
        "safety:\n  min_confidence: text",
        "safety:\n  min_confidence: 0.49",
        "safety:\n  min_confidence: 1.01",
        "safety:\n  max_unchanged_actions: 0",
        "safety:\n  max_unchanged_actions: false",
        "safety:\n  max_unchanged_actions: 1.5",
        "timeouts:\n  action: 0",
        "timeouts:\n  action: false",
        "timeouts:\n  action: .nan",
        "timeouts:\n  battle: -1",
        "timeouts:\n  battle: .inf",
        "timeouts:\n  battle: text",
        "notification:\n  recipient: 12",
        "blocked_states: payment",
        "blocked_states: {}",
        "blocked_states: [payment, 12]",
        "daily_whitelist: 师门",
        "daily_whitelist: {}",
        "daily_whitelist: [师门, false]",
    ],
)
def test_load_config_rejects_unsafe_values(tmp_path: Path, document: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize(
    "field",
    [
        "window.title",
        "window.width",
        "window.height",
        "safety.min_confidence",
        "safety.max_unchanged_actions",
        "timeouts.action",
        "timeouts.battle",
        "notification.recipient",
    ],
)
def test_load_config_rejects_mapping_valued_scalar_leaves(
    tmp_path: Path, field: str
) -> None:
    section, key = field.split(".")
    path = tmp_path / "config.yaml"
    path.write_text(f"{section}:\n  {key}:\n    bad: value\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_normalizes_yaml_errors(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("window: [", encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_null_document_loads_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("null", encoding="utf-8")

    assert load_config(path).width == 886


def test_default_config_is_packaged_and_matches_cli_copy() -> None:
    root = Path(__file__).parents[1]
    package_default = files("wendao_bot").joinpath("default.yaml").read_text("utf-8")
    cli_default = (root / "config" / "default.yaml").read_text(encoding="utf-8")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert package_default == cli_default
    assert "default.yaml" in pyproject["tool"]["setuptools"]["package-data"]["wendao_bot"]


@pytest.mark.parametrize(
    "name",
    ["押 镖", "daily.quest", "a" * 33, "alice@example.com"],
)
def test_daily_whitelist_rejects_unsafe_target_names(
    tmp_path: Path, name: str
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "daily_whitelist:\n" + f"  - {name!r}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="daily_whitelist"):
        load_config(path)


def test_daily_whitelist_rejects_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("daily_whitelist: [除暴, 除暴]\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="daily_whitelist"):
        load_config(path)


def test_daily_whitelist_accepts_safe_chinese_and_ascii_names(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("daily_whitelist: [除暴, shimen]\n", encoding="utf-8")

    assert load_config(path).daily_whitelist == ("除暴", "shimen")
