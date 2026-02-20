"""
static_call_stack_analyzer.py

CURE — Codebase Update & Refactor Engine
Static Call Stack Context Analyzer

Builds a codebase-wide function index at startup, then for each code chunk
traces every pointer/index/divisor/enum through the call chain to find where
values are set, validated, or constrained.  Injected as Context Layer 4
alongside HeaderContextBuilder (Layer 2) and ContextValidator (Layer 3).

Two modes:
  - Regex-only  (always available, no CCLS required)
  - CCLS-enhanced  (uses LSP call hierarchy when available)

Author: Pavan R
"""

import fnmatch
import logging
import os
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════════

C_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}

# Directories always excluded from indexing
_BUILTIN_EXCLUDE_DIRS = frozenset({
    ".git", "build", "node_modules", ".venv", "__pycache__", "dist",
    ".ccls-cache", "bin", "obj", ".svn", ".hg", "CMakeFiles",
})

# C keywords to skip when extracting identifiers
_C_KEYWORDS = frozenset({
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "inline", "int", "long", "register", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned",
    "void", "volatile", "while", "bool", "true", "false", "NULL", "nullptr",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t", "int8_t", "int16_t",
    "int32_t", "int64_t", "size_t", "ssize_t", "ptrdiff_t",
})

# Known allocation functions
_ALLOC_FUNCTIONS = frozenset({
    "malloc", "calloc", "realloc", "strdup", "strndup",
    "kmalloc", "kzalloc", "kcalloc", "kvmalloc", "kvzalloc",
    "devm_kzalloc", "devm_kmalloc", "devm_kcalloc",
    "vzalloc", "vmalloc", "dma_alloc_coherent",
    "kstrdup", "kasprintf", "krealloc",
    "qdf_mem_malloc", "qdf_mem_malloc_atomic",
})

# ═══════════════════════════════════════════════════════════════════════════════
#  Regex Patterns
# ═══════════════════════════════════════════════════════════════════════════════

# Function definition: captures return_type, name, params
# Handles: static, inline, __attribute__, const qualifiers
_FUNC_DEF_RE = re.compile(
    r"^[ \t]*"                                   # optional leading whitespace
    r"((?:static\s+|inline\s+|__\w+\s*\([^)]*\)\s+)*"  # optional qualifiers
    r"(?:(?:const\s+|volatile\s+|unsigned\s+|signed\s+|long\s+|short\s+)*"
    r"[\w*]+(?:\s*\*)*)\s+)"                     # return type
    r"(\w+)"                                      # function name
    r"\s*\(([^)]*)\)"                             # parameter list
    r"\s*\{",                                     # opening brace
    re.MULTILINE,
)

# Null check patterns
_NULL_CHECK_RE = re.compile(
    r"if\s*\(\s*!?\s*(\w+)\s*(?:==|!=)\s*(?:NULL|nullptr|0)\s*\)"
)
_NULL_CHECK_SHORT_RE = re.compile(r"if\s*\(\s*!(\w+)\s*\)")

# Bounds check: if (var op LIMIT)
_BOUNDS_CHECK_RE = re.compile(
    r"if\s*\(\s*(\w+)\s*(<|<=|>=|>)\s*(\w+)\s*\)"
)

# For-loop: for (...; var op limit; ...)
_FOR_LOOP_RE = re.compile(
    r"for\s*\([^;]*;\s*(\w+)\s*(<|<=)\s*(\w+)\s*;"
)

# Assignment: var = expr;  (skip == comparisons)
_ASSIGNMENT_RE = re.compile(r"\b(\w+)\s*=\s*([^;=]+);")

# Function call with assignment: var = func(args);
_FUNC_CALL_ASSIGN_RE = re.compile(r"\b(\w+)\s*=\s*(\w+)\s*\(([^)]*)\)\s*;")

# Standalone function call: func(args);
_FUNC_CALL_RE = re.compile(r"\b(\w+)\s*\(([^)]*)\)\s*;")

# Division/modulo: a / b  or  a % b
_DIVISION_RE = re.compile(r"\b(\w+)\s*([/%])\s*(\w+)")

# Array access: arr[idx]
_ARRAY_ACCESS_RE = re.compile(r"\b(\w+)\s*\[\s*([^\]]+)\s*\]")

# Pointer dereference: ptr->field
_PTR_DEREF_RE = re.compile(r"\b(\w+)\s*->")

# Star dereference: *ptr  (excluding declarations like int *ptr)
_PTR_STAR_RE = re.compile(r"(?<!\w)\*\s*(\w+)")

# Enum variable declaration: enum type var
_ENUM_VAR_DECL_RE = re.compile(r"\benum\s+(\w+)\s+(\w+)")

# Macro name (all uppercase with underscores, at least 2 chars)
_MACRO_NAME_RE = re.compile(r"\b([A-Z][A-Z0-9_]{1,})\b")

# Non-zero check: if (var == 0) return  or  if (!var) return
_NONZERO_CHECK_RE = re.compile(
    r"if\s*\(\s*(?:!(\w+)|(\w+)\s*==\s*0)\s*\)\s*(?:return|goto)"
)

