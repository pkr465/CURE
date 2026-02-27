"""
CURE — Codebase Update & Refactor Engine
Batch Patch Agent

Parses a multi-file patch (with ``===`` file headers) and applies each
file's diff to the corresponding source file in the codebase, writing
patched copies to ``out/patched_files/<folder_structure>``.

Patch file format (normal diff with ``===`` headers)::

    === //server/depot/path/to/file.h#641 — /local/mnt/workspace/path/to/file.h
    2524c2524,2525
    <     A_UINT32 txop_us);
    ---
    >     A_UINT32 txop_us,
    >     wal_pdev_t *pdev);
    === //server/depot/path/to/cfg.c#805 — /local/mnt/workspace/path/to/cfg.c
    1589c1589,1591
    <     .opt.txop_truncation_threshold_us = 5000,
    ---
    >     .opt.min_avg_txop_dur_thresh_for_txop_truncation = 10000,
    >     .opt.min_avg_txop_dur_thresh_for_txop_truncation_reg_domain = 6000,
    >     .opt.max_txop_limit = 12000,

Usage::

    # Reads codebase_path from global_config.yaml
    python agents/codebase_batch_patch_agent.py --patch-file t.patch

    # Explicit codebase path
    python agents/codebase_batch_patch_agent.py --patch-file t.patch --codebase-path /path/to/codebase

    # Dry run (show plan without writing files)
    python agents/codebase_batch_patch_agent.py --patch-file t.patch --dry-run

    # Via fixer_workflow.py
    python fixer_workflow.py --batch-patch t.patch --codebase-path /path/to/codebase
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger("codebase_batch_patch_agent")


# ──────────────────────────────────────────────────────────────────────────────
# Data Classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PatchHunk:
    """A single diff hunk (mirrors codebase_patch_agent.PatchHunk)."""

    orig_start: int
    orig_count: int
    new_start: int
    new_count: int
    header: str = ""
    removed_lines: List[str] = field(default_factory=list)
    added_lines: List[str] = field(default_factory=list)
    context_lines: List[str] = field(default_factory=list)
    raw_lines: List[str] = field(default_factory=list)


@dataclass
class FileEntry:
    """One file section extracted from a multi-file patch."""

    server_path: str        # Depot / Perforce path (before ' — ')
    local_path: str         # Absolute local path   (after  ' — ')
    diff_body: str          # Raw diff text for this file
    hunks: List[PatchHunk] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Diff Parsers (standalone — no instance state required)
# ──────────────────────────────────────────────────────────────────────────────

# Regex patterns (same as CodebasePatchAgent)
_UNIFIED_HUNK_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$"
)
_COMBINED_HUNK_RE = re.compile(
    r"^@@@\s+.*?\+(\d+)(?:,(\d+))?\s+@@@(.*)$"
)
_CTX_SEP_RE = re.compile(r"^\*{15,}")
_CTX_ORIG_RE = re.compile(r"^\*\*\*\s+(\d+)(?:,(\d+))?\s+\*{4}")
_CTX_NEW_RE = re.compile(r"^---\s+(\d+)(?:,(\d+))?\s+-{4}")
_NORMAL_CMD_RE = re.compile(
    r"^(\d+)(?:,(\d+))?([acd])(\d+)(?:,(\d+))?$"
)


def detect_diff_format(text: str) -> str:
    """Auto-detect diff format from text. Returns one of
    ``unified``, ``context``, ``normal``, ``combined``, ``unknown``.
    """
    for line in text.splitlines()[:100]:
        if _COMBINED_HUNK_RE.match(line):
            return "combined"
        if _UNIFIED_HUNK_RE.match(line):
            return "unified"
        if _CTX_SEP_RE.match(line):
            return "context"
        if _NORMAL_CMD_RE.match(line):
            return "normal"
    return "unknown"


def parse_normal_diff(text: str) -> List[PatchHunk]:
    """Parse a normal diff (``NUMaNUM``, ``NUMcNUM``, ``NUMdNUM``)."""
    hunks: List[PatchHunk] = []
    lines = text.splitlines()
    i, n = 0, len(lines)

    while i < n:
        m = _NORMAL_CMD_RE.match(lines[i])
        if not m:
            i += 1
            continue

        orig_s = int(m.group(1))
        orig_e = int(m.group(2) or orig_s)
        cmd = m.group(3)
        new_s = int(m.group(4))
        new_e = int(m.group(5) or new_s)
        i += 1

        removed: List[str] = []
        added: List[str] = []
        raw_lines: List[str] = []

        if cmd == "a":
            while i < n and lines[i].startswith("> "):
                content = lines[i][2:]
                added.append(content)
                raw_lines.append(f"+{content}")
                i += 1
            hunks.append(PatchHunk(
                orig_start=orig_s + 1,
                orig_count=0,
                new_start=new_s,
                new_count=new_e - new_s + 1,
                removed_lines=[],
                added_lines=added,
                raw_lines=raw_lines,
            ))

        elif cmd == "d":
            while i < n and lines[i].startswith("< "):
                content = lines[i][2:]
                removed.append(content)
                raw_lines.append(f"-{content}")
                i += 1
            hunks.append(PatchHunk(
                orig_start=orig_s,
                orig_count=orig_e - orig_s + 1,
                new_start=new_s,
                new_count=0,
                removed_lines=removed,
                added_lines=[],
                raw_lines=raw_lines,
            ))

        elif cmd == "c":
            while i < n and lines[i].startswith("< "):
                content = lines[i][2:]
                removed.append(content)
                raw_lines.append(f"-{content}")
                i += 1
            if i < n and lines[i] == "---":
                i += 1
            while i < n and lines[i].startswith("> "):
                content = lines[i][2:]
                added.append(content)
                raw_lines.append(f"+{content}")
                i += 1
            hunks.append(PatchHunk(
                orig_start=orig_s,
                orig_count=orig_e - orig_s + 1,
                new_start=new_s,
                new_count=new_e - new_s + 1,
                removed_lines=removed,
                added_lines=added,
                raw_lines=raw_lines,
            ))

    return hunks


def parse_unified_diff(text: str) -> List[PatchHunk]:
    """Parse a unified diff (``@@`` markers)."""
    hunks: List[PatchHunk] = []
    current: Optional[PatchHunk] = None

    for line in text.splitlines():
        m = _UNIFIED_HUNK_RE.match(line)
        if m:
            if current is not None:
                hunks.append(current)
            current = PatchHunk(
                orig_start=int(m.group(1)),
                orig_count=int(m.group(2) or 1),
                new_start=int(m.group(3)),
                new_count=int(m.group(4) or 1),
                header=m.group(5).strip(),
            )
            continue

        if current is None:
            continue

        if line.startswith("-"):
            current.removed_lines.append(line[1:])
            current.raw_lines.append(line)
        elif line.startswith("+"):
            current.added_lines.append(line[1:])
            current.raw_lines.append(line)
        elif line.startswith(" ") or line == "":
            current.context_lines.append(line[1:] if line.startswith(" ") else line)
            current.raw_lines.append(line)

    if current is not None:
        hunks.append(current)
    return hunks


def parse_diff(text: str) -> Tuple[str, List[PatchHunk]]:
    """Auto-detect format and parse. Returns ``(format_name, hunks)``."""
    fmt = detect_diff_format(text)
    if fmt == "normal":
        return fmt, parse_normal_diff(text)
    if fmt == "unified":
        return fmt, parse_unified_diff(text)
    # Fallback: try normal first (most common in batch patches), then unified
    hunks = parse_normal_diff(text)
    if hunks:
        return "normal", hunks
    hunks = parse_unified_diff(text)
    if hunks:
        return "unified", hunks
    return "unknown", []


def apply_patch(source: str, hunks: List[PatchHunk]) -> str:
    """Apply parsed hunks to source text and return patched content."""
    lines = source.splitlines(keepends=True)
    offset = 0

    for hunk in hunks:
        start = max(0, hunk.orig_start - 1 + offset)
        end = start + hunk.orig_count

        new_lines: List[str] = []
        for raw_line in hunk.raw_lines:
            if raw_line.startswith("+"):
                new_lines.append(raw_line[1:] + "\n")
            elif raw_line.startswith(" ") or raw_line == "":
                content = raw_line[1:] if raw_line.startswith(" ") else raw_line
                new_lines.append(content + "\n")

        if start > len(lines):
            lines.extend(new_lines)
        else:
            lines[start:end] = new_lines

        offset += len(new_lines) - hunk.orig_count

    return "".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Multi-File Patch Parser
# ──────────────────────────────────────────────────────────────────────────────

# Format 1:  === <server_path> — <local_path>
#            (em-dash or hyphen separator, 3 equal signs)
_FILE_HEADER_RE = re.compile(
    r"^===\s+(.+?)\s+[\u2014\-]+\s+(.+)$"
)

# Format 2:  ==== <server_path>#<rev> - <local_path> ====
#            (Perforce standard diff header, 4 equal signs)
_P4_HEADER_RE = re.compile(
    r"^====\s+(.+?)\s+[\u2014\-]+\s+(.+?)\s*={0,4}$"
)

# Format 3:  --- a/<path>  /  +++ b/<path>
#            (Standard unified diff file headers, e.g. git diff)
_UNIFIED_MINUS_RE = re.compile(r"^---\s+(?:a/)?(.+?)(?:\t.*)?$")
_UNIFIED_PLUS_RE = re.compile(r"^\+\+\+\s+(?:b/)?(.+?)(?:\t.*)?$")

# Format 4:  diff --git a/<path> b/<path>
_GIT_DIFF_RE = re.compile(r"^diff\s+--git\s+a/(.+?)\s+b/(.+)$")

# Format 5:  diff <flags> <path_a> <path_b>   (plain diff command output)
_PLAIN_DIFF_RE = re.compile(r"^diff\s+(?:-\S+\s+)*(\S+)\s+(\S+)$")


def _is_file_header(line: str) -> bool:
    """Return True if the line looks like any multi-file separator."""
    return bool(
        _FILE_HEADER_RE.match(line)
        or _P4_HEADER_RE.match(line)
        or _GIT_DIFF_RE.match(line)
        or _PLAIN_DIFF_RE.match(line)
    )


def _parse_triple_eq(lines: List[str]) -> List[FileEntry]:
    """Parse ``=== server — local`` format."""
    entries: List[FileEntry] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].rstrip("\n\r")
        m = _FILE_HEADER_RE.match(line)
        if not m:
            i += 1
            continue
        server_path = m.group(1).strip()
        local_path = m.group(2).strip()
        i += 1
        body: List[str] = []
        while i < n:
            nl = lines[i].rstrip("\n\r")
            if _FILE_HEADER_RE.match(nl):
                break
            body.append(nl)
            i += 1
        diff_body = "\n".join(body).strip()
        if diff_body:
            entries.append(FileEntry(server_path=server_path, local_path=local_path, diff_body=diff_body))
    return entries


def _parse_p4_header(lines: List[str]) -> List[FileEntry]:
    """Parse ``==== depot#rev - local ====`` Perforce format."""
    entries: List[FileEntry] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].rstrip("\n\r")
        m = _P4_HEADER_RE.match(line)
        if not m:
            i += 1
            continue
        server_path = m.group(1).strip()
        local_path = m.group(2).strip()
        i += 1
        body: List[str] = []
        while i < n:
            nl = lines[i].rstrip("\n\r")
            if _P4_HEADER_RE.match(nl):
                break
            body.append(nl)
            i += 1
        diff_body = "\n".join(body).strip()
        if diff_body:
            entries.append(FileEntry(server_path=server_path, local_path=local_path, diff_body=diff_body))
    return entries


