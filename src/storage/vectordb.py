"""
Vector storage layer using ChromaDB.

Handles embedding, storing, and querying code chunks.
Each repo gets tagged in metadata so you can search across all repos or filter by one.
"""

import hashlib
from dataclasses import dataclass

import chromadb
import structlog
from sentence_transformers import SentenceTransformer

from src.indexer.chunker import CodeChunk

logger = structlog.get_logger()


@dataclass
class SearchResult:
    text: str
    file_path: str
    repo_name: str
    symbol_name: str
    symbol_type: str
    parent_name: str | None
    start_line: int
    end_line: int
    language: str
    score: float


class VectorStore:
    def __init__(self, persist_dir: str = "/data/chroma_db", model_name: str = "jinaai/jina-embeddings-v2-base-code"):
        logger.info("initializing_vector_store", persist_dir=persist_dir, model=model_name)
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._client.get_or_create_collection(
            name="codebase",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("loading_embedding_model", model=model_name)
        self._model = SentenceTransformer(model_name, trust_remote_code=True)
        logger.info("embedding_model_loaded")

    def _chunk_id(self, chunk: CodeChunk) -> str:
        """Stable ID for a chunk — same chunk always gets the same ID."""
        key = f"{chunk.repo_name}::{chunk.file_path}::{chunk.symbol_name}::{chunk.start_line}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _build_search_text(self, chunk: CodeChunk) -> str:
        """
        Build the text we embed. Includes the code plus metadata context
        so semantic search can match on both code content and structure.
        """
        parts = [
            f"# {chunk.symbol_type}: {chunk.symbol_name}",
            f"# File: {chunk.file_path}",
        ]
        if chunk.parent_name:
            parts.append(f"# Class: {chunk.parent_name}")
        if chunk.imports:
            parts.append(f"# Imports:\n{chunk.imports}")
        parts.append(chunk.text)
        return "\n".join(parts)

    def upsert_chunks(self, chunks: list[CodeChunk]) -> int:
        """
        Upsert chunks into the vector store. Returns count of chunks stored.
        Uses batch processing for efficiency.
        """
        if not chunks:
            return 0

        batch_size = 100
        total = 0

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]

            ids = [self._chunk_id(c) for c in batch]
            texts = [self._build_search_text(c) for c in batch]
            embeddings = self._model.encode(texts).tolist()
            metadatas = [
                {
                    "file_path": c.file_path,
                    "repo_name": c.repo_name,
                    "symbol_name": c.symbol_name,
                    "symbol_type": c.symbol_type,
                    "parent_name": c.parent_name or "",
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "language": c.language,
                }
                for c in batch
            ]
            documents = [c.text for c in batch]

            self._collection.upsert(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents,
            )
            total += len(batch)
            logger.info("upserted_batch", count=len(batch), total=total)

        return total

    def delete_repo(self, repo_name: str) -> None:
        """Delete all chunks for a repo (before re-indexing)."""
        self._collection.delete(where={"repo_name": repo_name})
        logger.info("deleted_repo_chunks", repo_name=repo_name)

    def search(self, query: str, top_k: int = 10, repo_name: str | None = None) -> list[SearchResult]:
        """
        Semantic search across indexed code.
        Optionally filter by repo name.
        """
        query_embedding = self._model.encode([query]).tolist()

        where_filter = {"repo_name": repo_name} if repo_name else None

        results = self._collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        if not results["documents"] or not results["documents"][0]:
            return []

        search_results = []
        for doc, meta, distance in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            # ChromaDB returns distances — convert to similarity score (cosine)
            score = 1.0 - distance
            search_results.append(
                SearchResult(
                    text=doc,
                    file_path=meta["file_path"],
                    repo_name=meta["repo_name"],
                    symbol_name=meta["symbol_name"],
                    symbol_type=meta["symbol_type"],
                    parent_name=meta["parent_name"] or None,
                    start_line=meta["start_line"],
                    end_line=meta["end_line"],
                    language=meta["language"],
                    score=score,
                )
            )

        return search_results

    def get_entity(self, name: str, repo_name: str | None = None) -> list[SearchResult]:
        """
        Exact lookup by symbol name. Returns all chunks matching the name.
        """
        where_filter: dict = {"symbol_name": name}
        if repo_name:
            where_filter = {"$and": [{"symbol_name": name}, {"repo_name": repo_name}]}

        results = self._collection.get(
            where=where_filter,
            include=["documents", "metadatas"],
        )

        if not results["documents"]:
            return []

        return [
            SearchResult(
                text=doc,
                file_path=meta["file_path"],
                repo_name=meta["repo_name"],
                symbol_name=meta["symbol_name"],
                symbol_type=meta["symbol_type"],
                parent_name=meta["parent_name"] or None,
                start_line=meta["start_line"],
                end_line=meta["end_line"],
                language=meta["language"],
                score=1.0,
            )
            for doc, meta in zip(results["documents"], results["metadatas"])
        ]

    def list_repos(self) -> dict[str, int]:
        """Return a map of repo_name -> chunk count."""
        # ChromaDB doesn't have a great GROUP BY, so we get all metadata
        all_meta = self._collection.get(include=["metadatas"])
        repo_counts: dict[str, int] = {}
        for meta in all_meta["metadatas"]:
            repo = meta["repo_name"]
            repo_counts[repo] = repo_counts.get(repo, 0) + 1
        return repo_counts

    def total_chunks(self) -> int:
        return self._collection.count()
