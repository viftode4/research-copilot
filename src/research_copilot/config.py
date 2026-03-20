"""Configuration management for Research Copilot."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class DatabaseConfig(BaseSettings):
    """PostgreSQL connection settings."""

    model_config = {"env_prefix": "RC_DB_"}

    host: str = "localhost"
    port: int = 5432
    name: str = "research_copilot"
    user: str = "research_copilot"
    password: str = ""

    @property
    def url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    @property
    def sync_url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class WandbConfig(BaseSettings):
    """Weights & Biases settings."""

    model_config = {"env_prefix": "RC_WANDB_"}

    api_key: str = ""
    entity: str = ""
    project: str = ""


class SlurmConfig(BaseSettings):
    """Slurm cluster settings."""

    model_config = {"env_prefix": "RC_SLURM_"}

    host: str = ""
    user: str = ""
    ssh_key_path: str = ""
    partition: str = "gpu"
    default_gpus: int = 1
    default_time: str = "04:00:00"


class LiteratureConfig(BaseSettings):
    """Literature search API settings."""

    model_config = {"env_prefix": "RC_LIT_"}

    semantic_scholar_api_key: str = ""
    arxiv_max_results: int = 20
    semantic_scholar_max_results: int = 20


class CopilotConfig(BaseSettings):
    """Top-level Research Copilot configuration."""

    model_config = {"env_prefix": "RC_"}

    model: str = "claude-opus-4-6"
    max_budget_usd: float = 5.0
    max_turns: int = 50
    working_dir: str = "."

    db: DatabaseConfig = Field(default_factory=DatabaseConfig)
    wandb: WandbConfig = Field(default_factory=WandbConfig)
    slurm: SlurmConfig = Field(default_factory=SlurmConfig)
    literature: LiteratureConfig = Field(default_factory=LiteratureConfig)


def load_config() -> CopilotConfig:
    """Load configuration from environment variables."""
    return CopilotConfig()
