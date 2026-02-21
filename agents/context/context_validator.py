"""
Context Validator — Per-chunk pre-analysis that traces pointer validations,
array bounds, return-value checks, and chained dereferences to reduce
false positives in LLM-based code analysis.

Works heuristically via regex (no CCLS required).
When CCLS is available, enhances pointer tracing with call-hierarchy data.

Integrates into CodebaseLLMAgent: the compact validation summary is injected
into each chunk's prompt BEFORE the LLM sees the code.
"""

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  Constants & Patterns
# ═══════════════════════════════════════════════════════════════════════════════

# Dynamic allocation functions (pointer MUST be checked after these)
_ALLOC_FUNCS = {
    "malloc", "calloc", "realloc", "strdup", "strndup",
    "kmalloc", "kzalloc", "kcalloc", "kvmalloc", "kvzalloc",
    "devm_kzalloc", "devm_kmalloc", "devm_kcalloc",
    "vzalloc", "vmalloc", "dma_alloc_coherent",
    "kstrdup", "kasprintf", "krealloc",
    "qdf_mem_malloc", "qdf_mem_malloc_atomic",
}

# Regex: pointer dereference  ptr->field  or  *ptr
_DEREF_RE = re.compile(r'\b(\w+)\s*->')
_STAR_DEREF_RE = re.compile(r'\*\s*(\w+)\b')

# Regex: array access  arr[idx]
_ARRAY_ACCESS_RE = re.compile(r'\b(\w+)\s*\[\s*(\w+)\s*\]')

# Regex: function call  result = func(...)  or  func(...)
_FUNC_CALL_RE = re.compile(r'\b(\w+)\s*=\s*(\w+)\s*\(')
_VOID_CALL_RE = re.compile(r'\b(\w+)\s*\([^)]*\)\s*;')

# Regex: null check patterns
_NULL_CHECK_RE = re.compile(
    r'if\s*\(\s*!?\s*(\w+)\s*(?:==|!=)\s*(?:NULL|0|nullptr)\s*\)|'
    r'if\s*\(\s*!(\w+)\s*\)|'
    r'if\s*\(\s*(\w+)\s*\)',
    re.IGNORECASE,
)

# Regex: comparison / bounds check  if (idx < MAX)
_BOUNDS_CHECK_RE = re.compile(
    r'(?:if|while|for)\s*\([^)]*\b(\w+)\s*(<|<=|>|>=)\s*(\w+)',
)

# Regex: for-loop bound  for (type i = 0; i < LIMIT; ...)
_FOR_LOOP_RE = re.compile(
    r'for\s*\([^;]*;\s*(\w+)\s*(<|<=)\s*(\w+)\s*;',
)

# Regex: modulo bound  idx % SIZE
_MODULO_RE = re.compile(r'\b(\w+)\s*%\s*(\w+)\b')

# Regex: static function detection
_STATIC_FUNC_RE = re.compile(
    r'^\s*static\s+[\w*\s]+\s+(\w+)\s*\(([^)]*)\)\s*\{',
    re.MULTILINE,
)

# Regex: allocation assignment  ptr = malloc(...)
_ALLOC_ASSIGN_RE = re.compile(
    r'\b(\w+)\s*=\s*(?:' + '|'.join(_ALLOC_FUNCS) + r')\s*\(',
)

# Regex: chained dereference  a->b->c
_CHAIN_RE = re.compile(r'\b(\w+)(?:\s*->\s*\w+){2,}')

# C keywords to exclude from identifier extraction
_C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "inline", "int", "long", "register", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned",
    "void", "volatile", "while", "NULL", "nullptr", "true", "false",
    "bool", "size_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "int8_t", "int16_t", "int32_t", "int64_t",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    symbol_name: str
    issue_type: str        # null_deref, bounds_check, return_check, chained_deref
    status: str            # VALIDATED, NOT_CHECKED, LOCALLY_ALLOCATED, BOUNDED
    confidence: str        # HIGH, MEDIUM, LOW
    location: str          # Where the validation happens
    reasoning: str
    recommendation: str    # IGNORE or FLAG

    def compact_str(self) -> str:
        """Single-line summary for prompt injection."""
        return f"{self.symbol_name:16s} -> {self.status} ({self.location})"


