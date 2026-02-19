"""
Header Context Builder for LLM-Exclusive Code Analysis.

Resolves #include directives, parses header files for type definitions
(enums, structs, macros, typedefs, function prototypes, extern variables),
and builds chunk-specific context strings to inject into LLM prompts.

Works entirely via regex — no CCLS or external tooling required.
Caches parsed headers for the lifetime of the builder instance.
"""

import fnmatch
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EnumMember:
    name: str
    value: Optional[str] = None          # "0", "0x10", "FOO + 1", or None (auto)
    numeric_value: Optional[int] = None  # Resolved integer if deterministic


@dataclass
class EnumDef:
    name: str                            # enum tag or typedef alias
    members: List[EnumMember] = field(default_factory=list)
    is_typedef: bool = False
    raw: str = ""                        # Compact single-line repr


@dataclass
class StructField:
    type_name: str
    field_name: str
    array_size: Optional[str] = None     # e.g. "64", "MAX_BUF", None


@dataclass
class StructDef:
    name: str
    kind: str = "struct"                 # "struct" or "union"
    fields: List[StructField] = field(default_factory=list)
    is_typedef: bool = False
    raw: str = ""


@dataclass
class MacroDef:
    name: str
    value: str
    is_numeric: bool = False
    numeric_value: Optional[int] = None
    is_function_like: bool = False
    raw: str = ""


@dataclass
class TypedefDef:
    alias: str
    original_type: str
    raw: str = ""


@dataclass
class FuncProto:
    name: str
    return_type: str
    params: str
    raw: str = ""


@dataclass
class ExternVar:
    name: str
    type_name: str
    raw: str = ""


@dataclass
class HeaderDefinitions:
    enums: List[EnumDef] = field(default_factory=list)
    structs: List[StructDef] = field(default_factory=list)
    macros: List[MacroDef] = field(default_factory=list)
    typedefs: List[TypedefDef] = field(default_factory=list)
    function_protos: List[FuncProto] = field(default_factory=list)
    extern_vars: List[ExternVar] = field(default_factory=list)
    file_path: str = ""


@dataclass
class ResolvedInclude:
    name: str                    # As written in the #include directive
    abs_path: Optional[str]      # Resolved absolute path (None if unresolved)
    include_type: str            # "local" or "system"
    resolved: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# Well-known system headers (skip for context injection)
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_HEADERS: Set[str] = {
    # C standard
    "assert.h", "complex.h", "ctype.h", "errno.h", "fenv.h", "float.h",
    "inttypes.h", "iso646.h", "limits.h", "locale.h", "math.h", "setjmp.h",
    "signal.h", "stdalign.h", "stdarg.h", "stdatomic.h", "stdbool.h",
    "stddef.h", "stdint.h", "stdio.h", "stdlib.h", "stdnoreturn.h",
    "string.h", "tgmath.h", "threads.h", "time.h", "uchar.h", "wchar.h",
    "wctype.h",
    # POSIX
    "unistd.h", "fcntl.h", "sys/types.h", "sys/stat.h", "sys/socket.h",
    "sys/ioctl.h", "sys/mman.h", "sys/wait.h", "netinet/in.h",
    "arpa/inet.h", "pthread.h", "dlfcn.h", "dirent.h", "poll.h",
    "semaphore.h", "syslog.h",
    # C++ standard
    "algorithm", "any", "array", "atomic", "bitset", "cassert", "cctype",
    "cerrno", "cfloat", "chrono", "cinttypes", "climits", "clocale",
    "cmath", "compare", "complex", "concepts", "condition_variable",
    "coroutine", "csetjmp", "csignal", "cstdarg", "cstddef", "cstdint",
    "cstdio", "cstdlib", "cstring", "ctime", "cuchar", "cwchar",
    "cwctype", "deque", "exception", "execution", "filesystem", "format",
    "forward_list", "fstream", "functional", "future", "initializer_list",
    "iomanip", "ios", "iosfwd", "iostream", "istream", "iterator",
    "latch", "limits", "list", "locale", "map", "memory", "memory_resource",
    "mutex", "new", "numeric", "optional", "ostream", "queue", "random",
    "ranges", "ratio", "regex", "scoped_allocator", "semaphore", "set",
    "shared_mutex", "source_location", "span", "sstream", "stack",
    "stdexcept", "stop_token", "streambuf", "string", "string_view",
    "syncstream", "system_error", "thread", "tuple", "type_traits",
    "typeindex", "typeinfo", "unordered_map", "unordered_set", "utility",
    "valarray", "variant", "vector", "version",
    # Linux kernel
    "linux/module.h", "linux/kernel.h", "linux/init.h", "linux/types.h",
    "linux/slab.h", "linux/fs.h", "linux/errno.h", "linux/list.h",
    "linux/spinlock.h", "linux/mutex.h", "linux/skbuff.h", "linux/netdevice.h",
}