def _parse_unified_headers(lines: List[str]) -> List[FileEntry]:
    """Parse standard unified diff (``--- a/`` / ``+++ b/``) or ``diff --git``."""
    entries: List[FileEntry] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i].rstrip("\n\r")

        # Try ``diff --git a/X b/X`` first
        gm = _GIT_DIFF_RE.match(line)
        if gm:
            server_path = gm.group(1).strip()
            local_path = gm.group(2).strip()
            i += 1
            # Skip optional index/mode lines and --- / +++ lines
            while i < n:
                peek = lines[i].rstrip("\n\r")
                if peek.startswith("---") or peek.startswith("+++") or peek.startswith("index ") \
                        or peek.startswith("old mode") or peek.startswith("new mode") \
                        or peek.startswith("new file") or peek.startswith("deleted file"):
                    # Possibly update local_path from +++ line
                    pm = _UNIFIED_PLUS_RE.match(peek)
                    if pm:
                        local_path = pm.group(1).strip()
                    i += 1
                else:
                    break
            body: List[str] = []
            while i < n:
                nl = lines[i].rstrip("\n\r")
                if _GIT_DIFF_RE.match(nl) or _PLAIN_DIFF_RE.match(nl):
                    break
                body.append(nl)
                i += 1
            diff_body = "\n".join(body).strip()
            if diff_body:
                entries.append(FileEntry(server_path=server_path, local_path=local_path, diff_body=diff_body))
            continue

        # Try ``diff <flags> <a> <b>``
        dm = _PLAIN_DIFF_RE.match(line)
        if dm:
            server_path = dm.group(1).strip()
            local_path = dm.group(2).strip()
            i += 1
            # Skip --- / +++ lines that follow
            while i < n:
                peek = lines[i].rstrip("\n\r")
                if peek.startswith("---") or peek.startswith("+++"):
                    pm = _UNIFIED_PLUS_RE.match(peek)
                    if pm:
                        local_path = pm.group(1).strip()
                    i += 1
                else:
                    break
            body = []
            while i < n:
                nl = lines[i].rstrip("\n\r")
                if _GIT_DIFF_RE.match(nl) or _PLAIN_DIFF_RE.match(nl):
                    break
                body.append(nl)
                i += 1
            diff_body = "\n".join(body).strip()
            if diff_body:
                entries.append(FileEntry(server_path=server_path, local_path=local_path, diff_body=diff_body))
            continue

        # Try bare ``--- a/X`` / ``+++ b/X`` pair (no diff header line)
        mm = _UNIFIED_MINUS_RE.match(line)
        if mm and (i + 1 < n):
            next_line = lines[i + 1].rstrip("\n\r")
            pm = _UNIFIED_PLUS_RE.match(next_line)
            if pm:
                server_path = mm.group(1).strip()
                local_path = pm.group(1).strip()
                i += 2
                body = []
                while i < n:
                    nl = lines[i].rstrip("\n\r")
                    if _UNIFIED_MINUS_RE.match(nl) or _GIT_DIFF_RE.match(nl) or _PLAIN_DIFF_RE.match(nl):
                        break
                    body.append(nl)
                    i += 1
                diff_body = "\n".join(body).strip()
                if diff_body:
                    entries.append(FileEntry(server_path=server_path, local_path=local_path, diff_body=diff_body))
                continue

        i += 1

    return entries


