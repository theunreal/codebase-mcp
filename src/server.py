"""
MCP Server — exposes codebase search tools over Streamable HTTP.

Developers configure their IDE to connect to this server's URL.
The server indexes repos on startup and on a schedule, then serves
semantic search queries from any connected client.
"""

import asyncio
import threading

import structlog
from mcp.server.fastmcp import FastMCP

from src.config import AppConfig, load_config
from src.indexer.manager import run_full_index
from src.storage.vectordb import VectorStore

logger = structlog.get_logger()

# Global state — initialized at startup
config: AppConfig | None = None
store: VectorStore | None = None

mcp = FastMCP(
    "Codebase Search",
    instructions=(
        "This server provides semantic search across your organization's codebase. "
        "Use semantic_search for natural language queries like 'how does auth work'. "
        "Use get_entity for exact lookups like 'show me the UserService class'. "
        "Use get_file_skeleton to understand a file's structure without reading all the code. "
        "Use list_repos to see what repos are indexed."
    ),
)


@mcp.tool()
def semantic_search(query: str, repo_name: str | None = None, top_k: int = 10) -> str:
    """
    Search the codebase using natural language.

    Use this when you need to understand how something works, find implementations,
    or locate code related to a concept.

    Examples:
      - "how does authentication work"
      - "credit balance calculation"
      - "database connection setup"

    Args:
        query: Natural language description of what you're looking for.
        repo_name: Optional — filter results to a specific repo.
        top_k: Number of results to return (default 10).
    """
    if store is None:
        return "Error: Server not initialized. Try again in a moment."

    results = store.search(query, top_k=top_k, repo_name=repo_name)

    if not results:
        return "No results found."

    formatted = []
    for i, r in enumerate(results, 1):
        location = f"{r.repo_name}/{r.file_path}:{r.start_line}-{r.end_line}"
        parent = f" (in {r.parent_name})" if r.parent_name else ""
        header = f"### {i}. {r.symbol_type} `{r.symbol_name}`{parent} — {location} (score: {r.score:.2f})"
        formatted.append(f"{header}\n```{r.language.lstrip('.')}\n{r.text}\n```")

    return "\n\n".join(formatted)


@mcp.tool()
def get_entity(name: str, repo_name: str | None = None) -> str:
    """
    Look up a specific function, class, method, or interface by exact name.

    Use this when you know the name of what you're looking for.

    Examples:
      - get_entity("UserService")
      - get_entity("authenticate", repo_name="auth-service")
      - get_entity("CreditAccount")

    Args:
        name: Exact name of the function, class, or method.
        repo_name: Optional — filter to a specific repo.
    """
    if store is None:
        return "Error: Server not initialized."

    results = store.get_entity(name, repo_name=repo_name)

    if not results:
        # Fall back to semantic search if exact match fails
        results = store.search(name, top_k=5, repo_name=repo_name)
        if not results:
            return f"No entity found matching '{name}'."
        prefix = f"No exact match for '{name}'. Closest semantic matches:\n\n"
    else:
        prefix = ""

    formatted = []
    for r in results:
        location = f"{r.repo_name}/{r.file_path}:{r.start_line}-{r.end_line}"
        parent = f" (in {r.parent_name})" if r.parent_name else ""
        header = f"### {r.symbol_type} `{r.symbol_name}`{parent} — {location}"
        formatted.append(f"{header}\n```{r.language.lstrip('.')}\n{r.text}\n```")

    return prefix + "\n\n".join(formatted)