@dataclass
class ValidationReport:
    file_path: str
    start_line: int
    validations: Dict[str, ValidationResult] = field(default_factory=dict)

    def format_summary(self, max_chars: int = 800) -> str:
        """Format as compact C-comment block for prompt injection."""
        if not self.validations:
            return ""

        lines = [
            "// ── CONTEXT VALIDATION (pre-analysis) ──────────────",
        ]

        # Group by issue type
        ptrs = [v for v in self.validations.values() if v.issue_type == "null_deref"]
        bounds = [v for v in self.validations.values() if v.issue_type == "bounds_check"]
        returns = [v for v in self.validations.values() if v.issue_type == "return_check"]
        chains = [v for v in self.validations.values() if v.issue_type == "chained_deref"]

        if ptrs:
            lines.append("// Pointers:")
            for v in ptrs[:8]:
                flag = " — FLAG if unchecked" if v.recommendation == "FLAG" else ""
                lines.append(f"//   {v.symbol_name:16s} -> {v.status} ({v.location}){flag}")

        if bounds:
            lines.append("// Array Bounds:")
            for v in bounds[:8]:
                lines.append(f"//   {v.symbol_name:16s} -> {v.status} ({v.location})")

        if returns:
            lines.append("// Return Values:")
            for v in returns[:5]:
                flag = " — FLAG if unchecked" if v.recommendation == "FLAG" else ""
                lines.append(f"//   {v.symbol_name:16s} -> {v.status} ({v.location}){flag}")

        if chains:
            lines.append("// Chained Derefs:")
            for v in chains[:5]:
                lines.append(f"//   {v.symbol_name:16s} -> {v.status} ({v.location})")

        lines.append("// ── END VALIDATION ─────────────────────────────────")

        result = "\n".join(lines)
        if len(result) > max_chars:
            # Truncate by removing entries from the end
            result = result[:max_chars].rsplit("\n", 1)[0]
            result += "\n// ... (truncated)\n// ── END VALIDATION ─────────────────────────────────"
        return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Validators
# ═══════════════════════════════════════════════════════════════════════════════

class PointerValidator:
    """Traces whether a pointer has been null-checked."""

    def __init__(self, file_content: str = ""):
        self.file_content = file_content

    def trace(
        self,
        ptr_name: str,
        chunk_text: str,
        chunk_start_line: int,
    ) -> ValidationResult:
        """Determine if ptr_name has been validated before use in chunk."""

        # 1. Local allocation — MUST be checked
        if self._is_locally_allocated(ptr_name, chunk_text):
            # Check if there's a null check after allocation
            if self._has_null_check(ptr_name, chunk_text):
                return ValidationResult(
                    symbol_name=ptr_name, issue_type="null_deref",
                    status="VALIDATED", confidence="HIGH",
                    location=f"null-checked after allocation in chunk",
                    reasoning="Allocated locally and checked before use",
                    recommendation="IGNORE",
                )
            return ValidationResult(
                symbol_name=ptr_name, issue_type="null_deref",
                status="LOCALLY_ALLOCATED", confidence="HIGH",
                location=f"allocated in chunk (line ~{chunk_start_line}+)",
                reasoning="Dynamic allocation in current scope without null check",
                recommendation="FLAG",
            )

        # 2. Null check in current chunk
        if self._has_null_check(ptr_name, chunk_text):
            return ValidationResult(
                symbol_name=ptr_name, issue_type="null_deref",
                status="VALIDATED", confidence="HIGH",
                location="null-checked in current chunk",
                reasoning="Explicit null check found before dereference",
                recommendation="IGNORE",
            )

        # 3. Static function parameter — upstream responsibility
        func_info = self._get_enclosing_function(chunk_text)
        if func_info:
            func_name, params, is_static = func_info
            if is_static and ptr_name in params:
                return ValidationResult(
                    symbol_name=ptr_name, issue_type="null_deref",
                    status="VALIDATED", confidence="MEDIUM",
                    location=f"param of static {func_name}() — caller validates",
                    reasoning="Static/internal function; caller responsible for validation",
                    recommendation="IGNORE",
                )

        # 4. Check earlier in file (before chunk)
        if self.file_content and self._has_null_check_in_file(ptr_name, chunk_start_line):
            return ValidationResult(
                symbol_name=ptr_name, issue_type="null_deref",
                status="VALIDATED", confidence="MEDIUM",
                location="null-checked earlier in file",
                reasoning="Null check found in preceding code",
                recommendation="IGNORE",
            )

        # 5. Default — not checked
        return ValidationResult(
            symbol_name=ptr_name, issue_type="null_deref",
            status="NOT_CHECKED", confidence="LOW",
            location="no validation found (heuristic)",
            reasoning="No null check detected in accessible scope",
            recommendation="FLAG",
        )

    def _is_locally_allocated(self, ptr_name: str, chunk: str) -> bool:
        """Check if ptr is assigned from a dynamic allocation."""
        pattern = re.compile(
            rf'\b{re.escape(ptr_name)}\s*=\s*(?:' +
            '|'.join(re.escape(f) for f in _ALLOC_FUNCS) +
            r')\s*\(', re.MULTILINE
        )
        return bool(pattern.search(chunk))

    def _has_null_check(self, ptr_name: str, chunk: str) -> bool:
        """Check if there's a null check for ptr in the chunk."""
        escaped = re.escape(ptr_name)
        patterns = [
            rf'if\s*\(\s*!{escaped}\s*\)',
            rf'if\s*\(\s*{escaped}\s*==\s*(?:NULL|0|nullptr)\s*\)',
            rf'if\s*\(\s*{escaped}\s*!=\s*(?:NULL|0|nullptr)\s*\)',
            rf'if\s*\(\s*{escaped}\s*\)',
            rf'{escaped}\s*&&\s*{escaped}\s*->',
            rf'(?:unlikely|likely)\s*\(\s*!{escaped}\s*\)',
        ]
        combined = "|".join(patterns)
        return bool(re.search(combined, chunk, re.IGNORECASE))

    def _has_null_check_in_file(self, ptr_name: str, before_line: int) -> bool:
        """Check if null check exists earlier in the file."""
        lines = self.file_content.split("\n")
        # Look in preceding 50 lines of the function
        start = max(0, before_line - 50)
        preceding = "\n".join(lines[start:before_line])
        return self._has_null_check(ptr_name, preceding)

    def _get_enclosing_function(self, chunk: str) -> Optional[Tuple[str, str, bool]]:
        """Extract enclosing function info: (name, params, is_static)."""
        match = _STATIC_FUNC_RE.search(chunk)
        if match:
            return (match.group(1), match.group(2), True)
        # Also check non-static
        non_static = re.search(
            r'^\s*(?!static\b)([\w*\s]+)\s+(\w+)\s*\(([^)]*)\)\s*\{',
            chunk, re.MULTILINE
        )
        if non_static:
            return (non_static.group(2), non_static.group(3), False)
        return None


