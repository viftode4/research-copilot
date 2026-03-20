"""Tests for configuration management."""

from __future__ import annotations

import os
from unittest.mock import patch

from research_copilot.config import CopilotConfig, DatabaseConfig, load_config


class TestDatabaseConfig:
    def test_default_values(self):
        db = DatabaseConfig()
        assert db.host == "localhost"
        assert db.port == 5432
        assert db.name == "research_copilot"

    def test_url_construction(self):
        db = DatabaseConfig(host="db.example.com", port=5433, name="mydb", user="myuser", password="secret")
        assert db.url == "postgresql+asyncpg://myuser:secret@db.example.com:5433/mydb"
        assert db.sync_url == "postgresql://myuser:secret@db.example.com:5433/mydb"

    def test_env_prefix(self):
        with patch.dict(os.environ, {"RC_DB_HOST": "custom-host", "RC_DB_PORT": "5555"}):
            db = DatabaseConfig()
            assert db.host == "custom-host"
            assert db.port == 5555


class TestCopilotConfig:
    def test_default_config(self):
        config = CopilotConfig()
        assert config.model == "claude-sonnet-4-6"
        assert config.max_budget_usd == 5.0
        assert config.max_turns == 50

    def test_env_override(self):
        with patch.dict(os.environ, {"RC_MODEL": "claude-sonnet-4-6", "RC_MAX_BUDGET_USD": "10.0"}):
            config = CopilotConfig()
            assert config.model == "claude-sonnet-4-6"
            assert config.max_budget_usd == 10.0

    def test_load_config(self):
        config = load_config()
        assert isinstance(config, CopilotConfig)
        assert config.db is not None
        assert config.wandb is not None
        assert config.slurm is not None
        assert config.literature is not None
