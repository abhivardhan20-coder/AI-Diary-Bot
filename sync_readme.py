#!/usr/bin/env python3
"""
sync_readme.py

A reusable utility script to automatically generate versioned README files
containing a complete, portable code snapshot of the project's source code,
excluding build artifacts, dependencies, caches, logs, and lock files.
"""

import os
import sys
import argparse
import datetime
import fnmatch
from pathlib import Path
from typing import List, Set, Dict, Any

# Default configuration for directory and file exclusions
DEFAULT_EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "venv",
    ".venv",
    "__pycache__",
    ".next",
    ".cache",
    "backups",
    "data",
    "exports",
    "logs",
    ".gemini",
    ".idea",
    ".vscode",
    "html_artifacts",
    "browser_recordings",
}

DEFAULT_EXCLUDE_FILES = {
    # Lock files
    "uv.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    # Databases and binary outputs
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    # Environments
    ".env",
    ".env.local",
    ".env.*",
    # Script itself and versioned README files to prevent infinite recursion
    "sync_readme.py",
    "README_v*.md",
    # OS files
    ".DS_Store",
    "thumbs.db",
}

# Extension to Markdown syntax highlighting name mapping
EXTENSION_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".css": "css",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".bat": "batch",
    ".ps1": "powershell",
    ".sql": "sql",
    ".ini": "ini",
    ".dockerignore": "dockerignore",
    "Dockerfile": "dockerfile",
    "dockerfile": "dockerfile",
}

