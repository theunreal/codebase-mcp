# codebase-mcp

Remote MCP server that indexes your repos and lets AI agents search them semantically.

Developers connect by adding a URL — no local install needed.

## Quick Start

### 1. Configure repos

Edit `config.yaml`:

```yaml
repos:
  - name: my-backend
    path: /path/to/local/repo
    extensions: [".py", ".ts", ".java"]

  - name: my-frontend
    url: https://github.com/your-org/frontend.git
    branch: main
    extensions: [".ts", ".tsx"]
```

### 2. Run

**With Docker:**
```bash
docker compose up --build
```

**Without Docker:**
```bash
python -m venv .venv && source .venv/bin/activate
pip install mcp chromadb sentence-transformers tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-typescript tree-sitter-java pyyaml gitpython pydantic pydantic-settings structlog uvicorn
python -m src.server
```

First run downloads the embedding model (~80MB) and indexes all repos. Subsequent runs are incremental (only changed files).

### 3. Connect your IDE

**Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "codebase": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

**Cursor** — edit `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "codebase": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

**Claude Code** — edit `.mcp.json`:
```json
{
  "mcpServers": {
    "codebase": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

Replace `localhost:8080` with your server's actual address when hosting remotely.

## Tools

| Tool | What it does |
|------|-------------|
| `semantic_search(query)` | Natural language search: *"how does auth work"* |
| `get_entity(name)` | Exact lookup: *"show me UserService"* |
| `get_file_skeleton(file_path)` | File structure without bodies (saves tokens) |
| `list_repos()` | What's indexed |
| `reindex()` | Trigger re-indexing |

All tools accept an optional `repo_name` parameter to filter by repo.

## Architecture

```
IDE (Claude/Cursor) → Streamable HTTP → MCP Server → ChromaDB
                                            ↑
                                    tree-sitter AST chunking
                                    + sentence-transformers embeddings
                                    + git clone/pull on schedule
```

- **Chunking**: tree-sitter parses code into functions, classes, methods (not character splits)
- **Embeddings**: sentence-transformers (default `all-MiniLM-L6-v2`, or `jina-embeddings-v2-base-code` for better code search)
- **Storage**: ChromaDB with cosine similarity
- **Incremental**: file MD5 hashes tracked, only changed files re-embedded
- **Scheduled**: auto git pull + re-index every N minutes (configurable)
