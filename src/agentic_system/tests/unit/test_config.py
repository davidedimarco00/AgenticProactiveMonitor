from pathlib import Path

import pytest

from agentic_system.settings import RuntimeConfig, _env_bool, _load_yaml


def test_env_bool_accepts_enabled_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_FLAG", "yes")
    assert _env_bool("TEST_FLAG", False) is True


def test_env_bool_uses_default_when_variable_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_FLAG", raising=False)
    assert _env_bool("TEST_FLAG", True) is True


def test_load_yaml_requires_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text("- invalid\n- root\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="root must be a YAML mapping"):
        _load_yaml(path)


def test_runtime_config_defaults_to_ten_react_diagnostic_steps() -> None:
    assert RuntimeConfig.__dataclass_fields__["react_max_steps"].default == 10