# Include guard patterns (these macros should be excluded from context)
_INCLUDE_GUARD_RE = re.compile(
    r"^_+[A-Z][A-Z0-9_]*_H_*$|^[A-Z][A-Z0-9_]*_H(?:EADER)?_*$|^__[A-Z][A-Z0-9_]*_H__$"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Regex patterns for C/C++ definition extraction
# ═══════════════════════════════════════════════════════════════════════════════

# Include directive
_INCLUDE_RE = re.compile(
    r'^\s*#\s*include\s+([<"])([^">]+)[>"]', re.MULTILINE
)

# Enum: both `enum name { ... }` and `typedef enum { ... } name;`
_ENUM_TYPEDEF_RE = re.compile(
    r'typedef\s+enum\s+(\w+)?\s*\{([^}]*)\}\s*(\w+)\s*;', re.DOTALL
)
_ENUM_PLAIN_RE = re.compile(
    r'(?<!typedef\s)enum\s+(\w+)\s*\{([^}]*)\}', re.DOTALL
)

# Struct/union: both plain and typedef
_STRUCT_TYPEDEF_RE = re.compile(
    r'typedef\s+(struct|union)\s+(\w+)?\s*\{', re.DOTALL
)
_STRUCT_PLAIN_RE = re.compile(
    r'(?<!typedef\s)(struct|union)\s+(\w+)\s*\{', re.DOTALL
)

# Macro
_MACRO_RE = re.compile(
    r'^\s*#\s*define\s+(\w+)(?:\(([^)]*)\))?\s*(.*?)$', re.MULTILINE
)
_MACRO_CONTINUATION_RE = re.compile(r'\\\s*$')

# Typedef (non-struct/enum)
_TYPEDEF_SIMPLE_RE = re.compile(
    r'typedef\s+(?!enum\b)(?!struct\b)(?!union\b)([\w\s*]+?)\s+(\*?\w+)\s*(?:\[[\w\s]*\])?\s*;'
)

# Function prototype (declaration without body)
_FUNC_PROTO_RE = re.compile(
    r'^[ \t]*((?:(?:static|inline|extern|const|volatile|unsigned|signed|long|short|struct|enum)\s+)*'
    r'[\w*]+(?:\s*\*)*)\s+'   # return type
    r'(\w+)\s*'               # function name
    r'\(([^)]*)\)\s*;',       # parameters
    re.MULTILINE
)

# Extern variable
_EXTERN_VAR_RE = re.compile(
    r'extern\s+([\w\s*]+?)\s+(\w+)\s*(?:\[[\w\s]*\])?\s*;'
)

# Numeric literal
_NUMERIC_RE = re.compile(
    r'^[+-]?\s*(?:0[xX][0-9a-fA-F]+[uUlL]*|0[bB][01]+[uUlL]*|[0-9]+[uUlL]*)$'
)

# Simple arithmetic expression (for macro evaluation)
_SIMPLE_EXPR_RE = re.compile(
    r'^[\d\s+\-*/()xXa-fA-FuUlL]+$'
)

# Identifier extraction
_IDENT_RE = re.compile(r'\b[A-Za-z_]\w*\b')

# C keywords to exclude from identifier matching
_C_KEYWORDS: Set[str] = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "inline", "int", "long", "register", "restrict", "return", "short",
    "signed", "sizeof", "static", "struct", "switch", "typedef", "union",
    "unsigned", "void", "volatile", "while",
    # C++ additions
    "alignas", "alignof", "and", "and_eq", "asm", "bitand", "bitor",
    "bool", "catch", "char8_t", "char16_t", "char32_t", "class",
    "compl", "concept", "consteval", "constexpr", "constinit",
    "co_await", "co_return", "co_yield", "decltype", "delete", "dynamic_cast",
    "explicit", "export", "false", "friend", "mutable", "namespace", "new",
    "noexcept", "not", "not_eq", "nullptr", "operator", "or", "or_eq",
    "private", "protected", "public", "reinterpret_cast", "requires",
    "static_assert", "static_cast", "template", "this", "throw", "true",
    "try", "typeid", "typename", "using", "virtual", "wchar_t", "xor", "xor_eq",
}