class IndexValidator:
    """Traces whether an array index is bounded."""

    def __init__(self, file_content: str = ""):
        self.file_content = file_content

    def trace(
        self,
        idx_name: str,
        array_name: str,
        chunk_text: str,
    ) -> ValidationResult:
        """Determine if idx_name is bounded when used as array[idx]."""

        # 1. Loop bound check: for (i = 0; i < LIMIT; i++)
        for m in _FOR_LOOP_RE.finditer(chunk_text):
            loop_var, op, limit = m.groups()
            if loop_var == idx_name:
                return ValidationResult(
                    symbol_name=idx_name, issue_type="bounds_check",
                    status="BOUNDED", confidence="HIGH",
                    location=f"loop bound: {loop_var} {op} {limit}",
                    reasoning="Index is bounded by for-loop condition",
                    recommendation="IGNORE",
                )

        # 2. Explicit comparison: if (idx < MAX)
        for m in _BOUNDS_CHECK_RE.finditer(chunk_text):
            var, op, limit = m.groups()
            if var == idx_name:
                return ValidationResult(
                    symbol_name=idx_name, issue_type="bounds_check",
                    status="BOUNDED", confidence="HIGH",
                    location=f"comparison: {var} {op} {limit}",
                    reasoning="Index is compared against a limit before access",
                    recommendation="IGNORE",
                )

        # 3. Modulo bound: idx % SIZE
        for m in _MODULO_RE.finditer(chunk_text):
            var, divisor = m.groups()
            if var == idx_name:
                return ValidationResult(
                    symbol_name=idx_name, issue_type="bounds_check",
                    status="BOUNDED", confidence="HIGH",
                    location=f"modulo: {var} % {divisor}",
                    reasoning="Index is bounded by modulo operation",
                    recommendation="IGNORE",
                )

        # 4. Check file-level for enum type hint
        if self.file_content:
            enum_match = re.search(
                rf'enum\s+(\w+)\s+{re.escape(idx_name)}\b',
                self.file_content,
            )
            if enum_match:
                return ValidationResult(
                    symbol_name=idx_name, issue_type="bounds_check",
                    status="BOUNDED", confidence="MEDIUM",
                    location=f"enum type: {enum_match.group(1)}",
                    reasoning="Index is of enum type (compiler-bounded)",
                    recommendation="IGNORE",
                )

        # 5. Default — not checked
        return ValidationResult(
            symbol_name=idx_name, issue_type="bounds_check",
            status="NOT_CHECKED", confidence="LOW",
            location="no bounds check found (heuristic)",
            reasoning="No comparison/loop/modulo found bounding this index",
            recommendation="FLAG",
        )


