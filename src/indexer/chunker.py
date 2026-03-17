"""
AST-based code chunker using tree-sitter.

Parses source files into semantic chunks: functions, classes, methods.
Each chunk preserves full context (the entire function/class body) instead
of splitting at arbitrary character boundaries.
"""

from dataclasses import dataclass, field

import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

# Language setup — each tree-sitter-<lang> package exposes a language() function
LANGUAGES: dict[str, Language] = {
    ".py": Language(tspython.language()),
    ".js": Language(tsjavascript.language()),
    ".ts": Language(tstypescript.language_typescript()),
    ".tsx": Language(tstypescript.language_tsx()),
    ".java": Language(tsjava.language()),
}

# Node types that represent meaningful code units per language
CHUNK_NODE_TYPES: dict[str, set[str]] = {
    ".py": {"function_definition", "class_definition"},
    ".js": {"function_declaration", "class_declaration", "arrow_function",
            "method_definition", "export_statement"},
    ".ts": {"function_declaration", "class_declaration", "arrow_function",
            "method_definition", "export_statement", "interface_declaration",
            "type_alias_declaration"},
    ".tsx": {"function_declaration", "class_declaration", "arrow_function",
             "method_definition", "export_statement", "interface_declaration",
             "type_alias_declaration"},
    ".java": {"method_declaration", "class_declaration", "interface_declaration",
              "constructor_declaration", "enum_declaration"},
}


@dataclass
class CodeChunk:
    """A semantic unit of code extracted from a source file."""
    text: str
    file_path: str
    repo_name: str
    language: str
    symbol_name: str
    symbol_type: str  # "function", "class", "method", "interface", etc.
    parent_name: str | None  # class name if this is a method
    start_line: int
    end_line: int
    imports: str  # import statements from the file, for context


@dataclass
class FileChunks:
    """All chunks extracted from a single file."""
    file_path: str
    chunks: list[CodeChunk] = field(default_factory=list)
    file_header: str = ""  # imports + top-level assignments


def _extract_name(node: Node) -> str:
    """Extract the name identifier from an AST node."""
    # Look for a 'name' field first (works for most languages)
    name_node = node.child_by_field_name("name")
    if name_node:
        return name_node.text.decode("utf-8")

    # For export statements, look for the declaration inside
    if node.type == "export_statement":
        for child in node.children:
            if child.type in ("function_declaration", "class_declaration",
                              "interface_declaration", "type_alias_declaration"):
                return _extract_name(child)
            # export const foo = ...
            if child.type == "lexical_declaration":
                for decl_child in child.children:
                    if decl_child.type == "variable_declarator":
                        name = decl_child.child_by_field_name("name")
                        if name:
                            return name.text.decode("utf-8")

    # For arrow functions assigned to variables: const foo = () => {}
    if node.type == "arrow_function" and node.parent:
        if node.parent.type == "variable_declarator":
            name_node = node.parent.child_by_field_name("name")
            if name_node:
                return name_node.text.decode("utf-8")

    return "<anonymous>"


def _classify_symbol(node_type: str) -> str:
    """Map AST node type to a human-readable symbol type."""
    mapping = {
        "function_definition": "function",
        "function_declaration": "function",
        "arrow_function": "function",
        "class_definition": "class",
        "class_declaration": "class",
        "method_definition": "method",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "export_statement": "export",
    }
    return mapping.get(node_type, "unknown")


def _find_parent_class(node: Node) -> str | None:
    """Walk up the AST to find if this node is inside a class."""
    current = node.parent
    while current:
        if current.type in ("class_definition", "class_declaration", "class_body"):
            if current.type == "class_body" and current.parent:
                current = current.parent
            name_node = current.child_by_field_name("name")
            if name_node:
                return name_node.text.decode("utf-8")
        current = current.parent
    return None


def _extract_imports(source: str, ext: str) -> str:
    """Extract import statements from the top of a file."""
    lines = source.split("\n")
    import_lines = []
    for line in lines:
        stripped = line.strip()
        if ext == ".py":
            if stripped.startswith("import ") or stripped.startswith("from "):
                import_lines.append(stripped)
        elif ext in (".js", ".ts", ".tsx"):
            if stripped.startswith("import "):
                import_lines.append(stripped)
                # Handle multi-line imports
                if "{" in stripped and "}" not in stripped:
                    # Continue until closing brace
                    continue
            if stripped.startswith("const ") and "require(" in stripped:
                import_lines.append(stripped)
        elif ext == ".java":
            if stripped.startswith("import ") or stripped.startswith("package "):
                import_lines.append(stripped)
    return "\n".join(import_lines)


def chunk_file(
    source: str,
    file_path: str,
    repo_name: str,
    ext: str,
) -> FileChunks:
    """
    Parse a source file and extract semantic code chunks.

    Returns all functions, classes, methods, interfaces, etc. as individual chunks,
    each with metadata for search and retrieval.
    """
    if ext not in LANGUAGES:
        # Unsupported language — return the whole file as one chunk
        return FileChunks(
            file_path=file_path,
            chunks=[
                CodeChunk(
                    text=source,
                    file_path=file_path,
                    repo_name=repo_name,
                    language=ext,
                    symbol_name=file_path.split("/")[-1],
                    symbol_type="file",
                    parent_name=None,
                    start_line=1,
                    end_line=source.count("\n") + 1,
                    imports="",
                )
            ],
        )

    language = LANGUAGES[ext]
    parser = Parser(language)
    tree = parser.parse(bytes(source, "utf-8"))

    imports = _extract_imports(source, ext)
    node_types = CHUNK_NODE_TYPES.get(ext, set())
    chunks: list[CodeChunk] = []
    visited_ranges: set[tuple[int, int]] = set()

    def walk(node: Node, depth: int = 0):
        # Skip nodes we've already captured as part of a parent
        node_range = (node.start_point.row, node.end_point.row)

        if node.type in node_types and node_range not in visited_ranges:
            # For methods inside classes, we want the method, not the class
            # But we still want the class itself as a "skeleton" chunk
            text = node.text.decode("utf-8") if node.text else ""

            # Skip very small nodes (likely noise)
            if len(text.strip()) < 10:
                for child in node.children:
                    walk(child, depth + 1)
                return

            symbol_name = _extract_name(node)
            actual_type = node.type

            # For export statements, classify by the inner declaration
            if node.type == "export_statement":
                for child in node.children:
                    if child.type in node_types and child.type != "export_statement":
                        actual_type = child.type
                        break

            parent_name = _find_parent_class(node)

            chunks.append(
                CodeChunk(
                    text=text,
                    file_path=file_path,
                    repo_name=repo_name,
                    language=ext,
                    symbol_name=symbol_name,
                    symbol_type=_classify_symbol(actual_type),
                    parent_name=parent_name,
                    start_line=node.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    imports=imports,
                )
            )
            visited_ranges.add(node_range)

        # Always recurse into children to find nested definitions
        for child in node.children:
            walk(child, depth + 1)

    walk(tree.root_node)

    # If no chunks were found (e.g., a config file with no functions),
    # treat the whole file as one chunk
    if not chunks and source.strip():
        chunks.append(
            CodeChunk(
                text=source,
                file_path=file_path,
                repo_name=repo_name,
                language=ext,
                symbol_name=file_path.split("/")[-1],
                symbol_type="file",
                parent_name=None,
                start_line=1,
                end_line=source.count("\n") + 1,
                imports=imports,
            )
        )

    return FileChunks(file_path=file_path, chunks=chunks, file_header=imports)