@mcp.tool()
def get_file_skeleton(file_path: str, repo_name: str | None = None) -> str:
    """
    Get the structure of a file — just function/class signatures, no implementation bodies.

    Use this to understand what a file contains without reading all the code.
    Saves tokens compared to reading the full file.

    Args:
        file_path: Path to the file (relative to repo root, e.g. "src/auth/service.py").
        repo_name: Optional — which repo to look in.
    """
    if store is None:
        return "Error: Server not initialized."

    # Query for all chunks from this file
    where_filter: dict = {"file_path": file_path}
    if repo_name:
        where_filter = {"$and": [{"file_path": file_path}, {"repo_name": repo_name}]}

    results = store._collection.get(
        where=where_filter,
        include=["metadatas"],
    )

    if not results["metadatas"]:
        return f"No file found matching '{file_path}'."

    # Sort by line number and build skeleton
    entries = sorted(results["metadatas"], key=lambda m: m["start_line"])

    lines = [f"# {file_path} ({entries[0].get('repo_name', 'unknown')})\n"]
    for meta in entries:
        parent = f"  (in {meta['parent_name']})" if meta.get("parent_name") else ""
        lines.append(
            f"  L{meta['start_line']}-{meta['end_line']}  "
            f"{meta['symbol_type']} {meta['symbol_name']}{parent}"
        )

    return "\n".join(lines)


@mcp.tool()
def list_repos() -> str:
    """
    List all indexed repositories and their stats.

    Use this to see what codebases are available for search.
    """
    if store is None:
        return "Error: Server not initialized."

    repos = store.list_repos()
    total = store.total_chunks()

    if not repos:
        return "No repos indexed yet."

    lines = ["# Indexed Repositories\n"]
    for name, count in sorted(repos.items()):
        lines.append(f"  - **{name}**: {count} chunks")
    lines.append(f"\n**Total**: {total} chunks across {len(repos)} repos")

    return "\n".join(lines)


@mcp.tool()
def reindex(repo_name: str | None = None) -> str:
    """
    Trigger re-indexing of repos. Only re-indexes files that have changed.

    Args:
        repo_name: Optional — re-index only this specific repo. If omitted, re-indexes all.
    """
    if config is None or store is None:
        return "Error: Server not initialized."

    if repo_name:
        repos_to_index = [r for r in config.repos if r.name == repo_name]
        if not repos_to_index:
            return f"Unknown repo: {repo_name}"
    else:
        repos_to_index = config.repos

    from src.config import AppConfig

    subset_config = AppConfig(
        repos=repos_to_index,
        repos_dir=config.repos_dir,
        indexing=config.indexing,
        embedding=config.embedding,
        server=config.server,
    )

    results = run_full_index(subset_config, store)

    lines = ["# Re-indexing Results\n"]
    for name, count in results.items():
        lines.append(f"  - **{name}**: {count} new/updated chunks")

    return "\n".join(lines)


def _schedule_reindex(interval_minutes: int):
    """Background thread that re-indexes on a schedule."""
    if interval_minutes <= 0:
        return

    def loop():
        while True:
            import time
            time.sleep(interval_minutes * 60)
            logger.info("scheduled_reindex_starting")
            try:
                if config and store:
                    run_full_index(config, store)
                    logger.info("scheduled_reindex_complete")
            except Exception as e:
                logger.error("scheduled_reindex_failed", error=str(e))

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    logger.info("reindex_scheduler_started", interval_minutes=interval_minutes)


def main():
    global config, store

    import sys

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)

    # Initialize vector store + embedding model
    store = VectorStore(
        persist_dir="/data/chroma_db",
        model_name=config.embedding.model,
    )

    # Run initial indexing
    if config.repos:
        logger.info("running_initial_index", repo_count=len(config.repos))
        results = run_full_index(config, store)
        for name, count in results.items():
            logger.info("initial_index_result", repo=name, chunks=count)

    # Schedule periodic re-indexing
    _schedule_reindex(config.indexing.interval_minutes)

    # Start MCP server over Streamable HTTP
    logger.info(
        "starting_mcp_server",
        host=config.server.host,
        port=config.server.port,
        transport="streamable-http",
    )
    mcp.run(
        transport="streamable-http",
        host=config.server.host,
        port=config.server.port,
    )


if __name__ == "__main__":
    main()