class ReturnValueValidator:
    """Traces whether a function return value is checked."""

    def trace(
        self,
        var_name: str,
        func_name: str,
        chunk_text: str,
    ) -> ValidationResult:
        """Check if the return value of func_name (stored in var_name) is validated."""

        escaped = re.escape(var_name)

        # 1. Immediate null/error check after assignment
        patterns = [
            rf'{escaped}\s*=\s*{re.escape(func_name)}\s*\([^)]*\)\s*;[^{{]*?if\s*\(\s*!?{escaped}\b',
            rf'if\s*\(\s*!{escaped}\s*\)',
            rf'if\s*\(\s*{escaped}\s*==\s*(?:NULL|0|nullptr)\s*\)',
            rf'if\s*\(\s*{escaped}\s*!=\s*(?:NULL|0|nullptr)\s*\)',
            rf'if\s*\(\s*{escaped}\s*<\s*0\s*\)',
            rf'{escaped}\s*&&\s*{escaped}\s*->',
        ]
        for pat in patterns:
            if re.search(pat, chunk_text, re.DOTALL | re.IGNORECASE):
                return ValidationResult(
                    symbol_name=f"{func_name}()", issue_type="return_check",
                    status="VALIDATED", confidence="HIGH",
                    location=f"return checked for {var_name}",
                    reasoning="Return value is validated before use",
                    recommendation="IGNORE",
                )

        # 2. Guard pattern: ptr = func(); if (!ptr) return;
        guard_re = re.compile(
            rf'{escaped}\s*=\s*{re.escape(func_name)}\s*\([^)]*\)\s*;\s*'
            rf'if\s*\(\s*[!]?{escaped}',
            re.DOTALL,
        )
        if guard_re.search(chunk_text):
            return ValidationResult(
                symbol_name=f"{func_name}()", issue_type="return_check",
                status="VALIDATED", confidence="HIGH",
                location=f"guard pattern for {var_name}",
                reasoning="Guard check found immediately after call",
                recommendation="IGNORE",
            )

        # 3. Default — not checked
        return ValidationResult(
            symbol_name=f"{func_name}()", issue_type="return_check",
            status="NOT_CHECKED", confidence="MEDIUM",
            location="no return check found",
            reasoning="Return value used without validation",
            recommendation="FLAG",
        )


