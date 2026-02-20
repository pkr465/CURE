"""
Codebase Constraint Generator — Auto-generates constraint .md files
by scanning the codebase for enums, structs, macros, bit-field patterns,
and helper/validator functions.

Produces a `codebase_constraints.md` compatible with the existing
constraint pipeline (_load_constraints → prompt injection).

Usage (CLI):
    python agents/constraints/codebase_constraint_generator.py \
        --codebase-path /path/to/src \
        --output agents/constraints/codebase_constraints.md \
        --exclude-dirs build,vendor \
        --verbose

Usage (programmatic):
    from agents.constraints.codebase_constraint_generator import generate_constraints
    md_text = generate_constraints("/path/to/src", exclude_dirs=["build"])
"""

import argparse
import fnmatch
import logging
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════════

_C_CPP_EXTS = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".hxx"}
_HEADER_EXTS = {".h", ".hpp", ".hh", ".hxx"}

_DEFAULT_EXCLUDE_DIRS = {
    ".git", ".svn", "build", "dist", "out", "bin", "obj",
    "third_party", "external", "vendor", "__pycache__",
    "CMakeFiles", ".ccls-cache", "node_modules", ".venv",
}

# Hardware-initialised struct name fragments (case-insensitive)
_HW_STRUCT_HINTS = {
    "soc", "pdev", "hif", "ctx", "handle", "wmi", "hdd",
    "cdp", "hal", "htc", "ce_", "dp_", "wlan", "ath", "ar_",
    "qdf", "nss", "cfg80211", "ieee80211", "netdev",
}

