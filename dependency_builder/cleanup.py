"""
dependency_builder.cleanup
~~~~~~~~~~~~~~~~~~~~~~~~~~

Post-run cleanup of CCLS artifacts produced during ``--use-ccls`` analysis.

Artifacts cleaned (by default only temporary JSON files):
    1. ``.cache_metadata.json`` — dependency handler cache metadata
    2. Individual dependency-artifact JSON files tracked by the cache metadata

Preserved by default (expensive to regenerate):
    3. ``.ccls-cache/``  — CCLS index cache directory (set remove_ccls_cache=True to delete)
    4. ``.ccls``         — CCLS config file in project root (set remove_ccls_config=True to delete)

Usage:
    from dependency_builder.cleanup import cleanup_ccls_artifacts
    cleanup_ccls_artifacts(output_dir="/path/to/out", project_root="/path/to/src")
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def cleanup_ccls_artifacts(
    output_dir: str,
    project_root: Optional[str] = None,
    cache_metadata_filename: str = ".cache_metadata.json",
    remove_ccls_cache: bool = False,
    remove_dep_artifacts: bool = True,
    remove_ccls_config: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Remove temporary CCLS-generated artifacts from the output directory.

    By default only temporary dependency-artifact JSON files (tracked in
    cache metadata) are removed.  The ``.ccls-cache/`` directory and the
    ``.ccls`` project config are **preserved** by default because they are
    expensive to regenerate and are needed for subsequent runs.

    Args:
        output_dir:              Path to the output directory (e.g. ``./out``).
        project_root:            Path to the analysed project root. If provided
                                 and *remove_ccls_config* is True, the ``.ccls``
                                 config file at the root is removed.
        cache_metadata_filename: Name of the cache metadata file
                                 (default: ``.cache_metadata.json``).
        remove_ccls_cache:       Remove the ``.ccls-cache/`` directory.
                                 **Default False** — the cache is expensive to
                                 rebuild and is reused across runs.
        remove_dep_artifacts:    Remove individual dependency-artifact JSON files
                                 tracked in the cache metadata.
        remove_ccls_config:      Remove the ``.ccls`` file from project root.
                                 **Default False** — preserves project config.
        dry_run:                 If True, log what *would* be removed but don't
                                 actually delete anything.

    Returns:
        Summary dict with counts: ``files_removed``, ``dirs_removed``,
        ``bytes_freed``, ``errors``.
    """
    stats = {"files_removed": 0, "dirs_removed": 0, "bytes_freed": 0, "errors": []}
    abs_out = os.path.abspath(output_dir)

    # ── 1. Remove dependency-artifact JSON files via cache metadata ───────
    if remove_dep_artifacts:
        meta_path = os.path.join(abs_out, cache_metadata_filename)
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                for cache_key, entry in metadata.items():
                    artifact = entry.get("artifact_path", "")
                    if artifact and os.path.isfile(artifact):
                        size = os.path.getsize(artifact)
                        if dry_run:
                            logger.info("[dry-run] Would remove: %s (%d bytes)", artifact, size)
                        else:
                            os.remove(artifact)
                            stats["files_removed"] += 1
                            stats["bytes_freed"] += size
                            logger.debug("Removed dependency artifact: %s", artifact)

                # Remove the metadata file itself
                size = os.path.getsize(meta_path)
                if dry_run:
                    logger.info("[dry-run] Would remove: %s", meta_path)
                else:
                    os.remove(meta_path)
                    stats["files_removed"] += 1
                    stats["bytes_freed"] += size
                    logger.debug("Removed cache metadata: %s", meta_path)

            except (json.JSONDecodeError, IOError) as e:
                logger.warning("Could not read cache metadata for cleanup: %s", e)
                stats["errors"].append(str(e))

    # ── 2. Remove .ccls-cache directory ──────────────────────────────────
    if remove_ccls_cache:
        ccls_cache = os.path.join(abs_out, ".ccls-cache")
        if os.path.isdir(ccls_cache):
            if dry_run:
                # Calculate size
                total = sum(
                    os.path.getsize(os.path.join(dp, f))
                    for dp, _, fnames in os.walk(ccls_cache)
                    for f in fnames
                )
                logger.info("[dry-run] Would remove directory: %s (~%d bytes)", ccls_cache, total)
            else:
                try:
                    total = sum(
                        os.path.getsize(os.path.join(dp, f))
                        for dp, _, fnames in os.walk(ccls_cache)
                        for f in fnames
                    )
                    shutil.rmtree(ccls_cache)
                    stats["dirs_removed"] += 1
                    stats["bytes_freed"] += total
                    logger.info("Removed .ccls-cache directory (%d bytes freed)", total)
                except OSError as e:
                    logger.warning("Failed to remove .ccls-cache: %s", e)
                    stats["errors"].append(str(e))

    # ── 3. Remove .ccls config from project root ─────────────────────────
    if remove_ccls_config and project_root:
        ccls_config = os.path.join(os.path.abspath(project_root), ".ccls")
        if os.path.isfile(ccls_config):
            size = os.path.getsize(ccls_config)
            if dry_run:
                logger.info("[dry-run] Would remove: %s", ccls_config)
            else:
                try:
                    os.remove(ccls_config)
                    stats["files_removed"] += 1
                    stats["bytes_freed"] += size
                    logger.debug("Removed .ccls config: %s", ccls_config)
                except OSError as e:
                    logger.warning("Failed to remove .ccls config: %s", e)
                    stats["errors"].append(str(e))

    # ── Summary ──────────────────────────────────────────────────────────
    mb_freed = stats["bytes_freed"] / (1024 * 1024)
    action = "Would free" if dry_run else "Freed"
    logger.info(
        "CCLS cleanup: %d files removed, %d dirs removed, %s %.1f MB",
        stats["files_removed"],
        stats["dirs_removed"],
        action,
        mb_freed,
    )

    return stats
