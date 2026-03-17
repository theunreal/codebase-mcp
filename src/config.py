"""
Configuration loader — reads config.yaml and env vars.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RepoConfig:
    name: str
    path: str | None = None
    url: str | None = None
    branch: str = "main"
    extensions: list[str] = field(default_factory=lambda: [".py", ".ts", ".js", ".java"])


@dataclass
class IndexingConfig:
    interval_minutes: int = 30
    max_file_size: int = 100_000
    skip_dirs: list[str] = field(
        default_factory=lambda: [
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            "dist", "build", ".next", "target",
        ]
    )


@dataclass
class EmbeddingConfig:
    model: str = "jinaai/jina-embeddings-v2-base-code"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class AppConfig:
    repos: list[RepoConfig]
    repos_dir: str = "/data/repos"
    indexing: IndexingConfig = field(default_factory=IndexingConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


def load_config(config_path: str = "config.yaml") -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    repos = [RepoConfig(**r) for r in raw.get("repos", [])]

    indexing_raw = raw.get("indexing", {})
    embedding_raw = raw.get("embedding", {})
    server_raw = raw.get("server", {})

    return AppConfig(
        repos=repos,
        repos_dir=raw.get("repos_dir", "/data/repos"),
        indexing=IndexingConfig(**indexing_raw),
        embedding=EmbeddingConfig(**embedding_raw),
        server=ServerConfig(**server_raw),
    )