class ChainedDerefValidator:
    """Validates pointer chains like a->b->c->d."""

    def trace(
        self,
        chain_root: str,
        full_chain: str,
        chunk_text: str,
        ptr_validator: PointerValidator,
        chunk_start_line: int,
    ) -> ValidationResult:
        """If root is validated, entire chain is safe."""

        root_result = ptr_validator.trace(chain_root, chunk_text, chunk_start_line)

        if root_result.status == "VALIDATED":
            return ValidationResult(
                symbol_name=full_chain, issue_type="chained_deref",
                status="VALIDATED", confidence=root_result.confidence,
                location=f"root `{chain_root}` validated — chain inherits",
                reasoning="Root pointer validated; chained members inherit safety",
                recommendation="IGNORE",
            )

        return ValidationResult(
            symbol_name=full_chain, issue_type="chained_deref",
            status="NOT_CHECKED", confidence="LOW",
            location=f"root `{chain_root}` not validated",
            reasoning="Root pointer not validated; chain dereference may be unsafe",
            recommendation="FLAG",
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  ContextValidator — Main Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

class ContextValidator:
    """
    Per-chunk context validation that traces pointers, indices, return values,
    and dereference chains to pre-label them before LLM analysis.

    Operates in heuristic mode by default. When CCLS is available,
    uses call-hierarchy data for upstream pointer tracing.
    """

    def __init__(
        self,
        codebase_path: str,
        use_ccls: bool = False,
        ccls_navigator: Optional[object] = None,
    ):
        self.codebase_path = Path(codebase_path).resolve()
        self.use_ccls = use_ccls and ccls_navigator is not None
        self.ccls_navigator = ccls_navigator
        self._file_cache: Dict[str, str] = {}

    def _read_file(self, file_path: str) -> str:
        """Read file content with caching."""
        if file_path in self._file_cache:
            return self._file_cache[file_path]
        try:
            abs_path = Path(file_path)
            if not abs_path.is_absolute():
                abs_path = self.codebase_path / file_path
            content = abs_path.read_text(encoding="utf-8", errors="replace")
            self._file_cache[file_path] = content
            return content
        except Exception:
            return ""

    def analyze_chunk(
        self,
        chunk_text: str,
        file_path: str,
        file_content: str,
        start_line: int,
    ) -> ValidationReport:
        """
        Analyze a code chunk and return validation findings.

        Args:
            chunk_text: The code chunk to analyze
            file_path: Path to the source file (relative or absolute)
            file_content: Full file content (for backward-looking analysis)
            start_line: Starting line number of the chunk in the file

        Returns:
            ValidationReport with per-symbol validation results
        """
        report = ValidationReport(file_path=file_path, start_line=start_line)

        ptr_validator = PointerValidator(file_content)
        idx_validator = IndexValidator(file_content)
        ret_validator = ReturnValueValidator()
        chain_validator = ChainedDerefValidator()

        # ── Extract potential issues from chunk ──

        # 1. Pointer dereferences: ptr->field
        deref_ptrs: Set[str] = set()
        for m in _DEREF_RE.finditer(chunk_text):
            ptr = m.group(1)
            if ptr not in _C_KEYWORDS and ptr not in deref_ptrs:
                deref_ptrs.add(ptr)

        # 2. Star dereferences: *ptr
        for m in _STAR_DEREF_RE.finditer(chunk_text):
            ptr = m.group(1)
            if ptr not in _C_KEYWORDS:
                deref_ptrs.add(ptr)

        # 3. Array accesses: arr[idx]
        array_accesses: List[Tuple[str, str]] = []
        for m in _ARRAY_ACCESS_RE.finditer(chunk_text):
            arr, idx = m.group(1), m.group(2)
            if arr not in _C_KEYWORDS and idx not in _C_KEYWORDS:
                array_accesses.append((arr, idx))

        # 4. Function call return values: var = func(...)
        func_returns: List[Tuple[str, str]] = []
        for m in _FUNC_CALL_RE.finditer(chunk_text):
            var, func = m.group(1), m.group(2)
            if var not in _C_KEYWORDS and func not in _C_KEYWORDS:
                func_returns.append((var, func))

        # 5. Chained dereferences: a->b->c
        chain_roots: Dict[str, str] = {}
        for m in _CHAIN_RE.finditer(chunk_text):
            full = m.group(0)
            root = m.group(1)
            if root not in _C_KEYWORDS:
                chain_roots[root] = full

        # ── Validate each potential issue ──

        # Pointer validations (limit to avoid bloat)
        for ptr in list(deref_ptrs)[:10]:
            key = f"ptr:{ptr}"
            if key not in report.validations:
                result = ptr_validator.trace(ptr, chunk_text, start_line)
                report.validations[key] = result

        # Array bounds validations
        seen_indices: Set[str] = set()
        for arr, idx in array_accesses[:8]:
            if idx in seen_indices:
                continue
            seen_indices.add(idx)
            key = f"idx:{idx}"
            if key not in report.validations:
                result = idx_validator.trace(idx, arr, chunk_text)
                report.validations[key] = result

        # Return value validations
        for var, func in func_returns[:5]:
            key = f"ret:{func}"
            if key not in report.validations:
                result = ret_validator.trace(var, func, chunk_text)
                report.validations[key] = result

        # Chained dereference validations
        for root, chain in list(chain_roots.items())[:5]:
            key = f"chain:{root}"
            if key not in report.validations:
                # Skip if already validated as a pointer
                ptr_key = f"ptr:{root}"
                if ptr_key in report.validations and report.validations[ptr_key].status == "VALIDATED":
                    result = ValidationResult(
                        symbol_name=chain, issue_type="chained_deref",
                        status="VALIDATED", confidence=report.validations[ptr_key].confidence,
                        location=f"root `{root}` validated — chain inherits",
                        reasoning="Root pointer validated; chained members inherit safety",
                        recommendation="IGNORE",
                    )
                else:
                    result = chain_validator.trace(
                        root, chain, chunk_text, ptr_validator, start_line
                    )
                report.validations[key] = result

        # Log summary
        total = len(report.validations)
        validated = sum(1 for v in report.validations.values() if v.status in ("VALIDATED", "BOUNDED"))
        logger.debug(
            f"Context validation for {file_path} lines {start_line}+: "
            f"{validated}/{total} symbols pre-validated"
        )

        return report