# ═══════════════════════════════════════════════════════════════════════════════
# HeaderContextBuilder
# ═══════════════════════════════════════════════════════════════════════════════

class HeaderContextBuilder:
    """
    Resolves ``#include`` directives and extracts C/C++ type definitions
    from header files.  Works entirely via regex — no CCLS required.

    Usage::

        builder = HeaderContextBuilder("/path/to/codebase")
        includes = builder.resolve_includes("/path/to/codebase/src/main.c")
        context  = builder.build_context_for_chunk(chunk_text, includes)
    """

    # Default directory names to skip during recursive header search
    _DEFAULT_WALK_EXCLUDE = {
        ".git", "build", "dist", "third_party", "external",
        "vendor", ".ccls-cache", "__pycache__", "bin", "obj",
    }

    def __init__(
        self,
        codebase_path: str,
        include_paths: Optional[List[str]] = None,
        max_header_depth: int = 2,
        max_context_chars: int = 6000,
        exclude_system_headers: bool = True,
        max_definitions_per_header: int = 500,
        exclude_dirs: Optional[List[str]] = None,
        exclude_globs: Optional[List[str]] = None,
    ):
        self.codebase_path = Path(codebase_path).resolve()
        self.include_paths: List[Path] = []
        for p in (include_paths or []):
            ip = Path(p)
            if not ip.is_absolute():
                ip = self.codebase_path / ip
            self.include_paths.append(ip.resolve())
        self.max_header_depth = max_header_depth
        self.max_context_chars = max_context_chars
        self.exclude_system_headers = exclude_system_headers
        self.max_definitions_per_header = max_definitions_per_header

        # Merge caller-supplied exclusions with defaults
        self.exclude_dirs = self._DEFAULT_WALK_EXCLUDE | set(exclude_dirs or [])
        self.exclude_globs = exclude_globs or []

        # Caches (persist for the lifetime of this builder instance)
        self._include_cache: Dict[str, List[ResolvedInclude]] = {}
        self._header_cache: Dict[str, HeaderDefinitions] = {}

    # ─── Include Resolution ──────────────────────────────────────────────

    def resolve_includes(
        self,
        file_path: str,
        _depth: int = 0,
        _visited: Optional[Set[str]] = None,
    ) -> List[ResolvedInclude]:
        """
        Resolve ``#include`` directives from *file_path* (and recursively
        from the resolved headers up to *max_header_depth*).

        Returns a flat, deduplicated list of :class:`ResolvedInclude`.
        """
        abs_path = str(Path(file_path).resolve())

        if abs_path in self._include_cache:
            return self._include_cache[abs_path]

        if _visited is None:
            _visited = set()

        if abs_path in _visited:
            return []  # circular include guard
        _visited.add(abs_path)

        result: List[ResolvedInclude] = []
        seen_paths: Set[str] = set()

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except (OSError, IOError) as exc:
            logger.debug("Cannot read %s for include resolution: %s", abs_path, exc)
            return []

        file_dir = Path(abs_path).parent

        for match in _INCLUDE_RE.finditer(content):
            delim = match.group(1)  # '"' or '<'
            inc_name = match.group(2)
            inc_type = "local" if delim == '"' else "system"

            # Skip system headers if configured
            if inc_type == "system" and self.exclude_system_headers:
                # Check against known system headers
                basename = os.path.basename(inc_name)
                if inc_name in _SYSTEM_HEADERS or basename in _SYSTEM_HEADERS:
                    continue

            resolved_path = self._resolve_include_path(inc_name, inc_type, file_dir)

            ri = ResolvedInclude(
                name=inc_name,
                abs_path=resolved_path,
                include_type=inc_type,
                resolved=resolved_path is not None,
            )

            if resolved_path and resolved_path not in seen_paths:
                seen_paths.add(resolved_path)
                result.append(ri)
                logger.debug(
                    "  Resolved include: %s (%s) → %s", inc_name, inc_type, resolved_path
                )

                # Recurse into the resolved header
                if _depth < self.max_header_depth:
                    sub_includes = self.resolve_includes(
                        resolved_path, _depth=_depth + 1, _visited=_visited
                    )
                    for si in sub_includes:
                        if si.abs_path and si.abs_path not in seen_paths:
                            seen_paths.add(si.abs_path)
                            result.append(si)
            elif not resolved_path:
                # Unresolved — log diagnostic info to help debug
                logger.debug(
                    "  Unresolved include: %s (%s) in %s "
                    "(codebase_path=%s, file_dir=%s, include_paths=%s)",
                    inc_name, inc_type, abs_path,
                    self.codebase_path, file_dir,
                    [str(p) for p in self.include_paths],
                )

        self._include_cache[abs_path] = result
        return result

    def _resolve_include_path(
        self, inc_name: str, inc_type: str, file_dir: Path
    ) -> Optional[str]:
        """Try to find the actual file for an include directive.

        For both local ("") and system (<>) includes, we search the
        codebase root and common include subdirectories.  Many embedded
        C projects use angle brackets for project-local headers, so we
        must not restrict system-style includes to only ``include_paths``.
        """
        search_dirs: List[Path] = []

        if inc_type == "local":
            # Local includes: same dir → include_paths → codebase root
            search_dirs = [file_dir] + self.include_paths + [self.codebase_path]
        else:
            # System includes: include_paths → codebase root → file dir
            # (many projects use <> for project headers)
            search_dirs = list(self.include_paths) + [self.codebase_path, file_dir]

        # Also add common project include subdirectories
        for subdir in ("include", "inc", "src", "common", "api", "hdr"):
            candidate_dir = self.codebase_path / subdir
            if candidate_dir.is_dir() and candidate_dir not in search_dirs:
                search_dirs.append(candidate_dir)

        for search_dir in search_dirs:
            candidate = search_dir / inc_name
            if candidate.is_file():
                return str(candidate.resolve())

        # Fallback: recursive search under codebase_path for the basename
        # (handles cases like #include "subdir/header.h" when cwd is wrong,
        #  or headers in deeply nested project directories)
        basename = os.path.basename(inc_name)
        for root, _dirs, files in os.walk(self.codebase_path):
            # Apply directory-name exclusions
            _dirs[:] = [d for d in _dirs if d not in self.exclude_dirs]

            if basename in files:
                candidate = Path(root) / basename
                # Apply glob exclusions on the candidate
                if self.exclude_globs:
                    try:
                        rel = candidate.relative_to(self.codebase_path).as_posix().lower()
                        if any(fnmatch.fnmatch(rel, g.lower()) for g in self.exclude_globs):
                            continue
                    except ValueError:
                        pass
                # Verify the relative path matches if inc_name has directories
                if "/" in inc_name or "\\" in inc_name:
                    try:
                        candidate_rel = candidate.relative_to(self.codebase_path).as_posix()
                        if candidate_rel.endswith(inc_name.replace("\\", "/")):
                            return str(candidate.resolve())
                    except ValueError:
                        pass
                else:
                    return str(candidate.resolve())

        return None

    # ─── Header Parsing ──────────────────────────────────────────────────

    def parse_header(self, file_path: str) -> HeaderDefinitions:
        """
        Parse a header file and extract enum, struct, macro, typedef,
        function prototype, and extern variable definitions.

        Results are cached by absolute path.
        """
        abs_path = str(Path(file_path).resolve())

        if abs_path in self._header_cache:
            return self._header_cache[abs_path]

        defs = HeaderDefinitions(file_path=abs_path)

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except (OSError, IOError) as exc:
            logger.debug("Cannot read header %s: %s", abs_path, exc)
            self._header_cache[abs_path] = defs
            return defs

        # Strip single-line comments for cleaner parsing
        # (preserve line structure for multi-line constructs)
        cleaned = re.sub(r'//[^\n]*', '', content)
        # Strip block comments
        cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)

        self._parse_enums(cleaned, defs)
        self._parse_structs(cleaned, defs)
        self._parse_macros(content, defs)  # Use original for macros (# directives)
        self._parse_typedefs(cleaned, defs)
        self._parse_function_protos(cleaned, defs)
        self._parse_extern_vars(cleaned, defs)

        # Enforce per-header definition limit
        total = (len(defs.enums) + len(defs.structs) + len(defs.macros)
                 + len(defs.typedefs) + len(defs.function_protos) + len(defs.extern_vars))
        if total > self.max_definitions_per_header:
            logger.debug("Header %s has %d definitions, truncating to %d",
                         abs_path, total, self.max_definitions_per_header)

        self._header_cache[abs_path] = defs
        return defs

    def _parse_enums(self, content: str, defs: HeaderDefinitions) -> None:
        """Extract enum definitions (both plain and typedef'd)."""
        # typedef enum [tag] { ... } alias;
        for m in _ENUM_TYPEDEF_RE.finditer(content):
            tag = m.group(1) or ""
            body = m.group(2)
            alias = m.group(3)
            name = alias or tag or "_anon_enum"
            members = self._parse_enum_members(body)
            raw = self._format_enum(name, members)
            defs.enums.append(EnumDef(
                name=name, members=members, is_typedef=True, raw=raw
            ))

        # Plain enum name { ... }
        for m in _ENUM_PLAIN_RE.finditer(content):
            name = m.group(1)
            body = m.group(2)
            # Skip if already captured by typedef variant
            if any(e.name == name for e in defs.enums):
                continue
            members = self._parse_enum_members(body)
            raw = self._format_enum(name, members)
            defs.enums.append(EnumDef(
                name=name, members=members, is_typedef=False, raw=raw
            ))

    def _parse_enum_members(self, body: str) -> List[EnumMember]:
        """Parse enum member list, tracking auto-increment values."""
        members: List[EnumMember] = []
        auto_val = 0
        for line in body.split(","):
            line = line.strip()
            if not line:
                continue
            # Remove trailing comments
            line = re.sub(r'/\*.*?\*/', '', line).strip()
            line = re.sub(r'//.*$', '', line).strip()
            if not line:
                continue

            if "=" in line:
                parts = line.split("=", 1)
                name = parts[0].strip()
                val_str = parts[1].strip()
                num_val = self._try_parse_int(val_str)
                members.append(EnumMember(name=name, value=val_str, numeric_value=num_val))
                if num_val is not None:
                    auto_val = num_val + 1
                else:
                    auto_val += 1
            else:
                name = line.strip()
                if not re.match(r'^[A-Za-z_]\w*$', name):
                    continue
                members.append(EnumMember(
                    name=name, value=None, numeric_value=auto_val
                ))
                auto_val += 1
        return members

    @staticmethod
    def _format_enum(name: str, members: List[EnumMember]) -> str:
        """Format an enum as a single-line C declaration."""
        parts = []
        for m in members:
            if m.value is not None:
                parts.append(f"{m.name} = {m.value}")
            elif m.numeric_value is not None:
                parts.append(f"{m.name} = {m.numeric_value}")
            else:
                parts.append(m.name)
        return f"enum {name} {{ {', '.join(parts)} }};"

    def _parse_structs(self, content: str, defs: HeaderDefinitions) -> None:
        """Extract struct/union definitions."""
        # typedef struct/union [tag] { ... } alias;
        for m in _STRUCT_TYPEDEF_RE.finditer(content):
            kind = m.group(1)  # struct or union
            tag = m.group(2) or ""
            body_start = m.end() - 1  # position of '{'
            body, body_end = self._match_braces(content, body_start)
            if body is None:
                continue
            # Look for alias after closing brace
            after = content[body_end:body_end + 100].strip()
            alias_m = re.match(r'(\w+)\s*;', after)
            alias = alias_m.group(1) if alias_m else ""
            name = alias or tag or f"_anon_{kind}"
            fields = self._parse_struct_fields(body)
            raw = self._format_struct(kind, name, fields)
            defs.structs.append(StructDef(
                name=name, kind=kind, fields=fields, is_typedef=True, raw=raw
            ))

        # Plain struct/union name { ... }
        for m in _STRUCT_PLAIN_RE.finditer(content):
            kind = m.group(1)
            name = m.group(2)
            if any(s.name == name for s in defs.structs):
                continue
            body_start = m.end() - 1
            body, _ = self._match_braces(content, body_start)
            if body is None:
                continue
            fields = self._parse_struct_fields(body)
            raw = self._format_struct(kind, name, fields)
            defs.structs.append(StructDef(
                name=name, kind=kind, fields=fields, is_typedef=False, raw=raw
            ))

    @staticmethod
    def _match_braces(content: str, start: int) -> Tuple[Optional[str], int]:
        """Find matching closing brace, returns (body_text, end_pos)."""
        if start >= len(content) or content[start] != '{':
            return None, start
        depth = 0
        i = start
        while i < len(content):
            ch = content[i]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return content[start + 1:i], i + 1
            elif ch == '"' or ch == "'":
                # Skip string/char literals
                quote = ch
                i += 1
                while i < len(content) and content[i] != quote:
                    if content[i] == '\\':
                        i += 1
                    i += 1
            i += 1
        return None, start

    def _parse_struct_fields(self, body: str) -> List[StructField]:
        """Parse struct body to extract field declarations."""
        fields: List[StructField] = []
        # Remove nested structs/unions/enums for simpler field parsing
        cleaned = re.sub(r'(struct|union|enum)\s*\w*\s*\{[^}]*\}\s*\w*\s*;', '', body, flags=re.DOTALL)

        for line in cleaned.split(";"):
            line = line.strip()
            if not line:
                continue
            # Remove __attribute__, __aligned, etc.
            line = re.sub(r'__\w+__\s*\([^)]*\)', '', line)
            line = re.sub(r'__\w+', '', line)
            line = line.strip()
            if not line:
                continue

            # Check for array
            arr_match = re.match(r'(.*?)\s+(\w+)\s*\[([\w\s+\-*/]*)\]$', line)
            if arr_match:
                type_name = arr_match.group(1).strip()
                field_name = arr_match.group(2).strip()
                arr_size = arr_match.group(3).strip() or None
                if type_name and field_name and re.match(r'^[A-Za-z_]', field_name):
                    fields.append(StructField(type_name, field_name, arr_size))
                continue

            # Regular field: type name;
            parts = line.rsplit(None, 1)
            if len(parts) == 2:
                type_name = parts[0].strip()
                field_name = parts[1].strip().rstrip("*")
                if re.match(r'^\*?[A-Za-z_]\w*$', field_name):
                    # Handle pointer in field name
                    if parts[1].strip().startswith("*"):
                        type_name += " *"
                        field_name = parts[1].strip().lstrip("*")
                    fields.append(StructField(type_name, field_name))
        return fields

    @staticmethod
    def _format_struct(kind: str, name: str, fields: List[StructField]) -> str:
        """Format struct/union as a compact C declaration."""
        parts = []
        for f in fields:
            if f.array_size:
                parts.append(f"{f.type_name} {f.field_name}[{f.array_size}]")
            else:
                parts.append(f"{f.type_name} {f.field_name}")
        return f"{kind} {name} {{ {'; '.join(parts)}; }};"

    def _parse_macros(self, content: str, defs: HeaderDefinitions) -> None:
        """Extract #define macros, handling multi-line continuations."""
        lines = content.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            # Collect continuation lines
            full_line = line
            while _MACRO_CONTINUATION_RE.search(full_line) and i + 1 < len(lines):
                i += 1
                full_line = full_line.rstrip().rstrip('\\') + ' ' + lines[i].strip()

            m = _MACRO_RE.match(full_line.strip())
            if m:
                name = m.group(1)
                params = m.group(2)  # None if not function-like
                value = m.group(3).strip()

                # Skip include guards
                if _INCLUDE_GUARD_RE.match(name):
                    i += 1
                    continue

                # Skip empty defines (include guards, feature flags)
                if not value:
                    i += 1
                    continue

                is_func = params is not None
                is_numeric, num_val = self._classify_macro_value(value)

                raw = f"#define {name}"
                if is_func:
                    raw += f"({params})"
                raw += f" {value}"

                defs.macros.append(MacroDef(
                    name=name, value=value, is_numeric=is_numeric,
                    numeric_value=num_val, is_function_like=is_func, raw=raw
                ))

            i += 1

    def _parse_typedefs(self, content: str, defs: HeaderDefinitions) -> None:
        """Extract simple typedefs (not enum/struct which are handled separately)."""
        for m in _TYPEDEF_SIMPLE_RE.finditer(content):
            original = m.group(1).strip()
            alias = m.group(2).strip()
            if not alias or not re.match(r'^[A-Za-z_]\w*$', alias.lstrip('*')):
                continue
            raw = f"typedef {original} {alias};"
            defs.typedefs.append(TypedefDef(alias=alias.lstrip('*'), original_type=original, raw=raw))

    def _parse_function_protos(self, content: str, defs: HeaderDefinitions) -> None:
        """Extract function prototypes (declarations without bodies)."""
        for m in _FUNC_PROTO_RE.finditer(content):
            ret_type = m.group(1).strip()
            func_name = m.group(2).strip()
            params = m.group(3).strip()

            # Skip if name is a C keyword
            if func_name in _C_KEYWORDS:
                continue
            # Skip common macro invocations that look like protos
            if ret_type.upper() == ret_type and '_' in ret_type:
                continue

            raw = f"{ret_type} {func_name}({params});"
            defs.function_protos.append(FuncProto(
                name=func_name, return_type=ret_type, params=params, raw=raw
            ))

    def _parse_extern_vars(self, content: str, defs: HeaderDefinitions) -> None:
        """Extract extern variable declarations."""
        for m in _EXTERN_VAR_RE.finditer(content):
            type_name = m.group(1).strip()
            var_name = m.group(2).strip()
            if not re.match(r'^[A-Za-z_]\w*$', var_name):
                continue
            raw = f"extern {type_name} {var_name};"
            defs.extern_vars.append(ExternVar(name=var_name, type_name=type_name, raw=raw))

    # ─── Value Classification ────────────────────────────────────────────

    @staticmethod
    def _try_parse_int(val_str: str) -> Optional[int]:
        """Try to evaluate a string as an integer constant."""
        val = val_str.strip().rstrip("uUlL")
        try:
            return int(val, 0)
        except (ValueError, TypeError):
            pass
        # Try simple arithmetic
        if _SIMPLE_EXPR_RE.match(val):
            clean = re.sub(r'[uUlL]', '', val)
            try:
                return int(eval(clean))  # nosec - only numeric expressions
            except Exception:
                pass
        return None

    def _classify_macro_value(self, value: str) -> Tuple[bool, Optional[int]]:
        """Classify a macro value as numeric or not."""
        num = self._try_parse_int(value)
        if num is not None:
            return True, num
        # Check for string literal
        if value.startswith('"') or value.startswith("'"):
            return False, None
        # Check for simple sizeof expression
        if value.startswith("sizeof"):
            return False, None
        # Check for cast expression wrapping a number
        cast_m = re.match(r'\(\s*[\w\s*]+\s*\)\s*([\dxXa-fA-F]+[uUlL]*)', value)
        if cast_m:
            num = self._try_parse_int(cast_m.group(1))
            return num is not None, num
        return False, None

    # ─── Context Building ────────────────────────────────────────────────

    def build_context_for_chunk(
        self,
        chunk_text: str,
        file_includes: List[ResolvedInclude],
    ) -> str:
        """
        Build a concise header context string containing only definitions
        that are actually referenced in *chunk_text*.

        Respects ``max_context_chars`` budget with priority ordering:
        enums > macros > structs > typedefs > function protos > externs.
        """
        if not file_includes:
            return ""

        # Extract identifiers from chunk (minus keywords)
        chunk_idents = set(_IDENT_RE.findall(chunk_text)) - _C_KEYWORDS

        if not chunk_idents:
            return ""

        # Collect all relevant definitions across all resolved headers
        relevant_enums: List[str] = []
        relevant_macros: List[str] = []
        relevant_structs: List[str] = []
        relevant_typedefs: List[str] = []
        relevant_protos: List[str] = []
        relevant_externs: List[str] = []

        for inc in file_includes:
            if not inc.resolved or not inc.abs_path:
                continue

            hdefs = self.parse_header(inc.abs_path)

            # Enums: include if enum name or any member name is referenced
            for edef in hdefs.enums:
                if edef.name in chunk_idents:
                    relevant_enums.append(edef.raw)
                    continue
                # Check if any member is referenced
                for mem in edef.members:
                    if mem.name in chunk_idents:
                        relevant_enums.append(edef.raw)
                        break

            # Macros: include if macro name is referenced
            for mdef in hdefs.macros:
                if mdef.name in chunk_idents:
                    relevant_macros.append(mdef.raw)

            # Structs: include if struct name is referenced
            for sdef in hdefs.structs:
                if sdef.name in chunk_idents:
                    relevant_structs.append(sdef.raw)

            # Typedefs: include if alias is referenced
            for tdef in hdefs.typedefs:
                if tdef.alias in chunk_idents:
                    relevant_typedefs.append(tdef.raw)

            # Function prototypes: include if function name is referenced
            for fproto in hdefs.function_protos:
                if fproto.name in chunk_idents:
                    relevant_protos.append(fproto.raw)

            # Extern variables: include if variable name is referenced
            for evar in hdefs.extern_vars:
                if evar.name in chunk_idents:
                    relevant_externs.append(evar.raw)

        # Deduplicate
        relevant_enums = list(dict.fromkeys(relevant_enums))
        relevant_macros = list(dict.fromkeys(relevant_macros))
        relevant_structs = list(dict.fromkeys(relevant_structs))
        relevant_typedefs = list(dict.fromkeys(relevant_typedefs))
        relevant_protos = list(dict.fromkeys(relevant_protos))
        relevant_externs = list(dict.fromkeys(relevant_externs))

        # Nothing relevant found
        if not any([relevant_enums, relevant_macros, relevant_structs,
                     relevant_typedefs, relevant_protos, relevant_externs]):
            return ""

        # Build context string respecting token budget (priority order)
        sections: List[Tuple[str, List[str]]] = [
            ("Enums", relevant_enums),
            ("Macros", relevant_macros),
            ("Structs", relevant_structs),
            ("Typedefs", relevant_typedefs),
            ("Function prototypes", relevant_protos),
            ("Extern variables", relevant_externs),
        ]

        header_line = "// ──── HEADER CONTEXT (from included headers) ────"
        footer_line = "// ──── END HEADER CONTEXT ────"
        budget = self.max_context_chars - len(header_line) - len(footer_line) - 20

        parts: List[str] = []
        used = 0

        for section_name, items in sections:
            if not items or used >= budget:
                break
            section_header = f"// {section_name}:"
            section_lines = [section_header]
            for item in items:
                line_len = len(item) + 1  # +1 for newline
                if used + line_len > budget:
                    break
                section_lines.append(item)
                used += line_len
            if len(section_lines) > 1:  # Has items beyond the header
                parts.append("\n".join(section_lines))
                used += len(section_header) + 2  # header + spacing

        if not parts:
            return ""

        return f"{header_line}\n" + "\n\n".join(parts) + f"\n{footer_line}"

    # ─── Convenience ─────────────────────────────────────────────────────

    def get_file_context(self, file_path: str) -> str:
        """
        Get the full header context for a file (all definitions from all
        resolved includes, not filtered by chunk content).

        Useful for small files that are a single chunk.
        """
        includes = self.resolve_includes(file_path)
        if not includes:
            return ""

        all_raws: List[str] = []
        for inc in includes:
            if not inc.resolved or not inc.abs_path:
                continue
            hdefs = self.parse_header(inc.abs_path)
            for edef in hdefs.enums:
                all_raws.append(edef.raw)
            for mdef in hdefs.macros:
                all_raws.append(mdef.raw)
            for sdef in hdefs.structs:
                all_raws.append(sdef.raw)
            for tdef in hdefs.typedefs:
                all_raws.append(tdef.raw)
            for fproto in hdefs.function_protos:
                all_raws.append(fproto.raw)
            for evar in hdefs.extern_vars:
                all_raws.append(evar.raw)

        if not all_raws:
            return ""

        # Truncate to budget
        header_line = "// ──── HEADER CONTEXT (from included headers) ────"
        footer_line = "// ──── END HEADER CONTEXT ────"
        result_lines = [header_line]
        used = len(header_line) + len(footer_line) + 10
        for raw in all_raws:
            if used + len(raw) + 1 > self.max_context_chars:
                break
            result_lines.append(raw)
            used += len(raw) + 1
        result_lines.append(footer_line)
        return "\n".join(result_lines)

    def get_cache_stats(self) -> Dict[str, Any]:
        """Return cache statistics for debugging/telemetry."""
        total_defs = 0
        for hdefs in self._header_cache.values():
            total_defs += (len(hdefs.enums) + len(hdefs.structs) + len(hdefs.macros)
                           + len(hdefs.typedefs) + len(hdefs.function_protos)
                           + len(hdefs.extern_vars))
        return {
            "headers_parsed": len(self._header_cache),
            "includes_resolved": len(self._include_cache),
            "total_definitions_cached": total_defs,
        }