def parse_multi_file_patch(patch_text: str) -> List[FileEntry]:
    """Parse a multi-file patch, auto-detecting the header format.

    Supported formats:

    1. ``=== <server_path> — <local_path>``  (CURE / custom)
    2. ``==== <depot_path>#rev - <local_path> ====``  (Perforce)
    3. ``diff --git a/<path> b/<path>``  (Git)
    4. ``diff [-flags] <path_a> <path_b>``  (plain diff)
    5. ``--- a/<path>`` / ``+++ b/<path>``  (unified diff headers)

    Returns a list of :class:`FileEntry` objects, one per file section.
    """
    lines = patch_text.splitlines(keepends=True)

    # Quick scan to detect the dominant format
    has_triple_eq = any(_FILE_HEADER_RE.match(l.rstrip("\n\r")) for l in lines[:200])
    has_p4_eq = any(_P4_HEADER_RE.match(l.rstrip("\n\r")) for l in lines[:200])
    has_git_diff = any(_GIT_DIFF_RE.match(l.rstrip("\n\r")) for l in lines[:200])
    has_plain_diff = any(_PLAIN_DIFF_RE.match(l.rstrip("\n\r")) for l in lines[:200])
    has_unified = any(_UNIFIED_MINUS_RE.match(l.rstrip("\n\r")) for l in lines[:200])

    # Try parsers in order of specificity
    if has_triple_eq:
        entries = _parse_triple_eq(lines)
        if entries:
            return entries

    if has_p4_eq:
        entries = _parse_p4_header(lines)
        if entries:
            return entries

    if has_git_diff or has_plain_diff or has_unified:
        entries = _parse_unified_headers(lines)
        if entries:
            return entries

    # Fallback: try all parsers
    for parser_fn in (_parse_triple_eq, _parse_p4_header, _parse_unified_headers):
        entries = parser_fn(lines)
        if entries:
            return entries

    return []


