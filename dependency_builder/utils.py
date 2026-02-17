"""
Shared utility functions for the dependency_builder package.

Consolidates duplicated logic (e.g., URI cleaning) into a single module
to maintain DRY principles and consistent behavior across all components.
"""

import os
import hashlib
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse


def clean_uri(uri: str) -> str:
    """
    Convert an LSP URI (file://) to an absolute filesystem path.

    Handles:
        - Standard file:// URIs
        - Bare paths (no scheme)
        - URL-encoded characters (%2B → +, %20 → space, etc.)
        - Non-file schemes (returns decoded path component)

    Args:
        uri: An LSP URI string (e.g., "file:///home/user/src/main.cpp")

    Returns:
        Absolute filesystem path string. Empty string if input is empty.

    Examples:
        >>> clean_uri("file:///home/user/src/main.cpp")
        '/home/user/src/main.cpp'
        >>> clean_uri("file:///path/with%20spaces/file.cpp")
        '/path/with spaces/file.cpp'
        >>> clean_uri("")
        ''
    """
    if not uri:
        return ""
    try:
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            path = unquote(parsed.path)
        elif parsed.scheme == "":
            # Bare path, no scheme
            path = unquote(uri)
        else:
            # Other scheme — extract path component
            path = unquote(parsed.path)
        return os.path.abspath(path)
    except Exception:
        # Last-resort fallback for malformed URIs
        return uri.replace("file://", "").replace("%2B", "+")


def to_uri(path: str) -> str:
    """
    Convert a filesystem path to an LSP file:// URI.

    Args:
        path: A filesystem path (relative or absolute).

    Returns:
        A file:// URI string.

    Examples:
        >>> to_uri("/home/user/src/main.cpp")
        'file:///home/user/src/main.cpp'
    """
    return f"file://{Path(path).resolve()}"


def resolve_file_path(input_path: str, project_root: str) -> str:
    """
    Resolve a potentially relative file path against a project root.

    Resolution order:
        1. Try joining with project_root (handles relative paths like './src/main.cpp')
        2. Try as a pure absolute path
        3. Fallback: return the root-relative version anyway

    Args:
        input_path: The file path to resolve (relative or absolute).
        project_root: The project root directory.

    Returns:
        Absolute path string. Empty string if input_path is empty.
    """
    if not input_path:
        return ""

    # Strip leading './' or '/' for clean joining
    cleaned_input = input_path.lstrip("." + os.sep)
    root_relative = os.path.abspath(os.path.join(project_root, cleaned_input))

    if os.path.exists(root_relative):
        return root_relative

    # Try as a pure absolute path
    abs_input = os.path.abspath(input_path)
    if os.path.exists(abs_input):
        return abs_input

    # Fallback: return root-relative version
    return root_relative


def safe_filename(raw_name: str, max_length: int = 200,
                  hash_suffix_length: int = 12,
                  prefix_length: int = 180) -> str:
    """
    Generate a safe, collision-resistant filename from a raw string.

    For filenames within the max_length limit, simply sanitizes path separators.
    For longer filenames, truncates and appends an MD5 hash suffix.

    Args:
        raw_name: The raw filename string.
        max_length: Maximum allowed filename length before hashing.
        hash_suffix_length: Number of hash characters to use as suffix.
        prefix_length: Number of characters to keep before the hash suffix.

    Returns:
        A safe filename string.
    """
    safe = raw_name.replace("/", "_").replace("\\", "_")

    if len(safe) > max_length:
        name_hash = hashlib.md5(safe.encode()).hexdigest()[:hash_suffix_length]
        safe = safe[:prefix_length] + f"_{name_hash}.json"

    return safe


def compute_content_hash(file_path: str, chunk_size: int = 8192) -> Optional[str]:
    """
    Compute an MD5 content hash for cache collision detection.

    Args:
        file_path: Path to the file to hash.
        chunk_size: Size of read chunks in bytes.

    Returns:
        MD5 hex digest string, or None on error.
    """
    try:
        h = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()
    except (IOError, OSError):
        return None


def normalize_path_in_project(file_name: str, project_root: str) -> str:
    """
    Normalize a file path relative to a project root.

    If the file_name is an absolute path under project_root, converts it
    to a relative path. Otherwise returns it unchanged.

    Args:
        file_name: The file path to normalize.
        project_root: The project root directory.

    Returns:
        Normalized path string.
    """
    if file_name and os.path.isabs(file_name):
        try:
            if file_name.startswith(project_root):
                return os.path.relpath(file_name, project_root)
        except ValueError:
            pass
    return file_name