# Parameter parsing: type [*] name
_PARAM_RE = re.compile(
    r"(?:const\s+|volatile\s+|unsigned\s+|signed\s+|long\s+|short\s+|enum\s+|struct\s+)*"
    r"([\w]+(?:\s*\*)*)\s+(\*?\s*\w+)"
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ParamInfo:
    """A single function parameter."""
    name: str
    type_name: str
    is_pointer: bool
    position: int  # 0-indexed


@dataclass
class CallSite:
    """Records a function call within a function body."""
    callee_name: str
    line_number: int
    arguments: List[str] = field(default_factory=list)


@dataclass
class FunctionDef:
    """Metadata extracted from a single C/C++ function definition."""
    name: str
    file_path: str
    start_line: int
    end_line: int
    body: str = ""
    return_type: str = ""
    is_static: bool = False
    parameters: List[ParamInfo] = field(default_factory=list)
    null_checks: Dict[str, int] = field(default_factory=dict)
    bounds_checks: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    assignments: Dict[str, List[str]] = field(default_factory=dict)
    callees: Set[str] = field(default_factory=set)
    call_sites: Dict[str, List[CallSite]] = field(default_factory=dict)
    for_loops: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    nonzero_checks: Set[str] = field(default_factory=set)


@dataclass
class SymbolEvidence:
    """Evidence found for a single symbol through call-chain tracing."""
    symbol_name: str
    evidence_type: str   # null_check, bounds, non_zero, enum_range, macro_value, allocation, loop_bound
    status: str          # VALIDATED, BOUNDED, NOT_CHECKED, ALLOCATED_UNCHECKED, GUARANTEED_NON_ZERO
    source_func: str     # function where evidence was found
    source_line: int = 0
    detail: str = ""     # human-readable explanation
    depth: int = 0       # 0=current, 1=caller, 2=caller's caller
    confidence: str = "MEDIUM"


@dataclass
class MacroValue:
    """Resolved macro constant."""
    name: str
    value: str
    is_numeric: bool = False
    numeric_value: Optional[int] = None


@dataclass
class EnumRange:
    """Resolved enum type range."""
    type_name: str
    members: List[str] = field(default_factory=list)
    min_value: int = 0
    max_value: int = 0
    count: int = 0


@dataclass
class ChunkCallStackContext:
    """All evidence collected for a single code chunk."""
    pointer_evidence: List[SymbolEvidence] = field(default_factory=list)
    index_evidence: List[SymbolEvidence] = field(default_factory=list)
    division_evidence: List[SymbolEvidence] = field(default_factory=list)
    enum_evidence: List[SymbolEvidence] = field(default_factory=list)
    macro_evidence: List[SymbolEvidence] = field(default_factory=list)
    loop_evidence: List[SymbolEvidence] = field(default_factory=list)

    def format_for_prompt(self, max_chars: int = 1200) -> str:
        """Format all evidence as a compact C-comment block for LLM injection."""
        sections = []

        # Priority order: pointers > indices > divisions > enums > macros > loops
        if self.pointer_evidence:
            lines = ["// Pointers:"]
            for ev in self.pointer_evidence[:8]:
                lines.append(f"//   {ev.symbol_name:<14s} -> {ev.detail}")
            sections.append("\n".join(lines))

        if self.index_evidence:
            lines = ["// Array Bounds:"]
            for ev in self.index_evidence[:6]:
                lines.append(f"//   {ev.symbol_name:<14s} -> {ev.detail}")
            sections.append("\n".join(lines))

        if self.division_evidence:
            lines = ["// Division Safety:"]
            for ev in self.division_evidence[:4]:
                lines.append(f"//   {ev.symbol_name:<14s} -> {ev.detail}")
            sections.append("\n".join(lines))

        if self.enum_evidence:
            lines = ["// Enum Ranges:"]
            for ev in self.enum_evidence[:4]:
                lines.append(f"//   {ev.symbol_name:<14s} -> {ev.detail}")
            sections.append("\n".join(lines))

        if self.macro_evidence:
            # Compact: multiple macros on fewer lines
            items = [f"{ev.symbol_name}={ev.detail}" for ev in self.macro_evidence[:6]]
            sections.append("// Macros: " + ", ".join(items))

        if self.loop_evidence:
            lines = ["// Loop Bounds:"]
            for ev in self.loop_evidence[:4]:
                lines.append(f"//   {ev.symbol_name:<14s} -> {ev.detail}")
            sections.append("\n".join(lines))

        if not sections:
            return ""

        # Build output with budget
        header = "// ──── CALL STACK CONTEXT ────────────────────────────────"
        footer = "// ──── END CALL STACK CONTEXT ────────────────────────────"

        body = "\n".join(sections)

        # Truncate body if over budget (leave room for header/footer)
        budget = max_chars - len(header) - len(footer) - 10
        if len(body) > budget:
            body = body[:budget].rsplit("\n", 1)[0]

        return f"{header}\n{body}\n{footer}"


# ═══════════════════════════════════════════════════════════════════════════════
#  CodebaseIndex — Codebase-wide function registry
# ═══════════════════════════════════════════════════════════════════════════════

class CodebaseIndex:
    """
    In-memory index of all C/C++ functions in the codebase.
    Built once at initialization, queried per-chunk for call-chain tracing.
    """

    def __init__(self):
        # "file_path:func_name" → FunctionDef
        self.functions: Dict[str, FunctionDef] = {}
        # Short name → list of FunctionDefs (handles overloads / same-name in different files)
        self.functions_by_name: Dict[str, List[FunctionDef]] = {}
        # Call graph: func_key → set of callee short names
        self.call_graph: Dict[str, Set[str]] = {}
        # Reverse: callee short name → set of caller func_keys
        self.reverse_call_graph: Dict[str, Set[str]] = {}
        # Macro constants resolved from headers
        self.macro_values: Dict[str, MacroValue] = {}
        # Enum type ranges
        self.enum_ranges: Dict[str, EnumRange] = {}
        # File → list of function keys (sorted by start_line)
        self.functions_by_file: Dict[str, List[str]] = {}

    def register_function(self, func_def: FunctionDef):
        """Add a function to the index."""
        key = f"{func_def.file_path}:{func_def.name}"
        self.functions[key] = func_def

        if func_def.name not in self.functions_by_name:
            self.functions_by_name[func_def.name] = []
        self.functions_by_name[func_def.name].append(func_def)

        # Track by file
        if func_def.file_path not in self.functions_by_file:
            self.functions_by_file[func_def.file_path] = []
        self.functions_by_file[func_def.file_path].append(key)

    def build_call_graphs(self):
        """Build forward and reverse call graphs from callee sets."""
        for key, func_def in self.functions.items():
            self.call_graph[key] = set(func_def.callees)
            for callee_name in func_def.callees:
                if callee_name not in self.reverse_call_graph:
                    self.reverse_call_graph[callee_name] = set()
                self.reverse_call_graph[callee_name].add(key)

    def get_function(self, name: str) -> Optional[FunctionDef]:
        """Lookup by short name (returns first match)."""
        funcs = self.functions_by_name.get(name, [])
        return funcs[0] if funcs else None

    def get_function_by_key(self, key: str) -> Optional[FunctionDef]:
        """Lookup by full key (file:name)."""
        return self.functions.get(key)

    def get_callers_of(self, func_name: str) -> List[FunctionDef]:
        """Return all functions that call func_name."""
        caller_keys = self.reverse_call_graph.get(func_name, set())
        result = []
        for key in caller_keys:
            fd = self.functions.get(key)
            if fd:
                result.append(fd)
        return result

    def find_enclosing_function(self, file_path: str, line_number: int) -> Optional[FunctionDef]:
        """Find the function that encloses the given line number."""
        keys = self.functions_by_file.get(file_path, [])
        best = None
        for key in keys:
            fd = self.functions[key]
            if fd.start_line <= line_number <= fd.end_line:
                if best is None or fd.start_line > best.start_line:
                    best = fd
        return best

    def stats(self) -> str:
        """Return summary string for logging."""
        return (
            f"functions={len(self.functions)}, "
            f"call_edges={sum(len(v) for v in self.call_graph.values())}, "
            f"macros={len(self.macro_values)}, "
            f"enums={len(self.enum_ranges)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  StaticCallStackAnalyzer — Main class
# ═══════════════════════════════════════════════════════════════════════════════

class StaticCallStackAnalyzer:
    """
    Builds a codebase-wide function index, then for each code chunk traces
    every pointer/index/divisor/enum through the call chain to produce
    deep call-stack context for the LLM.

    Usage:
        analyzer = StaticCallStackAnalyzer(codebase_path="/path/to/src")
        context_str = analyzer.analyze_chunk(chunk_text, file_path, file_content, start_line)
        # → Inject context_str into LLM prompt before the code chunk
    """

    def __init__(
        self,
        codebase_path: str,
        exclude_dirs: Optional[List[str]] = None,
        exclude_globs: Optional[List[str]] = None,
        header_context_builder=None,
        use_ccls: bool = False,
        ccls_navigator=None,
        max_trace_depth: int = 3,
        max_context_chars: int = 1200,
    ):
        self.codebase_path = Path(codebase_path)
        self.exclude_dirs = set(exclude_dirs or []) | _BUILTIN_EXCLUDE_DIRS
        self.exclude_globs = [g.lower() for g in (exclude_globs or [])]
        self.header_context_builder = header_context_builder
        self.use_ccls = use_ccls
        self.ccls_navigator = ccls_navigator
        self.max_trace_depth = max_trace_depth
        self.max_context_chars = max_context_chars

        # Build the codebase index
        self.index = CodebaseIndex()
        self._build_index()

    # ───────────────────────────────────────────────────────────────────────
    #  Index Building (Phase 1)
    # ───────────────────────────────────────────────────────────────────────

    def _build_index(self):
        """Walk the codebase and build the function index."""
        t0 = time.time()
        files_scanned = 0

        for root, dirs, filenames in os.walk(self.codebase_path):
            # Prune excluded directories in-place
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]

            for fname in filenames:
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()
                if ext not in C_EXTENSIONS:
                    continue

                # Apply glob exclusions
                try:
                    rel_path = os.path.relpath(fpath, self.codebase_path).lower()
                except ValueError:
                    continue
                if any(fnmatch.fnmatch(rel_path, pat) for pat in self.exclude_globs):
                    continue

                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    self._index_file(fpath, content)
                    files_scanned += 1
                except Exception as exc:
                    logger.debug(f"Index skip {fpath}: {exc}")

        # Build call graphs from callee sets
        self.index.build_call_graphs()

        # Import macro and enum data from HeaderContextBuilder if available
        self._import_header_context_data()

        elapsed = time.time() - t0
        logger.info(
            f"[CallStackAnalyzer] Index built in {elapsed:.1f}s — "
            f"{files_scanned} files, {self.index.stats()}"
        )

    def _index_file(self, file_path: str, content: str):
        """Extract all function definitions and their metadata from a single file."""
        # Strip single-line comments for cleaner parsing
        stripped = re.sub(r"//[^\n]*", "", content)
        # Strip block comments
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)

        lines = content.split("\n")
        total_lines = len(lines)

        for match in _FUNC_DEF_RE.finditer(stripped):
            ret_type = match.group(1).strip()
            func_name = match.group(2)
            params_str = match.group(3).strip()

            # Skip C keywords that look like functions (sizeof, if, while, etc.)
            if func_name in _C_KEYWORDS:
                continue

            # Find opening brace position and line number
            brace_pos = match.end() - 1
            start_line = stripped[:match.start()].count("\n") + 1

            # Find matching closing brace
            end_pos = self._find_matching_brace(stripped, brace_pos)
            if end_pos < 0:
                continue
            end_line = stripped[:end_pos].count("\n") + 1

            # Extract function body
            body = stripped[brace_pos + 1:end_pos]

            # Parse parameters
            parameters = self._parse_parameters(params_str)

            # Detect static/inline
            is_static = "static" in ret_type.lower()

            func_def = FunctionDef(
                name=func_name,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                body=body,
                return_type=ret_type,
                is_static=is_static,
                parameters=parameters,
            )

            # Extract metadata from function body
            self._extract_null_checks(func_def)
            self._extract_bounds_checks(func_def)
            self._extract_for_loops(func_def)
            self._extract_assignments(func_def)
            self._extract_callees(func_def)
            self._extract_call_sites(func_def)
            self._extract_nonzero_checks(func_def)

            self.index.register_function(func_def)

    def _find_matching_brace(self, content: str, start: int) -> int:
        """Find the matching closing brace for the opening brace at `start`."""
        if start >= len(content) or content[start] != "{":
            return -1

        depth = 1
        i = start + 1
        in_string = False
        in_char = False
        length = len(content)

        while i < length and depth > 0:
            ch = content[i]
            if in_string:
                if ch == "\\" and i + 1 < length:
                    i += 2
                    continue
                if ch == '"':
                    in_string = False
            elif in_char:
                if ch == "\\" and i + 1 < length:
                    i += 2
                    continue
                if ch == "'":
                    in_char = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "'":
                    in_char = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
            i += 1

        return i - 1 if depth == 0 else -1

    def _parse_parameters(self, params_str: str) -> List[ParamInfo]:
        """Parse a C function parameter list into ParamInfo objects."""
        if not params_str or params_str.strip() == "void":
            return []

        params = []
        position = 0

        for part in params_str.split(","):
            part = part.strip()
            if not part:
                continue

            is_pointer = "*" in part
            # Extract the last word as name
            tokens = part.replace("*", " * ").split()
            if len(tokens) >= 2:
                name = tokens[-1].strip("*").strip()
                type_name = " ".join(tokens[:-1]).replace(" * ", "*").strip()
            elif len(tokens) == 1:
                name = tokens[0]
                type_name = "unknown"
            else:
                position += 1
                continue

            # Clean up name
            name = name.strip("*").strip()
            if name and name not in _C_KEYWORDS:
                params.append(ParamInfo(
                    name=name,
                    type_name=type_name,
                    is_pointer=is_pointer,
                    position=position,
                ))
            position += 1

        return params

    def _extract_null_checks(self, func_def: FunctionDef):
        """Find null check patterns in the function body."""
        body_lines = func_def.body.split("\n")
        for i, line in enumerate(body_lines):
            for m in _NULL_CHECK_RE.finditer(line):
                var = m.group(1)
                func_def.null_checks[var] = func_def.start_line + i + 1
            for m in _NULL_CHECK_SHORT_RE.finditer(line):
                var = m.group(1)
                if var not in func_def.null_checks:
                    func_def.null_checks[var] = func_def.start_line + i + 1

    def _extract_bounds_checks(self, func_def: FunctionDef):
        """Find bounds comparison patterns: if (var < LIMIT)."""
        for m in _BOUNDS_CHECK_RE.finditer(func_def.body):
            var = m.group(1)
            op = m.group(2)
            limit = m.group(3)
            func_def.bounds_checks[var] = (op, limit)

    def _extract_for_loops(self, func_def: FunctionDef):
        """Find for-loop bound patterns: for (...; var < limit; ...)."""
        for m in _FOR_LOOP_RE.finditer(func_def.body):
            var = m.group(1)
            op = m.group(2)
            limit = m.group(3)
            func_def.for_loops[var] = (op, limit)

    def _extract_assignments(self, func_def: FunctionDef):
        """Extract variable assignments (var = expr)."""
        for m in _ASSIGNMENT_RE.finditer(func_def.body):
            var = m.group(1)
            rhs = m.group(2).strip()
            if var in _C_KEYWORDS:
                continue
            if var not in func_def.assignments:
                func_def.assignments[var] = []
            func_def.assignments[var].append(rhs)

    def _extract_callees(self, func_def: FunctionDef):
        """Extract function names called within the body."""
        for m in _FUNC_CALL_RE.finditer(func_def.body):
            callee = m.group(1)
            if callee not in _C_KEYWORDS and not callee.startswith("__"):
                func_def.callees.add(callee)
        # Also from assignment calls
        for m in _FUNC_CALL_ASSIGN_RE.finditer(func_def.body):
            callee = m.group(2)
            if callee not in _C_KEYWORDS and not callee.startswith("__"):
                func_def.callees.add(callee)

    def _extract_call_sites(self, func_def: FunctionDef):
        """Extract call sites with argument lists for caller tracing."""
        body_lines = func_def.body.split("\n")
        for i, line in enumerate(body_lines):
            for m in _FUNC_CALL_ASSIGN_RE.finditer(line):
                callee = m.group(2)
                args_str = m.group(3)
                args = [a.strip() for a in args_str.split(",") if a.strip()]
                site = CallSite(
                    callee_name=callee,
                    line_number=func_def.start_line + i + 1,
                    arguments=args,
                )
                if callee not in func_def.call_sites:
                    func_def.call_sites[callee] = []
                func_def.call_sites[callee].append(site)

            # Also standalone calls (no assignment)
            for m in _FUNC_CALL_RE.finditer(line):
                callee = m.group(1)
                args_str = m.group(2)
                if callee in func_def.call_sites:
                    continue  # Already captured via assign pattern
                if callee in _C_KEYWORDS:
                    continue
                args = [a.strip() for a in args_str.split(",") if a.strip()]
                site = CallSite(
                    callee_name=callee,
                    line_number=func_def.start_line + i + 1,
                    arguments=args,
                )
                if callee not in func_def.call_sites:
                    func_def.call_sites[callee] = []
                func_def.call_sites[callee].append(site)

    def _extract_nonzero_checks(self, func_def: FunctionDef):
        """Find patterns like: if (var == 0) return; or if (!var) return;"""
        for m in _NONZERO_CHECK_RE.finditer(func_def.body):
            var = m.group(1) or m.group(2)
            if var:
                func_def.nonzero_checks.add(var)

    def _import_header_context_data(self):
        """Import macro and enum data from HeaderContextBuilder if available."""
        if not self.header_context_builder:
            return

        try:
            # Import from header cache
            for _path, header_defs in self.header_context_builder._header_cache.items():
                for enum_def in header_defs.enums:
                    members = [m.name for m in enum_def.members]
                    numeric_vals = [
                        m.numeric_value for m in enum_def.members
                        if m.numeric_value is not None
                    ]
                    self.index.enum_ranges[enum_def.name] = EnumRange(
                        type_name=enum_def.name,
                        members=members,
                        min_value=min(numeric_vals) if numeric_vals else 0,
                        max_value=max(numeric_vals) if numeric_vals else 0,
                        count=len(members),
                    )
                for macro_def in header_defs.macros:
                    self.index.macro_values[macro_def.name] = MacroValue(
                        name=macro_def.name,
                        value=macro_def.value,
                        is_numeric=macro_def.is_numeric,
                        numeric_value=macro_def.numeric_value,
                    )
        except Exception as exc:
            logger.debug(f"Header context import failed: {exc}")

    # ───────────────────────────────────────────────────────────────────────
    #  Per-Chunk Analysis (Phase 2)
    # ───────────────────────────────────────────────────────────────────────

    def analyze_chunk(
        self,
        chunk_text: str,
        file_path: str,
        file_content: str,
        start_line: int,
    ) -> str:
        """
        Analyze a code chunk and return call-stack context as a formatted string.

        Args:
            chunk_text: The code chunk being sent to the LLM
            file_path: Absolute path to the source file
            file_content: Entire file content (for enclosing function lookup)
            start_line: Starting line number of the chunk

        Returns:
            Formatted C-comment block with call-stack evidence, or empty string
        """
        try:
            ctx = ChunkCallStackContext()

            # Find the enclosing function for this chunk
            enclosing_func = self.index.find_enclosing_function(file_path, start_line)

            # 1. Trace pointer dereferences
            seen_ptrs = set()
            for m in _PTR_DEREF_RE.finditer(chunk_text):
                ptr_name = m.group(1)
                if ptr_name in seen_ptrs or ptr_name in _C_KEYWORDS:
                    continue
                seen_ptrs.add(ptr_name)
                ev = self._trace_pointer(ptr_name, enclosing_func, chunk_text)
                if ev:
                    ctx.pointer_evidence.append(ev)
                if len(ctx.pointer_evidence) >= 8:
                    break

            # 2. Trace array accesses
            seen_indices = set()
            for m in _ARRAY_ACCESS_RE.finditer(chunk_text):
                arr_name = m.group(1)
                idx_expr = m.group(2).strip()
                # Extract simple variable name from index expression
                idx_match = re.match(r"(\w+)", idx_expr)
                if not idx_match:
                    continue
                idx_name = idx_match.group(1)
                if idx_name in seen_indices or idx_name in _C_KEYWORDS:
                    continue
                seen_indices.add(idx_name)
                ev = self._trace_index(idx_name, arr_name, enclosing_func, chunk_text)
                if ev:
                    ctx.index_evidence.append(ev)
                if len(ctx.index_evidence) >= 6:
                    break

            # 3. Trace divisions
            seen_divs = set()
            for m in _DIVISION_RE.finditer(chunk_text):
                divisor = m.group(3)
                if divisor in seen_divs or divisor in _C_KEYWORDS:
                    continue
                seen_divs.add(divisor)
                ev = self._trace_divisor(divisor, enclosing_func, chunk_text)
                if ev:
                    ctx.division_evidence.append(ev)
                if len(ctx.division_evidence) >= 4:
                    break

            # 4. Trace enum usage
            seen_enums = set()
            for m in _ENUM_VAR_DECL_RE.finditer(chunk_text):
                enum_type = m.group(1)
                var_name = m.group(2)
                if enum_type in seen_enums:
                    continue
                seen_enums.add(enum_type)
                ev = self._trace_enum(enum_type, var_name)
                if ev:
                    ctx.enum_evidence.append(ev)

            # Also check for enum-typed variables used as array indices
            for idx_name in seen_indices:
                ev = self._check_enum_index(idx_name, enclosing_func, chunk_text)
                if ev and ev.symbol_name not in seen_enums:
                    ctx.enum_evidence.append(ev)
                    if len(ctx.enum_evidence) >= 4:
                        break

            # 5. Trace macro values
            seen_macros = set()
            for m in _MACRO_NAME_RE.finditer(chunk_text):
                macro_name = m.group(1)
                if macro_name in seen_macros or len(macro_name) < 3:
                    continue
                seen_macros.add(macro_name)
                ev = self._trace_macro(macro_name)
                if ev:
                    ctx.macro_evidence.append(ev)
                if len(ctx.macro_evidence) >= 6:
                    break

            # 6. Trace loop bounds
            for m in _FOR_LOOP_RE.finditer(chunk_text):
                loop_var = m.group(1)
                limit_var = m.group(3)
                ev = self._trace_loop_bound(limit_var, enclosing_func)
                if ev:
                    ctx.loop_evidence.append(ev)
                if len(ctx.loop_evidence) >= 4:
                    break

            return ctx.format_for_prompt(max_chars=self.max_context_chars)

        except Exception as exc:
            logger.debug(f"Call stack analysis error: {exc}")
            return ""

    # ───────────────────────────────────────────────────────────────────────
    #  Symbol Tracers
    # ───────────────────────────────────────────────────────────────────────

    def _trace_pointer(
        self, ptr_name: str, enclosing_func: Optional[FunctionDef],
        chunk_text: str, depth: int = 0,
    ) -> Optional[SymbolEvidence]:
        """Trace a pointer dereference through the call chain."""

        # Check if locally allocated in chunk
        for alloc_fn in _ALLOC_FUNCTIONS:
            if re.search(rf"\b{re.escape(ptr_name)}\s*=\s*{re.escape(alloc_fn)}\s*\(", chunk_text):
                # Check if null-checked after allocation
                if _NULL_CHECK_RE.search(chunk_text) or _NULL_CHECK_SHORT_RE.search(chunk_text):
                    has_check = any(
                        m.group(1) == ptr_name
                        for m in _NULL_CHECK_RE.finditer(chunk_text)
                    ) or any(
                        m.group(1) == ptr_name
                        for m in _NULL_CHECK_SHORT_RE.finditer(chunk_text)
                    )
                    if has_check:
                        return SymbolEvidence(
                            symbol_name=ptr_name,
                            evidence_type="null_check",
                            status="VALIDATED",
                            source_func=enclosing_func.name if enclosing_func else "?",
                            detail=f"Allocated ({alloc_fn}) + null-checked locally",
                            depth=0,
                            confidence="HIGH",
                        )
                return SymbolEvidence(
                    symbol_name=ptr_name,
                    evidence_type="allocation",
                    status="ALLOCATED_UNCHECKED",
                    source_func=enclosing_func.name if enclosing_func else "?",
                    detail=f"Allocated ({alloc_fn}), needs null check",
                    depth=0,
                    confidence="HIGH",
                )

        if not enclosing_func:
            return None

        # Check if null-checked in enclosing function
        if ptr_name in enclosing_func.null_checks:
            line = enclosing_func.null_checks[ptr_name]
            return SymbolEvidence(
                symbol_name=ptr_name,
                evidence_type="null_check",
                status="VALIDATED",
                source_func=enclosing_func.name,
                source_line=line,
                detail=f"NULL-checked in {enclosing_func.name}() at L{line}",
                depth=0,
                confidence="HIGH",
            )

        # Check if it's a parameter — trace through callers
        param = self._find_param(ptr_name, enclosing_func)
        if param and param.is_pointer:
            # Static function → caller is responsible
            if enclosing_func.is_static:
                return SymbolEvidence(
                    symbol_name=ptr_name,
                    evidence_type="null_check",
                    status="VALIDATED",
                    source_func=enclosing_func.name,
                    detail=f"Param of static {enclosing_func.name}() — caller validates",
                    depth=0,
                    confidence="MEDIUM",
                )

            # Trace through callers
            if depth < self.max_trace_depth:
                ev = self._trace_param_through_callers(
                    ptr_name, param.position, enclosing_func,
                    check_type="null_check", depth=depth,
                )
                if ev:
                    return ev

        return None

    def _trace_index(
        self, idx_name: str, array_name: str,
        enclosing_func: Optional[FunctionDef], chunk_text: str,
    ) -> Optional[SymbolEvidence]:
        """Trace an array index to find bounds evidence."""

        # Check for-loop bounds in chunk
        for m in _FOR_LOOP_RE.finditer(chunk_text):
            if m.group(1) == idx_name:
                limit = m.group(3)
                resolved = self._resolve_value(limit)
                detail = f"Bounded: for-loop {idx_name} {m.group(2)} {limit}"
                if resolved:
                    detail += f" (={resolved})"
                return SymbolEvidence(
                    symbol_name=idx_name,
                    evidence_type="bounds",
                    status="BOUNDED",
                    source_func=enclosing_func.name if enclosing_func else "?",
                    detail=detail,
                    depth=0,
                    confidence="HIGH",
                )

        # Check bounds comparison in chunk
        for m in _BOUNDS_CHECK_RE.finditer(chunk_text):
            if m.group(1) == idx_name:
                limit = m.group(3)
                resolved = self._resolve_value(limit)
                detail = f"Bounded: checked {idx_name} {m.group(2)} {limit}"
                if resolved:
                    detail += f" (={resolved})"
                return SymbolEvidence(
                    symbol_name=idx_name,
                    evidence_type="bounds",
                    status="BOUNDED",
                    source_func=enclosing_func.name if enclosing_func else "?",
                    detail=detail,
                    depth=0,
                    confidence="HIGH",
                )

        # Check modulo in chunk
        mod_match = re.search(rf"\b{re.escape(idx_name)}\s*%\s*(\w+)", chunk_text)
        if mod_match:
            mod_base = mod_match.group(1)
            resolved = self._resolve_value(mod_base)
            detail = f"Bounded: {idx_name} % {mod_base}"
            if resolved:
                detail += f" (={resolved})"
            return SymbolEvidence(
                symbol_name=idx_name,
                evidence_type="bounds",
                status="BOUNDED",
                source_func=enclosing_func.name if enclosing_func else "?",
                detail=detail,
                depth=0,
                confidence="HIGH",
            )

        # Check enclosing function's bounds and loops
        if enclosing_func:
            if idx_name in enclosing_func.for_loops:
                op, limit = enclosing_func.for_loops[idx_name]
                resolved = self._resolve_value(limit)
                detail = f"Bounded: for-loop in {enclosing_func.name}() {idx_name} {op} {limit}"
                if resolved:
                    detail += f" (={resolved})"
                return SymbolEvidence(
                    symbol_name=idx_name,
                    evidence_type="bounds",
                    status="BOUNDED",
                    source_func=enclosing_func.name,
                    detail=detail,
                    depth=0,
                    confidence="MEDIUM",
                )

            if idx_name in enclosing_func.bounds_checks:
                op, limit = enclosing_func.bounds_checks[idx_name]
                resolved = self._resolve_value(limit)
                detail = f"Bounded: {enclosing_func.name}() checks {idx_name} {op} {limit}"
                if resolved:
                    detail += f" (={resolved})"
                return SymbolEvidence(
                    symbol_name=idx_name,
                    evidence_type="bounds",
                    status="BOUNDED",
                    source_func=enclosing_func.name,
                    detail=detail,
                    depth=0,
                    confidence="MEDIUM",
                )

        return None

    def _trace_divisor(
        self, divisor: str, enclosing_func: Optional[FunctionDef],
        chunk_text: str,
    ) -> Optional[SymbolEvidence]:
        """Trace a divisor to determine if it's guaranteed non-zero."""

        # Check if it's a numeric literal
        try:
            val = int(divisor, 0)
            if val != 0:
                return SymbolEvidence(
                    symbol_name=divisor,
                    evidence_type="non_zero",
                    status="GUARANTEED_NON_ZERO",
                    source_func="literal",
                    detail=f"Constant {divisor} (non-zero)",
                    depth=0,
                    confidence="HIGH",
                )
        except ValueError:
            pass

        # Check if it's a macro with known numeric value
        macro_val = self.index.macro_values.get(divisor)
        if macro_val and macro_val.is_numeric and macro_val.numeric_value and macro_val.numeric_value != 0:
            return SymbolEvidence(
                symbol_name=divisor,
                evidence_type="non_zero",
                status="GUARANTEED_NON_ZERO",
                source_func="macro",
                detail=f"Macro {divisor}={macro_val.numeric_value} (non-zero)",
                depth=0,
                confidence="HIGH",
            )

        # Check non-zero check in chunk
        if enclosing_func and divisor in enclosing_func.nonzero_checks:
            return SymbolEvidence(
                symbol_name=divisor,
                evidence_type="non_zero",
                status="GUARANTEED_NON_ZERO",
                source_func=enclosing_func.name,
                detail=f"Zero-checked in {enclosing_func.name}() before division",
                depth=0,
                confidence="HIGH",
            )

        # Check non-zero guard in chunk text
        if re.search(rf"if\s*\(\s*!?{re.escape(divisor)}\s*(?:==|!=)\s*0\s*\)\s*(?:return|goto)", chunk_text):
            return SymbolEvidence(
                symbol_name=divisor,
                evidence_type="non_zero",
                status="GUARANTEED_NON_ZERO",
                source_func=enclosing_func.name if enclosing_func else "?",
                detail=f"Zero-guard before division (return/goto on zero)",
                depth=0,
                confidence="HIGH",
            )

        # Trace through callers
        if enclosing_func:
            param = self._find_param(divisor, enclosing_func)
            if param:
                ev = self._trace_param_through_callers(
                    divisor, param.position, enclosing_func,
                    check_type="non_zero", depth=0,
                )
                if ev:
                    return ev

        return None

    def _trace_enum(self, enum_type: str, var_name: str) -> Optional[SymbolEvidence]:
        """Resolve an enum type to its range."""
        er = self.index.enum_ranges.get(enum_type)
        if er:
            return SymbolEvidence(
                symbol_name=var_name,
                evidence_type="enum_range",
                status="BOUNDED",
                source_func="enum_def",
                detail=f"Enum {enum_type} range [{er.min_value}..{er.max_value}] ({er.count} members)",
                depth=0,
                confidence="HIGH",
            )
        return None

    def _check_enum_index(
        self, idx_name: str, enclosing_func: Optional[FunctionDef],
        chunk_text: str,
    ) -> Optional[SymbolEvidence]:
        """Check if an array index variable is enum-typed."""
        # Look for enum type declaration in chunk or function
        m = re.search(rf"enum\s+(\w+)\s+.*?\b{re.escape(idx_name)}\b", chunk_text)
        if m:
            return self._trace_enum(m.group(1), idx_name)

        # Check enclosing function body
        if enclosing_func:
            m = re.search(rf"enum\s+(\w+)\s+.*?\b{re.escape(idx_name)}\b", enclosing_func.body)
            if m:
                return self._trace_enum(m.group(1), idx_name)

        return None

    def _trace_macro(self, macro_name: str) -> Optional[SymbolEvidence]:
        """Resolve a macro to its value."""
        mv = self.index.macro_values.get(macro_name)
        if mv:
            if mv.is_numeric:
                return SymbolEvidence(
                    symbol_name=macro_name,
                    evidence_type="macro_value",
                    status="BOUNDED",
                    source_func="macro_def",
                    detail=str(mv.numeric_value),
                    depth=0,
                    confidence="HIGH",
                )
            elif mv.value:
                short_val = mv.value[:30]
                return SymbolEvidence(
                    symbol_name=macro_name,
                    evidence_type="macro_value",
                    status="BOUNDED",
                    source_func="macro_def",
                    detail=short_val,
                    depth=0,
                    confidence="MEDIUM",
                )
        return None

    def _trace_loop_bound(
        self, limit_var: str, enclosing_func: Optional[FunctionDef],
    ) -> Optional[SymbolEvidence]:
        """Trace a loop bound variable to its origin."""

        # Check if it's a macro
        mv = self.index.macro_values.get(limit_var)
        if mv and mv.is_numeric:
            return SymbolEvidence(
                symbol_name=limit_var,
                evidence_type="loop_bound",
                status="BOUNDED",
                source_func="macro_def",
                detail=f"Loop limit {limit_var}={mv.numeric_value} (macro)",
                depth=0,
                confidence="HIGH",
            )

        # Check if it's an enum member
        for enum_name, er in self.index.enum_ranges.items():
            if limit_var in er.members:
                return SymbolEvidence(
                    symbol_name=limit_var,
                    evidence_type="loop_bound",
                    status="BOUNDED",
                    source_func="enum_def",
                    detail=f"Loop limit {limit_var} is member of enum {enum_name}",
                    depth=0,
                    confidence="HIGH",
                )

        # Check if it's assigned from a known source in enclosing function
        if enclosing_func and limit_var in enclosing_func.assignments:
            rhs_list = enclosing_func.assignments[limit_var]
            if rhs_list:
                rhs = rhs_list[-1]  # Last assignment
                resolved = self._resolve_value(rhs.strip())
                if resolved:
                    return SymbolEvidence(
                        symbol_name=limit_var,
                        evidence_type="loop_bound",
                        status="BOUNDED",
                        source_func=enclosing_func.name,
                        detail=f"Loop limit {limit_var}={resolved} (assigned in {enclosing_func.name})",
                        depth=0,
                        confidence="MEDIUM",
                    )

        return None

    # ───────────────────────────────────────────────────────────────────────
    #  Call Chain BFS
    # ───────────────────────────────────────────────────────────────────────

    def _trace_param_through_callers(
        self,
        param_name: str,
        param_position: int,
        current_func: FunctionDef,
        check_type: str,
        depth: int = 0,
    ) -> Optional[SymbolEvidence]:
        """
        BFS through the reverse call graph to find evidence for a parameter.

        For each caller of current_func:
          1. Find the call site where current_func is called
          2. Get the argument at param_position
          3. Check if that argument has the required evidence (null_check, bounds, non_zero)
          4. If not, recurse into the caller (depth-limited)
        """
        if depth >= self.max_trace_depth:
            return None

        callers = self.index.get_callers_of(current_func.name)
        if not callers:
            return None

        for caller_func in callers[:5]:  # Limit to 5 callers to avoid explosion
            # Find call sites where current_func is called
            sites = caller_func.call_sites.get(current_func.name, [])
            for site in sites[:3]:  # Limit sites per caller
                # Get argument at param_position
                if param_position >= len(site.arguments):
                    continue
                arg_expr = site.arguments[param_position].strip()

                # Extract simple variable name from argument
                arg_match = re.match(r"(\w+)", arg_expr)
                if not arg_match:
                    continue
                arg_name = arg_match.group(1)

                if check_type == "null_check":
                    # Check if arg is null-checked in caller
                    if arg_name in caller_func.null_checks:
                        line = caller_func.null_checks[arg_name]
                        return SymbolEvidence(
                            symbol_name=param_name,
                            evidence_type="null_check",
                            status="VALIDATED",
                            source_func=caller_func.name,
                            source_line=line,
                            detail=f"NULL-checked in caller {caller_func.name}() at L{line}",
                            depth=depth + 1,
                            confidence="MEDIUM" if depth == 0 else "LOW",
                        )

                elif check_type == "non_zero":
                    if arg_name in caller_func.nonzero_checks:
                        return SymbolEvidence(
                            symbol_name=param_name,
                            evidence_type="non_zero",
                            status="GUARANTEED_NON_ZERO",
                            source_func=caller_func.name,
                            detail=f"Zero-checked in caller {caller_func.name}()",
                            depth=depth + 1,
                            confidence="MEDIUM" if depth == 0 else "LOW",
                        )

                elif check_type == "bounds":
                    if arg_name in caller_func.bounds_checks:
                        op, limit = caller_func.bounds_checks[arg_name]
                        return SymbolEvidence(
                            symbol_name=param_name,
                            evidence_type="bounds",
                            status="BOUNDED",
                            source_func=caller_func.name,
                            detail=f"Bounded in caller {caller_func.name}(): {arg_name} {op} {limit}",
                            depth=depth + 1,
                            confidence="MEDIUM" if depth == 0 else "LOW",
                        )

                # Recurse into caller if arg is also a parameter
                caller_param = self._find_param(arg_name, caller_func)
                if caller_param:
                    ev = self._trace_param_through_callers(
                        param_name, caller_param.position, caller_func,
                        check_type, depth + 1,
                    )
                    if ev:
                        return ev

        return None

    # ───────────────────────────────────────────────────────────────────────
    #  Helpers
    # ───────────────────────────────────────────────────────────────────────

    def _find_param(self, name: str, func_def: FunctionDef) -> Optional[ParamInfo]:
        """Find a parameter by name in a function definition."""
        for p in func_def.parameters:
            if p.name == name:
                return p
        return None

    def _resolve_value(self, expr: str) -> Optional[str]:
        """Try to resolve an expression to a concrete value."""
        expr = expr.strip()

        # Direct numeric literal
        try:
            val = int(expr, 0)
            return str(val)
        except ValueError:
            pass

        # Macro lookup
        mv = self.index.macro_values.get(expr)
        if mv and mv.is_numeric:
            return str(mv.numeric_value)

        # Enum member lookup
        for enum_name, er in self.index.enum_ranges.items():
            if expr in er.members:
                idx = er.members.index(expr)
                return str(idx)

        return None

    def get_cache_stats(self) -> Dict:
        """Return index statistics for debugging."""
        return {
            "functions": len(self.index.functions),
            "call_edges": sum(len(v) for v in self.index.call_graph.values()),
            "reverse_edges": sum(len(v) for v in self.index.reverse_call_graph.values()),
            "macros": len(self.index.macro_values),
            "enums": len(self.index.enum_ranges),
            "files_indexed": len(self.index.functions_by_file),
        }
