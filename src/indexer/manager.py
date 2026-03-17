"""
Indexing manager — orchestrates the full pipeline:
  1. Walk repo files
  2. Check if file changed since last index (via hash)
  3. Chunk with tree-sitter
  4. Store in vector DB
"""

import hashlib
import json
import os
from pathlib import Path

import structlog

from src.config import AppConfig, RepoConfig
from src.indexer.chunker import CodeChunk, chunk_file
from src.storage.vectordb import VectorStore
from src.sync.git_sync import sync_all

logger = structlog.get_logger()

# File where we store hashes of indexed files to support incremental indexing
HASH_CACHE_PATH = "/data/file_hashes.json"


def _load_hash_cache() -> dict[str, str]:
    if os.path.exists(HASH_CACHE_PATH):
        with open(HASH_CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_hash_cache(cache: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(HASH_CACHE_PATH), exist_ok=True)
    with open(HASH_CACHE_PATH, "w") as f:
        json.dump(cache, f)


def _file_hash(file_path: str) -> str:
    with open(file_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _walk_repo(
    repo_path: str,
    extensions: list[str],
    skip_dirs: list[str],
    max_file_size: int,
) -> list[str]:
    """Walk a repo directory and return all indexable file paths."""
    files = []
    for root, dirs, filenames in os.walk(repo_path):
        # Skip excluded directories (modifying dirs in-place to prevent os.walk from descending)
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        for filename in filenames:
            file_path = os.path.join(root, filename)
            ext = Path(filename).suffix

            if ext not in extensions:
                continue
            if os.path.getsize(file_path) > max_file_size:
                continue

            files.append(file_path)

    return files


def index_repo(
    repo_config: RepoConfig,
    repo_path: str,
    store: VectorStore,
    config: AppConfig,
    hash_cache: dict[str, str],
) -> int:
    """Index a single repo. Returns number of chunks indexed."""
    logger.info("indexing_repo", name=repo_config.name, path=repo_path)

    files = _walk_repo(
        repo_path,
        repo_config.extensions,
        config.indexing.skip_dirs,
        config.indexing.max_file_size,
    )
    logger.info("found_files", repo=repo_config.name, count=len(files))

    # Build set of current file paths (relative) to detect deleted files
    current_files: set[str] = set()
    for file_path in files:
        relative_path = os.path.relpath(file_path, repo_path)
        current_files.add(relative_path)

    # Remove stale entries: files that were indexed before but no longer exist
    prefix = f"{repo_config.name}::"
    stale_keys = [
        key for key in hash_cache
        if key.startswith(prefix) and key[len(prefix):] not in current_files
    ]
    if stale_keys:
        # Delete stale chunks from vector DB
        stale_relative_paths = [key[len(prefix):] for key in stale_keys]
        store.delete_by_file_paths(repo_config.name, stale_relative_paths)
        for key in stale_keys:
            del hash_cache[key]
        logger.info("removed_stale_files", repo=repo_config.name, count=len(stale_keys))

    all_chunks: list[CodeChunk] = []
    changed_files: list[str] = []

    for file_path in files:
        current_hash = _file_hash(file_path)
        relative_path = os.path.relpath(file_path, repo_path)
        cache_key = f"{repo_config.name}::{relative_path}"

        # Skip unchanged files
        if hash_cache.get(cache_key) == current_hash:
            continue

        changed_files.append(relative_path)
        ext = Path(file_path).suffix

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()

            file_chunks = chunk_file(source, relative_path, repo_config.name, ext)
            all_chunks.extend(file_chunks.chunks)

            hash_cache[cache_key] = current_hash

        except Exception as e:
            logger.warning("chunk_failed", file=file_path, error=str(e))

    if not all_chunks:
        logger.info("no_changes", repo=repo_config.name, total_files=len(files))
        return 0

    # Delete old chunks for changed files before upserting new ones.
    # This handles renamed/moved functions that would otherwise leave orphans.
    store.delete_by_file_paths(repo_config.name, changed_files)

    logger.info(
        "chunking_complete",
        repo=repo_config.name,
        changed_files=len(changed_files),
        total_chunks=len(all_chunks),
    )

    count = store.upsert_chunks(all_chunks)
    logger.info("indexing_complete", repo=repo_config.name, chunks_stored=count)
    return count


def run_full_index(config: AppConfig, store: VectorStore) -> dict[str, int]:
    """
    Full indexing pipeline:
    1. Git sync all repos
    2. Index each repo incrementally
    3. Save hash cache
    """
    # Sync repos (clone/pull)
    repo_paths = sync_all(config.repos, config.repos_dir)

    hash_cache = _load_hash_cache()
    results: dict[str, int] = {}

    for repo_config in config.repos:
        if repo_config.name not in repo_paths:
            logger.warning("repo_not_synced", name=repo_config.name)
            continue

        repo_path = repo_paths[repo_config.name]
        count = index_repo(repo_config, repo_path, store, config, hash_cache)
        results[repo_config.name] = count

    _save_hash_cache(hash_cache)
    return results