def is_binary(file_path: Path) -> bool:
    """Check if a file is binary using standard heuristics."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            if b"\0" in chunk:
                return True
            # Attempt to decode as UTF-8
            chunk.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True
    except Exception:
        # Fallback: if we cannot read/decode it, treat as binary
        return True

def should_exclude(path: Path, exclude_dirs: Set[str], exclude_files: Set[str], base_dir: Path) -> bool:
    """Determine if a file or directory should be excluded based on configured rules."""
    # Check parent directories
    try:
        relative_parts = path.relative_to(base_dir).parts
    except ValueError:
        relative_parts = path.parts

    for part in relative_parts:
        if part in exclude_dirs:
            return True

    # Check file name against exact matches and glob patterns
    name = path.name
    if name in exclude_files:
        return True

    for pattern in exclude_files:
        if fnmatch.fnmatch(name, pattern):
            return True

    return False

def build_tree_dict(relative_paths: List[str]) -> Dict[str, Any]:
    """Build a nested dictionary representation of the file tree."""
    tree: Dict[str, Any] = {}
    for path_str in relative_paths:
        parts = path_str.replace("\\", "/").split("/")
        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]
    return tree

def render_tree(node: Dict[str, Any], prefix: str = "") -> List[str]:
    """Recursively render the nested dictionary file tree to list of strings."""
    lines = []
    keys = sorted(node.keys())
    for i, key in enumerate(keys):
        is_last = (i == len(keys) - 1)
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{key}")
        if isinstance(node[key], dict) and node[key]:
            new_prefix = prefix + ("    " if is_last else "│   ")
            lines.extend(render_tree(node[key], new_prefix))
    return lines

def get_next_version(output_dir: Path) -> int:
    """Find the next README version by scanning for existing README_v*.md files."""
    max_version = 0
    if output_dir.exists():
        for item in output_dir.iterdir():
            if item.is_file() and item.name.startswith("README_v") and item.name.endswith(".md"):
                # Extract version number
                version_str = item.name[len("README_v"):-len(".md")]
                if version_str.isdigit():
                    max_version = max(max_version, int(version_str))
    return max_version + 1

def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Generate a versioned README snapshot of the project's source code."
    )
    parser.add_argument(
        "--dir", "-d",
        default=".",
        help="The target directory to scan (default: current directory)."
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=".",
        help="Directory to save the generated README file (default: same as target directory)."
    )
    parser.add_argument(
        "--exclude-dirs",
        help="Comma-separated list of additional directories to exclude."
    )
    parser.add_argument(
        "--exclude-files",
        help="Comma-separated list of additional file names/patterns to exclude."
    )
    parser.add_argument(
        "--include-exts",
        help="Comma-separated list of file extensions to exclusively include (e.g. '.py,.json')."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and output a summary without writing the actual README file."
    )

    args = parser.parse_args()

    # Resolve paths
    target_dir = Path(args.dir).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not target_dir.exists():
        print(f"Error: Target directory '{target_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Compile exclusion sets
    exclude_dirs = set(DEFAULT_EXCLUDE_DIRS)
    if args.exclude_dirs:
        for d in args.exclude_dirs.split(","):
            exclude_dirs.add(d.strip())

    exclude_files = set(DEFAULT_EXCLUDE_FILES)
    if args.exclude_files:
        for f in args.exclude_files.split(","):
            exclude_files.add(f.strip())

    include_exts = None
    if args.include_exts:
        include_exts = {ext.strip().lower() for ext in args.include_exts.split(",")}
        # Standardize extensions to start with dot
        include_exts = {ext if ext.startswith(".") else f".{ext}" for ext in include_exts}

    print(f"Scanning directory: {target_dir}")
    print(f"Excluded directories: {', '.join(sorted(exclude_dirs))}")
    print(f"Excluded file patterns: {', '.join(sorted(exclude_files))}")
    if include_exts:
        print(f"Only including extensions: {', '.join(sorted(include_exts))}")

    # Discover and collect files
    included_files: List[Path] = []
    for root, dirs, files in os.walk(target_dir):
        # Prune excluded directories in-place to prevent walking into them
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        for file in files:
            file_path = Path(root) / file
            if should_exclude(file_path, exclude_dirs, exclude_files, target_dir):
                continue
            
            # Check extension if specified
            if include_exts and file_path.suffix.lower() not in include_exts:
                continue

            # Check if binary
            if is_binary(file_path):
                continue

            included_files.append(file_path)

    # Sort files by path for deterministic generation
    included_files.sort()

    total_files = len(included_files)
    print(f"Found {total_files} relevant source code files.")

    if total_files == 0:
        print("No files matched the inclusion criteria. Exiting.")
        sys.exit(0)

    # Get version number
    version = get_next_version(output_dir)
    readme_filename = f"README_v{version}.md"
    readme_path = output_dir / readme_filename

    # Build relative paths and file tree
    relative_paths = [str(f.relative_to(target_dir)) for f in included_files]
    tree_dict = build_tree_dict(relative_paths)
    tree_lines = render_tree(tree_dict)
    tree_str = "\n".join(tree_lines)

    # Generate Markdown Content
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    project_name = target_dir.name

    markdown_parts = [
        f"# Project Snapshot: {project_name}",
        "",
        "> [!NOTE]",
        "> This is an automatically generated, portable code snapshot of the project.",
        "> It contains metadata, the file tree layout, and the complete contents of all non-excluded files.",
        "",
        "## Snapshot Metadata",
        f"- **Generation Timestamp**: {timestamp} (Local Time)",
        f"- **README Version**: v{version}",
        f"- **Total Files Included**: {total_files}",
        "",
        "## Project Directory Tree",
        "```text",
        ".",
        tree_str,
        "```",
        "",
        "---",
        "",
        "## File Contents",
        "",
    ]

    for file_path in included_files:
        rel_path = file_path.relative_to(target_dir)
        rel_path_str = str(rel_path).replace("\\", "/")
        
        # Determine language for markdown syntax highlighting
        ext = file_path.suffix.lower()
        # Check both suffix and file name (e.g. Dockerfile)
        lang = EXTENSION_LANGUAGE_MAP.get(ext, EXTENSION_LANGUAGE_MAP.get(file_path.name, ""))

        markdown_parts.append(f"### 📄 `{rel_path_str}`")
        markdown_parts.append("")
        markdown_parts.append(f"```{lang}")
        
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            markdown_parts.append(content)
        except Exception as e:
            markdown_parts.append(f"/* Error reading file: {e} */")
            
        markdown_parts.append("```")
        markdown_parts.append("")

    full_markdown = "\n".join(markdown_parts)

    if args.dry_run:
        print("\n=== DRY RUN ===")
        print(f"Would write to: {readme_path}")
        print(f"Version: v{version}")
        print(f"Total files: {total_files}")
        print("Directory Tree preview:")
        print(".\n" + tree_str)
        print("===============")
    else:
        # Write to file
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(full_markdown)
        print(f"\nSuccessfully generated: {readme_path}")

if __name__ == "__main__":
    main()