# ──────────────────────────────────────────────────────────────────────────────
# Batch Patch Agent
# ──────────────────────────────────────────────────────────────────────────────

class CodebaseBatchPatchAgent:
    """Parse a multi-file patch and apply it to a local codebase.

    Copies each referenced file to ``out/patched_files/<rel_path>``
    and writes the patched version there, preserving the codebase's
    folder structure.
    """

    def __init__(
        self,
        patch_file: str,
        codebase_path: str,
        output_dir: str = "./out",
        config: Optional[object] = None,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.patch_file = Path(patch_file).resolve()
        self.codebase_path = Path(codebase_path).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.patched_dir = self.output_dir / "patched_files"
        self.config = config
        self.dry_run = dry_run
        self.verbose = verbose
        self.logger = logging.getLogger("codebase_batch_patch_agent")

        # Stats
        self.patched_count = 0
        self.skipped_count = 0
        self.failed_count = 0

    # ─── Public API ───────────────────────────────────────────────────

    def run(self) -> dict:
        """Execute the batch patching pipeline.

        Returns:
            Summary dict with counts and file list.
        """
        self.logger.info("=" * 60)
        self.logger.info(" Batch Patch Agent")
        self.logger.info("=" * 60)
        self.logger.info(f"  Patch file: {self.patch_file}")
        self.logger.info(f"  Codebase:   {self.codebase_path}")
        self.logger.info(f"  Output:     {self.patched_dir}")
        if self.dry_run:
            self.logger.info("  Mode:       DRY RUN")

        # Validate inputs
        if not self.patch_file.exists():
            self.logger.error(f"Patch file not found: {self.patch_file}")
            return {"status": "error", "message": "Patch file not found"}

        if not self.codebase_path.exists():
            self.logger.error(f"Codebase path not found: {self.codebase_path}")
            return {"status": "error", "message": "Codebase path not found"}

        # Read and parse multi-file patch
        patch_text = self.patch_file.read_text(encoding="utf-8", errors="replace")
        if patch_text.startswith("\ufeff"):
            patch_text = patch_text[1:]
        patch_text = patch_text.replace("\r\n", "\n").replace("\r", "\n")

        entries = parse_multi_file_patch(patch_text)
        if not entries:
            # Show first few lines to help debug
            preview = patch_text[:500].splitlines()[:5]
            self.logger.warning("No file entries found in patch file.")
            self.logger.warning("    Supported header formats:")
            self.logger.warning("      === <server_path> — <local_path>")
            self.logger.warning("      ==== <depot_path>#rev - <local_path> ====")
            self.logger.warning("      diff --git a/<path> b/<path>")
            self.logger.warning("      --- a/<path> / +++ b/<path>")
            self.logger.warning("    First lines of patch file:")
            for pl in preview:
                self.logger.warning(f"      | {pl}")
            return {"status": "warning", "message": "No file entries found"}

        self.logger.info(f"Parsed patch file: {len(entries)} file(s) found.")

        # Create output directory
        if not self.dry_run:
            self.patched_dir.mkdir(parents=True, exist_ok=True)

        # Process each file
        patched_files: List[str] = []
        for idx, entry in enumerate(entries, 1):
            result = self._process_entry(idx, len(entries), entry)
            if result:
                patched_files.append(result)

        # CCLS temporary artifact cleanup
        self._cleanup_ccls_artifacts()

        # Summary
        self.logger.info(
            f"Summary: {self.patched_count} patched, "
            f"{self.skipped_count} skipped, {self.failed_count} failed"
        )
        if patched_files:
            self.logger.info(f"Output directory: {self.patched_dir}/")

        return {
            "status": "completed",
            "patched": self.patched_count,
            "skipped": self.skipped_count,
            "failed": self.failed_count,
            "files": patched_files,
            "output_dir": str(self.patched_dir),
        }

    # ─── CCLS cleanup ─────────────────────────────────────────────────

    def _cleanup_ccls_artifacts(self):
        """Remove temporary CCLS JSON artifacts from the output directory.

        Preserves the .ccls-cache directory and .ccls config.
        Only removes dependency-artifact JSON files tracked in cache metadata.
        """
        try:
            from dependency_builder.cleanup import cleanup_ccls_artifacts
            stats = cleanup_ccls_artifacts(
                output_dir=str(self.output_dir),
                project_root=str(self.codebase_path),
            )
            if stats["files_removed"] > 0:
                mb = stats["bytes_freed"] / (1024 * 1024)
                self.logger.info(
                    f"CCLS cleanup: {stats['files_removed']} temp files removed "
                    f"({mb:.1f} MB freed)"
                )
        except ImportError:
            pass  # cleanup module not available
        except Exception as e:
            self.logger.debug(f"CCLS cleanup: {e}")

    # ─── Per-file processing ──────────────────────────────────────────

    def _process_entry(
        self, idx: int, total: int, entry: FileEntry
    ) -> Optional[str]:
        """Process a single file entry. Returns the output path or None."""

        # Resolve the source file path
        source_path = self._resolve_source_path(entry)
        display_name = source_path.name if source_path else Path(entry.local_path).name

        if not source_path or not source_path.exists():
            self.logger.warning(f"[{idx}/{total}] {display_name} — File not found — SKIPPED")
            if self.verbose and entry.local_path:
                self.logger.debug(f"         Tried: {entry.local_path}")
            self.skipped_count += 1
            return None

        # Parse the diff
        fmt, hunks = parse_diff(entry.diff_body)
        if not hunks:
            self.logger.warning(f"[{idx}/{total}] {display_name} — No hunks parsed — SKIPPED")
            self.skipped_count += 1
            return None

        # Read original
        try:
            original_content = source_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            self.logger.error(f"[{idx}/{total}] {display_name} — Read error: {exc} — FAILED")
            self.failed_count += 1
            return None

        # Apply patch
        try:
            patched_content = apply_patch(original_content, hunks)
        except Exception as exc:
            self.logger.error(f"[{idx}/{total}] {display_name} — Patch error: {exc} — FAILED")
            self.failed_count += 1
            return None

        # Determine output path (preserve folder structure)
        rel_path = self._get_relative_path(source_path)
        out_path = self.patched_dir / rel_path

        if self.dry_run:
            self.logger.info(f"[{idx}/{total}] {display_name} — {len(hunks)} hunk(s) — WOULD PATCH")
            if self.verbose:
                self.logger.debug(f"         {source_path} -> {out_path}")
            self.patched_count += 1
            return str(out_path)

        # Write patched file
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(patched_content, encoding="utf-8")
        except Exception as exc:
            self.logger.error(f"[{idx}/{total}] {display_name} — Write error: {exc} — FAILED")
            self.failed_count += 1
            return None

        self.logger.info(f"[{idx}/{total}] {display_name} — {len(hunks)} hunk(s) — PATCHED")
        self.patched_count += 1
        return str(out_path)

    # ─── Path resolution helpers ──────────────────────────────────────

    def _resolve_source_path(self, entry: FileEntry) -> Optional[Path]:
        """Resolve the local source file path from a FileEntry.

        Resolution order:
          1. ``entry.local_path`` if it exists as-is
          2. ``entry.local_path`` relative to ``codebase_path``
          3. Filename match under ``codebase_path`` (fallback)
        """
        # 1. Absolute local path from patch header
        local = Path(entry.local_path)
        if local.is_absolute() and local.exists():
            return local

        # 2. Try as relative to codebase_path
        candidate = self.codebase_path / entry.local_path
        if candidate.exists():
            return candidate

        # 3. Try stripping common prefixes to find relative path
        #    e.g. /local/mnt/workspace/wlan/src/file.c → wlan/src/file.c
        local_str = str(local)
        codebase_str = str(self.codebase_path)
        if local_str.startswith(codebase_str):
            return local

        # 4. Try matching by filename/subfolder from server path
        #    e.g. //depot/.../src/sched_algo/file.h → search under codebase
        server_parts = Path(entry.server_path.lstrip("/")).parts
        for depth in range(min(5, len(server_parts)), 0, -1):
            sub = Path(*server_parts[-depth:])
            candidate = self.codebase_path / sub
            if candidate.exists():
                return candidate

        return None

    def _get_relative_path(self, source_path: Path) -> Path:
        """Get relative path of source within codebase, for output folder structure."""
        try:
            return source_path.resolve().relative_to(self.codebase_path.resolve())
        except ValueError:
            # Source not under codebase_path — use just the filename
            return Path(source_path.name)


# Backward-compatible alias
BatchPatchAgent = CodebaseBatchPatchAgent


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_codebase_path(args) -> Optional[str]:
    """Resolve codebase_path from CLI arg or global_config.yaml."""
    if args.codebase_path:
        return args.codebase_path

    # Try GlobalConfig
    try:
        from utils.parsers.global_config_parser import GlobalConfig
        config_file = getattr(args, "config_file", None)
        gc = GlobalConfig(config_file=config_file) if config_file else GlobalConfig()
        path = gc.get_path("paths.code_base_path")
        if path:
            return path
    except Exception:
        pass

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Apply a multi-file patch to a codebase, "
                    "writing patched files to out/patched_files/.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--patch-file", required=True,
        help="Path to the multi-file patch (=== header format)",
    )
    parser.add_argument(
        "--codebase-path", default=None,
        help="Root directory of the source code "
             "(defaults to global_config.yaml paths.code_base_path)",
    )
    parser.add_argument(
        "--out-dir", default="out",
        help="Output directory (patched files go to <out-dir>/patched_files/)",
    )
    parser.add_argument(
        "--config-file", default=None,
        help="Path to custom global_config.yaml",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be patched without writing files",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s — %(levelname)s — %(message)s",
    )

    # Resolve codebase path
    codebase_path = _resolve_codebase_path(args)
    if not codebase_path:
        print("[!] Error: --codebase-path not provided and could not be "
              "resolved from global_config.yaml.")
        print("    Provide --codebase-path or set paths.code_base_path in config.")
        sys.exit(1)

    agent = CodebaseBatchPatchAgent(
        patch_file=args.patch_file,
        codebase_path=codebase_path,
        output_dir=args.out_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    result = agent.run()
    sys.exit(0 if result.get("status") == "completed" else 1)


if __name__ == "__main__":
    main()
