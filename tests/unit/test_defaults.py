"""Unit tests for shared defaults helpers (env placeholder / yaml cache / max rounds)."""

import os
import time

from src.shared.defaults import (
    load_yaml_cached,
    max_tool_rounds,
    resolve_env_placeholders,
)


class TestResolveEnvPlaceholders:
    def test_whole_value(self, monkeypatch):
        monkeypatch.setenv("T1", "hello")
        assert resolve_env_placeholders("${T1}") == "hello"

    def test_mixed_value(self, monkeypatch):
        monkeypatch.setenv("T1", "hello")
        assert resolve_env_placeholders("prefix-${T1}-suffix") == "prefix-hello-suffix"

    def test_missing_env_becomes_empty(self):
        assert resolve_env_placeholders("${MISSING_XYZ}") == ""

    def test_non_string_passthrough(self):
        assert resolve_env_placeholders(123) == 123
        assert resolve_env_placeholders(None) is None


class TestMaxToolRounds:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("AGENT_TOOL_MAX_ROUNDS", raising=False)
        assert max_tool_rounds() == 5

    def test_override(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_MAX_ROUNDS", "8")
        assert max_tool_rounds() == 8

    def test_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_MAX_ROUNDS", "not-a-number")
        assert max_tool_rounds() == 5


class TestLoadYamlCached:
    def test_roundtrip(self, tmp_path):
        f = tmp_path / "c.yaml"
        f.write_text("a: 1\nb: hello\n", encoding="utf-8")
        assert load_yaml_cached(str(f)) == {"a": 1, "b": "hello"}

    def test_mtime_invalidate(self, tmp_path):
        f = tmp_path / "c.yaml"
        f.write_text("a: 1\n", encoding="utf-8")
        assert load_yaml_cached(str(f)) == {"a": 1}
        # 修改内容并强制 mtime 变化，缓存应失效
        f.write_text("a: 2\n", encoding="utf-8")
        future = time.time() + 10
        os.utime(f, (future, future))
        assert load_yaml_cached(str(f)) == {"a": 2}

    def test_empty_file_returns_none(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        assert load_yaml_cached(str(f)) is None