# Validator / checker function name patterns
_VALIDATOR_RE = re.compile(
    r"^(?:is_valid|validate|check|verify|assert|ensure|confirm|test_valid)",
    re.IGNORECASE,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  Regex patterns (mirrors header_context_builder.py)
# ═══════════════════════════════════════════════════════════════════════════════

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+[<"]([^">]+)[>"]', re.MULTILINE)

_ENUM_TYPEDEF_RE = re.compile(
    r'typedef\s+enum\s+(\w+)?\s*\{([^}]*)\}\s*(\w+)\s*;', re.DOTALL
)
_ENUM_PLAIN_RE = re.compile(
    r'(?<!typedef\s)enum\s+(\w+)\s*\{([^}]*)\}', re.DOTALL
)

_MACRO_RE = re.compile(
    r'^\s*#\s*define\s+(\w+)(?:\([^)]*\))?\s+(.*?)$', re.MULTILINE
)

_INCLUDE_GUARD_RE = re.compile(
    r"^_+[A-Z][A-Z0-9_]*_H_*$|^[A-Z][A-Z0-9_]*_H(?:EADER)?_*$|^__[A-Z][A-Z0-9_]*_H__$"
)

_STRUCT_TYPEDEF_RE = re.compile(
    r'typedef\s+(struct|union)\s+(\w+)?\s*\{', re.DOTALL
)
_STRUCT_PLAIN_RE = re.compile(
    r'(?<!typedef\s)(struct|union)\s+(\w+)\s*\{', re.DOTALL
)

_FUNC_PROTO_RE = re.compile(
    r'^[ \t]*((?:(?:static|inline|extern|const|volatile|unsigned|signed|'
    r'long|short|struct|enum)\s+)*[\w*]+(?:\s*\*)*)\s+(\w+)\s*\(([^)]*)\)\s*;',
    re.MULTILINE,
)

_FUNC_DEF_RE = re.compile(
    r'^[ \t]*((?:(?:static|inline|extern|const|volatile|unsigned|signed|'
    r'long|short|struct|enum)\s+)*[\w*]+(?:\s*\*)*)\s+(\w+)\s*\(([^)]*)\)\s*\{',
    re.MULTILINE,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Data classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EnumInfo:
    name: str
    members: List[Tuple[str, Optional[int]]]  # (member_name, numeric_value)
    max_member: Optional[str] = None           # Member with highest value
    max_value: Optional[int] = None
    has_max_sentinel: bool = False              # Has *_MAX, *_COUNT, NUM_* member
    file_path: str = ""

@dataclass
class StructInfo:
    name: str
    kind: str = "struct"  # "struct" or "union"
    fields: List[Tuple[str, str]] = field(default_factory=list)  # (type, name)
    is_hw_struct: bool = False
    file_path: str = ""

@dataclass
class MacroInfo:
    name: str
    value: str
    is_numeric: bool = False
    numeric_value: Optional[int] = None
    is_bitmask: bool = False
    is_size_limit: bool = False
    file_path: str = ""

@dataclass
class FunctionInfo:
    name: str
    return_type: str
    params: str
    is_validator: bool = False
    is_static: bool = False
    file_path: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
#  CodebaseSymbolExtractor
# ═══════════════════════════════════════════════════════════════════════════════

class CodebaseSymbolExtractor:
    """Walks the codebase and extracts all symbols from C/C++ files."""

    def __init__(
        self,
        codebase_path: str,
        exclude_dirs: Optional[List[str]] = None,
        exclude_globs: Optional[List[str]] = None,
    ):
        self.codebase_path = Path(codebase_path).resolve()
        self.exclude_dirs = _DEFAULT_EXCLUDE_DIRS | set(exclude_dirs or [])
        self.exclude_globs = exclude_globs or []

        self.enums: List[EnumInfo] = []
        self.structs: List[StructInfo] = []
        self.macros: List[MacroInfo] = []
        self.functions: List[FunctionInfo] = []

    # ── File Discovery ────────────────────────────────────────────────────

    def _discover_files(self) -> List[Path]:
        """Walk codebase and collect C/C++ files (respects exclusions)."""
        files = []
        for root, dirs, filenames in os.walk(self.codebase_path):
            # Prune excluded directories (in-place)
            dirs[:] = [
                d for d in dirs
                if d not in self.exclude_dirs and not d.startswith(".")
            ]
            for fname in filenames:
                fpath = Path(root) / fname
                if fpath.suffix.lower() not in _C_CPP_EXTS:
                    continue
                # Check glob exclusions
                if self.exclude_globs:
                    try:
                        rel = fpath.relative_to(self.codebase_path).as_posix().lower()
                        if any(fnmatch.fnmatch(rel, g.lower()) for g in self.exclude_globs):
                            continue
                    except ValueError:
                        pass
                files.append(fpath)
        logger.info(f"Discovered {len(files)} C/C++ files in {self.codebase_path}")
        return files

    # ── Parsing ───────────────────────────────────────────────────────────

    def _parse_enums(self, content: str, file_path: str) -> None:
        """Extract enum definitions."""
        for match in _ENUM_TYPEDEF_RE.finditer(content):
            _tag, body, alias = match.groups()
            self._process_enum(alias or _tag or "", body.strip(), file_path)
        for match in _ENUM_PLAIN_RE.finditer(content):
            name, body = match.groups()
            # Avoid duplicates from typedef enums
            if not any(e.name == name for e in self.enums):
                self._process_enum(name, body.strip(), file_path)

    def _process_enum(self, name: str, body: str, file_path: str) -> None:
        """Parse enum body into members with values."""
        if not name:
            return
        members = []
        auto_val = 0
        for line in body.split(","):
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("/*"):
                continue
            # Remove inline comments
            line = re.sub(r'//.*$', '', line).strip()
            line = re.sub(r'/\*.*?\*/', '', line).strip()
            if not line:
                continue
            if "=" in line:
                parts = line.split("=", 1)
                mname = parts[0].strip()
                val_str = parts[1].strip().rstrip(",").strip()
                num = self._try_parse_int(val_str)
                members.append((mname, num))
                if num is not None:
                    auto_val = num + 1
            else:
                mname = line.rstrip(",").strip()
                if mname:
                    members.append((mname, auto_val))
                    auto_val += 1

        if not members:
            return

        # Identify MAX/COUNT sentinel
        has_sentinel = False
        max_member = None
        max_value = None
        sentinel_patterns = ("_MAX", "_COUNT", "_NUM", "_LAST", "_END", "NUM_")
        for mname, mval in members:
            upper = mname.upper()
            if any(upper.endswith(s) or upper.startswith(s) for s in sentinel_patterns):
                has_sentinel = True
                max_member = mname
                max_value = mval
                break

        # If no sentinel, use last member
        if max_value is None and members:
            max_member = members[-1][0]
            max_value = members[-1][1]

        self.enums.append(EnumInfo(
            name=name,
            members=members,
            max_member=max_member,
            max_value=max_value,
            has_max_sentinel=has_sentinel,
            file_path=file_path,
        ))

    def _parse_structs(self, content: str, file_path: str) -> None:
        """Extract struct/union definitions."""
        for pattern in (_STRUCT_TYPEDEF_RE, _STRUCT_PLAIN_RE):
            for match in pattern.finditer(content):
                kind = match.group(1)        # struct or union
                name = match.group(2) or ""
                # Find matching close brace
                start = match.end()
                depth = 1
                pos = start
                while pos < len(content) and depth > 0:
                    if content[pos] == '{':
                        depth += 1
                    elif content[pos] == '}':
                        depth -= 1
                    pos += 1
                body = content[start:pos - 1]

                # For typedef struct, get alias after closing brace
                if pattern == _STRUCT_TYPEDEF_RE:
                    alias_match = re.match(r'\s*(\w+)\s*;', content[pos - 1:pos + 50])
                    if alias_match:
                        name = alias_match.group(1) or name

                if not name or any(s.name == name for s in self.structs):
                    continue

                # Parse fields
                fields = self._parse_struct_fields(body)

                # Detect hardware struct
                name_lower = name.lower()
                is_hw = any(hint in name_lower for hint in _HW_STRUCT_HINTS)

                self.structs.append(StructInfo(
                    name=name, kind=kind, fields=fields,
                    is_hw_struct=is_hw, file_path=file_path,
                ))

    def _parse_struct_fields(self, body: str) -> List[Tuple[str, str]]:
        """Extract (type, name) pairs from struct body."""
        fields = []
        field_re = re.compile(
            r'^\s*((?:(?:const|volatile|unsigned|signed|long|short|struct|union|enum)\s+)*'
            r'[\w*]+(?:\s*\*)*)\s+(\w+)\s*(?:\[[^\]]*\])?\s*;',
            re.MULTILINE,
        )
        for m in field_re.finditer(body):
            ftype = m.group(1).strip()
            fname = m.group(2).strip()
            if ftype and fname:
                fields.append((ftype, fname))
        return fields

    def _parse_macros(self, content: str, file_path: str) -> None:
        """Extract #define macros and categorize them."""
        for match in _MACRO_RE.finditer(content):
            name = match.group(1)
            value = match.group(2).strip()

            # Skip include guards and empty macros
            if _INCLUDE_GUARD_RE.match(name) or not value:
                continue

            # Skip multi-line continuation (captured partially)
            if value.endswith("\\"):
                continue

            # Remove inline comments from value
            value = re.sub(r'//.*$', '', value).strip()
            value = re.sub(r'/\*.*?\*/', '', value).strip()
            if not value:
                continue

            # Avoid duplicates
            if any(m.name == name for m in self.macros):
                continue

            num = self._try_parse_int(value)
            is_numeric = num is not None

            # Classify
            is_bitmask = False
            is_size_limit = False
            upper = name.upper()

            if is_numeric and num is not None:
                # Bitmask: power of 2 or hex with specific patterns
                if num > 0 and (num & (num - 1)) == 0:
                    is_bitmask = True
                if "_MASK" in upper or "_SHIFT" in upper or "_BIT" in upper:
                    is_bitmask = True
                # Size/limit
                if any(k in upper for k in ("MAX", "MIN", "SIZE", "LEN", "COUNT",
                                             "LIMIT", "NUM", "CAPACITY", "DEPTH")):
                    is_size_limit = True

            self.macros.append(MacroInfo(
                name=name, value=value,
                is_numeric=is_numeric, numeric_value=num,
                is_bitmask=is_bitmask, is_size_limit=is_size_limit,
                file_path=file_path,
            ))

    def _parse_functions(self, content: str, file_path: str) -> None:
        """Extract function definitions and prototypes."""
        seen = set()
        for pattern, is_def in [(_FUNC_DEF_RE, True), (_FUNC_PROTO_RE, False)]:
            for match in pattern.finditer(content):
                rtype = match.group(1).strip()
                fname = match.group(2).strip()
                params = match.group(3).strip()
                if fname in seen:
                    continue
                seen.add(fname)

                is_static = "static" in rtype
                is_validator = bool(_VALIDATOR_RE.match(fname))

                self.functions.append(FunctionInfo(
                    name=fname, return_type=rtype, params=params,
                    is_validator=is_validator, is_static=is_static,
                    file_path=file_path,
                ))

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _try_parse_int(val_str: str) -> Optional[int]:
        """Attempt to parse a C integer literal."""
        val_str = val_str.strip().rstrip("UuLl")
        try:
            if val_str.startswith("0x") or val_str.startswith("0X"):
                return int(val_str, 16)
            if val_str.startswith("0b") or val_str.startswith("0B"):
                return int(val_str, 2)
            if val_str.startswith("0") and len(val_str) > 1 and val_str[1:].isdigit():
                return int(val_str, 8)
            return int(val_str)
        except (ValueError, OverflowError):
            return None

    # ── Main entry ────────────────────────────────────────────────────────

    def extract_all(self) -> Dict[str, int]:
        """Scan codebase and populate symbol registries. Returns summary counts."""
        files = self._discover_files()
        for fpath in files:
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug(f"Failed to read {fpath}: {e}")
                continue

            rel = str(fpath.relative_to(self.codebase_path))
            self._parse_enums(content, rel)
            self._parse_structs(content, rel)
            self._parse_macros(content, rel)
            self._parse_functions(content, rel)

        logger.info(
            f"Extracted: {len(self.enums)} enums, {len(self.structs)} structs, "
            f"{len(self.macros)} macros, {len(self.functions)} functions"
        )
        return {
            "enums": len(self.enums),
            "structs": len(self.structs),
            "macros": len(self.macros),
            "functions": len(self.functions),
            "files_scanned": len(files),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  ConstraintRuleGenerator
# ═══════════════════════════════════════════════════════════════════════════════

class ConstraintRuleGenerator:
    """Generates markdown constraint rules from extracted symbols."""

    def __init__(self, extractor: CodebaseSymbolExtractor):
        self.ext = extractor

    def generate(self) -> str:
        """Generate complete constraint markdown file."""
        sections = []
        sections.append(self._header())
        sections.append("## 1. Issue Identification Rules\n")
        sections.append(self._enum_rules())
        sections.append(self._hw_struct_rules())
        sections.append(self._macro_limit_rules())
        sections.append(self._bitmask_rules())
        sections.append(self._validator_function_rules())
        sections.append(self._chained_deref_rules())
        sections.append("\n---\n")
        sections.append("## 2. Issue Resolution Rules\n")
        sections.append(self._resolution_rules())
        sections.append("\n---\n")
        sections.append("## 3. Symbol Reference\n")
        sections.append(self._symbol_reference())
        return "\n".join(s for s in sections if s)

    # ── Header ────────────────────────────────────────────────────────────

    def _header(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return (
            f"# Auto-Generated Codebase Constraints\n\n"
            f"<!-- Generated by codebase_constraint_generator.py -->\n"
            f"<!-- Source: {self.ext.codebase_path} | Date: {now} -->\n"
            f"<!-- Enums: {len(self.ext.enums)} | Structs: {len(self.ext.structs)} "
            f"| Macros: {len(self.ext.macros)} | Functions: {len(self.ext.functions)} -->\n\n"
        )

    # ── Enum-Based Bounds Rules ───────────────────────────────────────────

    def _enum_rules(self) -> str:
        sentinel_enums = [e for e in self.ext.enums if e.has_max_sentinel]
        if not sentinel_enums:
            return ""
        lines = ["### A. Enum-Bounded Array Access\n"]
        lines.append(
            "*The following enums define sentinel/MAX members that bound array sizes. "
            "When an array is sized to the MAX member and indexed by this enum type, "
            "bounds checks are guaranteed by the compiler.*\n"
        )
        for e in sentinel_enums[:50]:  # Cap at 50
            members_str = ", ".join(
                f"`{m}={v}`" if v is not None else f"`{m}`"
                for m, v in e.members[:8]
            )
            if len(e.members) > 8:
                members_str += f", ... ({len(e.members)} total)"
            lines.append(
                f"*   **Target**: `{e.name}` — sentinel: `{e.max_member}` = {e.max_value}\n"
                f"    Members: {members_str}\n"
                f"    **Rule**: **IGNORE** array bounds warnings when array is sized to "
                f"`{e.max_member}` and indexed by `{e.name}` values.\n"
            )
        return "\n".join(lines) + "\n"

    # ── Hardware Struct Rules ─────────────────────────────────────────────

    def _hw_struct_rules(self) -> str:
        hw = [s for s in self.ext.structs if s.is_hw_struct]
        if not hw:
            return ""
        lines = ["### B. Hardware-Initialized Structures\n"]
        lines.append(
            "*These structures are identified as hardware/driver context types "
            "that are allocated at initialization and are never NULL in data paths.*\n"
        )
        for s in hw[:40]:
            field_names = ", ".join(f"`{fn}`" for _, fn in s.fields[:6])
            if len(s.fields) > 6:
                field_names += f", ... ({len(s.fields)} fields)"
            lines.append(
                f"*   **Target**: `{s.kind} {s.name}` — fields: {field_names}\n"
                f"    **Rule**: **IGNORE** NULL pointer dereference warnings for "
                f"pointers of type `{s.name}` in data paths (TX/RX/ISR).\n"
                f"    **Exception**: FLAG if the pointer is the result of a "
                f"local `malloc`/`kzalloc`/`devm_kzalloc` in the current scope.\n"
            )
        return "\n".join(lines) + "\n"

    # ── Macro Limit Rules ─────────────────────────────────────────────────

    def _macro_limit_rules(self) -> str:
        limits = [m for m in self.ext.macros if m.is_size_limit and m.is_numeric]
        if not limits:
            return ""
        lines = ["### C. Macro-Defined Size Limits\n"]
        lines.append(
            "*These macros define compile-time size/limit constants. "
            "When a variable is compared against these macros, bounds are guaranteed.*\n"
        )
        for m in limits[:60]:
            lines.append(
                f"*   **Target**: `{m.name}` = `{m.value}` (numeric: {m.numeric_value})\n"
                f"    **Rule**: **IGNORE** array bounds / buffer overflow warnings where "
                f"the index or size is validated against `{m.name}`.\n"
            )
        return "\n".join(lines) + "\n"

    # ── Bitmask Rules ─────────────────────────────────────────────────────

    def _bitmask_rules(self) -> str:
        masks = [m for m in self.ext.macros if m.is_bitmask and m.is_numeric]
        if not masks:
            return ""
        lines = ["### D. Bit Field & Mask Operations\n"]
        lines.append(
            "*These macros define bit masks and shift values. Bit operations "
            "using these constants are intentional hardware register access patterns, "
            "NOT array indices.*\n"
        )
        # Group by prefix for compactness
        mask_names = [m.name for m in masks[:40]]
        lines.append(
            f"*   **Target**: {', '.join(f'`{n}`' for n in mask_names[:15])}"
        )
        if len(mask_names) > 15:
            lines.append(f"    ... and {len(mask_names) - 15} more bitmask macros")
        lines.append(
            f"\n    **Rule**: **IGNORE** \"suspicious bit manipulation\", "
            f"\"shift overflow\", and \"potential data loss\" warnings when "
            f"these macros are used in bitwise operations (`&`, `|`, `<<`, `>>`).\n"
            f"    **Rule**: **IGNORE** array bounds warnings when these values "
            f"are used as bit flags, NOT as array indices.\n"
        )
        return "\n".join(lines) + "\n"

    # ── Validator Function Rules ──────────────────────────────────────────

    def _validator_function_rules(self) -> str:
        validators = [f for f in self.ext.functions if f.is_validator]
        if not validators:
            return ""
        lines = ["### E. Validation Helper Functions\n"]
        lines.append(
            "*These functions perform parameter validation. When they are called "
            "on a variable before its use, the variable is considered validated.*\n"
        )
        for f in validators[:30]:
            lines.append(
                f"*   **Target**: `{f.return_type} {f.name}({f.params[:60]})`\n"
                f"    **Rule**: **IGNORE** missing validation for parameters that "
                f"have been passed through `{f.name}()` upstream in the call chain.\n"
            )
        return "\n".join(lines) + "\n"

    # ── Chained Dereference Rules ─────────────────────────────────────────

    def _chained_deref_rules(self) -> str:
        hw = [s for s in self.ext.structs if s.is_hw_struct]
        if not hw:
            return ""
        lines = ["### F. Chained Dereference Patterns\n"]
        lines.append(
            "*When a root pointer has been validated (either by a null check or "
            "by being a hardware-initialized structure), all intermediate pointers "
            "in a dereference chain inherit that validation.*\n"
        )
        # Build example chains from struct fields that are pointers
        chains = []
        for s in hw[:10]:
            ptr_fields = [
                fn for ft, fn in s.fields
                if "*" in ft or any(h in ft.lower() for h in ("ctx", "handle", "ops"))
            ]
            if ptr_fields:
                for pf in ptr_fields[:3]:
                    chains.append(f"`{s.name.lower()}->{pf}`")
        if chains:
            lines.append(
                f"*   **Target**: Chains such as {', '.join(chains[:6])}\n"
                f"    **Rule**: **IGNORE** NULL checks on intermediate pointers "
                f"if the root pointer has been validated.\n"
                f"    **Reasoning**: Embedded drivers validate once at the public API "
                f"boundary. Redundant checks in inner functions add latency "
                f"(forbidden in real-time TX/RX/ISR paths).\n"
            )
        return "\n".join(lines) + "\n"

    # ── Resolution Rules ──────────────────────────────────────────────────

    def _resolution_rules(self) -> str:
        lines = ["### A. General Resolution Guidelines\n"]
        lines.append(
            "*   **Rule**: **DO NOT** add redundant NULL checks for hardware-initialized "
            "pointers (see Section 1B above).\n"
            "*   **Rule**: **DO NOT** introduce locking (mutex/spinlocks) in ISR or "
            "real-time data path functions without explicit user request.\n"
            "*   **Rule**: **PREFER** `goto cleanup` pattern for resource cleanup "
            "on error paths.\n"
            "*   **Rule**: **RETAIN** existing `memcpy`/`memset` calls. Do not replace "
            "with secure variants (`memcpy_s`, `memset_s`) unless specifically requested. "
            "Instead, add explicit bounds checks before the call.\n"
        )
        return "\n".join(lines) + "\n"

    # ── Symbol Reference ──────────────────────────────────────────────────

    def _symbol_reference(self) -> str:
        lines = []

        if self.ext.enums:
            lines.append("### Enums\n")
            for e in self.ext.enums[:80]:
                members_brief = ", ".join(
                    f"{m}={v}" if v is not None else m
                    for m, v in e.members[:5]
                )
                if len(e.members) > 5:
                    members_brief += f", ... ({len(e.members)} members)"
                sentinel = f" [sentinel: {e.max_member}]" if e.has_max_sentinel else ""
                lines.append(f"- `{e.name}`: {members_brief}{sentinel}")
            lines.append("")

        if self.ext.structs:
            lines.append("### Structs\n")
            for s in self.ext.structs[:80]:
                fields_brief = ", ".join(f"{fn}" for _, fn in s.fields[:5])
                if len(s.fields) > 5:
                    fields_brief += f", ... ({len(s.fields)} fields)"
                hw_tag = " **[HW]**" if s.is_hw_struct else ""
                lines.append(f"- `{s.kind} {s.name}`: {fields_brief}{hw_tag}")
            lines.append("")

        if self.ext.macros:
            # Only show size/limit and bitmask macros in reference
            notable = [m for m in self.ext.macros if m.is_size_limit or m.is_bitmask]
            if notable:
                lines.append("### Notable Macros (Limits & Bitmasks)\n")
                for m in notable[:80]:
                    tag = "[LIMIT]" if m.is_size_limit else "[MASK]"
                    lines.append(f"- `{m.name}` = `{m.value}` {tag}")
                lines.append("")

        if self.ext.functions:
            validators = [f for f in self.ext.functions if f.is_validator]
            if validators:
                lines.append("### Validator Functions\n")
                for f in validators[:40]:
                    lines.append(f"- `{f.return_type} {f.name}({f.params[:50]})`")
                lines.append("")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════════

def generate_constraints(
    codebase_path: str,
    exclude_dirs: Optional[List[str]] = None,
    exclude_globs: Optional[List[str]] = None,
) -> str:
    """
    Scan a codebase and generate constraint markdown text.

    Returns the full markdown string (caller is responsible for writing to file).
    """
    extractor = CodebaseSymbolExtractor(
        codebase_path, exclude_dirs=exclude_dirs, exclude_globs=exclude_globs,
    )
    extractor.extract_all()
    generator = ConstraintRuleGenerator(extractor)
    return generator.generate()


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Auto-generate codebase constraint rules from C/C++ symbols."
    )
    parser.add_argument(
        "--codebase-path", required=True,
        help="Root path of the C/C++ codebase to scan.",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output .md file path (default: agents/constraints/codebase_constraints.md).",
    )
    parser.add_argument(
        "--exclude-dirs", nargs="*", default=[],
        help="Additional directory names to exclude.",
    )
    parser.add_argument(
        "--exclude-globs", nargs="*", default=[],
        help="Glob patterns to exclude (e.g., '*.test.cpp').",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    md_text = generate_constraints(
        codebase_path=args.codebase_path,
        exclude_dirs=args.exclude_dirs,
        exclude_globs=args.exclude_globs,
    )

    if args.output:
        out_path = args.output
    else:
        out_path = os.path.join(
            os.path.dirname(__file__), "codebase_constraints.md"
        )

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md_text)
    print(f"Constraint file written to: {out_path}")
    print(f"  Lines: {md_text.count(chr(10)) + 1}")


if __name__ == "__main__":
    main()
