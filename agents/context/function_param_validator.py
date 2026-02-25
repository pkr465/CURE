"""
Function Parameter Validation Context Builder.

Analyzes C/C++ code chunks to determine whether function parameters have
been validated (null checks, bounds checks, enum range checks, struct field
validation).  Produces a compact C-comment block that can be injected into
LLM prompts so the model knows which params are already guarded.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════════

_C_KEYWORDS = frozenset({
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "inline", "int", "long", "register", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned",
    "void", "volatile", "while", "bool", "true", "false", "NULL", "nullptr",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t", "int8_t", "int16_t",
    "int32_t", "int64_t", "size_t", "ssize_t", "ptrdiff_t",
})

# ═══════════════════════════════════════════════════════════════════════════════
#  Regex Patterns
# ═══════════════════════════════════════════════════════════════════════════════

# Function definition (same as static_call_stack_analyzer)
_FUNC_DEF_RE = re.compile(
    r"^[ \t]*"
    r"((?:static\s+|inline\s+|__\w+\s*\([^)]*\)\s+)*"
    r"(?:(?:const\s+|volatile\s+|unsigned\s+|signed\s+|long\s+|short\s+)*"
    r"[\w*]+(?:\s*\*)*)\s+)"
    r"(\w+)"
    r"\s*\(([^)]*)\)"
    r"\s*\{",
    re.MULTILINE,
)

# Null-check patterns
_NULL_CHECK_RE = re.compile(
    r"if\s*\(\s*!?\s*(\w+)\s*(?:==|!=)\s*(?:NULL|nullptr|0)\s*\)"
)
_NULL_CHECK_SHORT_RE = re.compile(r"if\s*\(\s*!(\w+)\s*\)")

# Bounds-check: if (var op LIMIT)
_BOUNDS_CHECK_RE = re.compile(
    r"if\s*\(\s*(\w+)\s*(<|<=|>=|>)\s*(\w+)\s*\)"
)

# For-loop bounds: for (...; var op limit; ...)
_FOR_LOOP_RE = re.compile(
    r"for\s*\([^;]*;\s*(\w+)\s*(<|<=)\s*(\w+)\s*;"
)

# Switch statement on a variable
_SWITCH_RE = re.compile(r"switch\s*\(\s*(\w+)\s*\)")

# Struct field access: param->field or param.field
_FIELD_ACCESS_RE = re.compile(r"\b(\w+)\s*(?:->|\.)\s*(\w+)")


# ═══════════════════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ParamValidation:
    """Validation status for a single function parameter."""
    param_name: str
    param_type: str
    is_pointer: bool
    checks_found: List[str] = field(default_factory=list)
    status: str = "NOT_CHECKED"  # VALIDATED | NOT_CHECKED | CALLER_CHECKED


@dataclass
class ParamValidationReport:
    """Validation report for one function's parameters."""
    function_name: str
    return_type: str
    params: List[ParamValidation] = field(default_factory=list)

    def format_summary(self, max_chars: int = 2000) -> str:
        """Format as a C-comment block for LLM injection."""
        if not self.params:
            return ""

        lines = ["// ── FUNCTION PARAMETER VALIDATION ──"]
        sig_params = ", ".join(
            f"{p.param_type} {p.param_name}" for p in self.params
        )
        lines.append(f"// {self.return_type} {self.function_name}({sig_params}):")

        for p in self.params:
            check_str = ", ".join(p.checks_found) if p.checks_found else "none"
            lines.append(f"//   {p.param_name} ({p.param_type}): {p.status} [{check_str}]")

        lines.append("// ── END PARAMETER VALIDATION ──")
        result = "\n".join(lines)
        if len(result) > max_chars:
            result = result[:max_chars - 4] + "\n// …"
        return result


# ═══════════════════════════════════════════════════════════════════════════════
#  FunctionParamValidator
# ═══════════════════════════════════════════════════════════════════════════════

class FunctionParamValidator:
    """
    Analyzes C/C++ code chunks and produces per-function parameter validation
    context.  Designed to be instantiated once and called per-chunk.
    """

    def __init__(self, codebase_path: str):
        self.codebase_path = codebase_path

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def analyze_chunk(
        self,
        chunk_text: str,
        file_path: str,
        file_content: str,
        start_line: int,
    ) -> List[ParamValidationReport]:
        """
        Analyze all function definitions found in *chunk_text* and return
        a list of ParamValidationReport objects (one per function).

        Parameters
        ----------
        chunk_text : str
            The code chunk to analyze (lines as-is, no line numbers).
        file_path : str
            Path to the source file (for logging).
        file_content : str
            Full file content — used to look up caller-side validation.
        start_line : int
            1-based line number of the first line of chunk_text in the file.
        """
        reports: List[ParamValidationReport] = []

        for match in _FUNC_DEF_RE.finditer(chunk_text):
            ret_type = match.group(1).strip()
            func_name = match.group(2)
            params_str = match.group(3)

            if func_name in _C_KEYWORDS:
                continue

            params = self._parse_parameters(params_str)
            if not params:
                continue

            # Extract the function body (text after the opening brace)
            body_start = match.end()
            body = self._extract_body(chunk_text, body_start)

            report = ParamValidationReport(
                function_name=func_name,
                return_type=ret_type,
            )

            for p_name, p_type, p_is_ptr in params:
                pv = ParamValidation(
                    param_name=p_name,
                    param_type=p_type,
                    is_pointer=p_is_ptr,
                )

                checks: List[str] = []

                # 1) Pointer null checks
                if p_is_ptr:
                    null_line = self._find_null_check(p_name, body, start_line + match.start())
                    if null_line:
                        checks.append(f"NULL_CHECKED at line {null_line}")

                # 2) Array/index bounds checks
                bounds_info = self._find_bounds_check(p_name, body, start_line + match.start())
                if bounds_info:
                    checks.append(f"BOUNDS_CHECKED ({bounds_info})")

                # 3) Enum/switch checks
                if self._find_switch_check(p_name, body):
                    checks.append("SWITCH_CHECKED")

                # 4) Struct field access validation
                if p_is_ptr:
                    field_checks = self._find_field_validation(p_name, body)
                    if field_checks:
                        checks.append(f"FIELDS_ACCESSED: {', '.join(field_checks[:5])}")

                # 5) Caller-side checks (search file_content for calls to this func)
                if not checks:
                    caller_check = self._find_caller_validation(
                        func_name, p_name, params, file_content
                    )
                    if caller_check:
                        checks.append("CALLER_CHECKED")
                        pv.status = "CALLER_CHECKED"

                if checks and pv.status != "CALLER_CHECKED":
                    pv.status = "VALIDATED"
                pv.checks_found = checks
                report.params.append(pv)

            reports.append(report)

        return reports

    def format_reports(
        self,
        reports: List[ParamValidationReport],
        max_chars: int = 3000,
    ) -> str:
        """Combine multiple reports into a single context block."""
        if not reports:
            return ""
        parts = []
        budget = max_chars
        for r in reports:
            s = r.format_summary(max_chars=budget)
            if s:
                parts.append(s)
                budget -= len(s) + 1
                if budget <= 100:
                    break
        return "\n".join(parts)

    # ------------------------------------------------------------------
    #  Parameter parsing (mirrors StaticCallStackAnalyzer._parse_parameters)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_parameters(params_str: str) -> List[Tuple[str, str, bool]]:
        """Parse C parameter list into (name, type, is_pointer) tuples."""
        if not params_str or params_str.strip() == "void":
            return []

        result = []
        for part in params_str.split(","):
            part = part.strip()
            if not part:
                continue

            is_pointer = "*" in part
            tokens = part.replace("*", " * ").split()
            if len(tokens) >= 2:
                name = tokens[-1].strip("*").strip()
                type_name = " ".join(tokens[:-1]).replace(" * ", "*").strip()
            elif len(tokens) == 1:
                name = tokens[0]
                type_name = "unknown"
            else:
                continue

            name = name.strip("*").strip()
            if name and name not in _C_KEYWORDS:
                result.append((name, type_name, is_pointer))

        return result

    # ------------------------------------------------------------------
    #  Body extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_body(text: str, start: int) -> str:
        """Extract function body from opening brace to matching close brace."""
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        return text[start:i]

    # ------------------------------------------------------------------
    #  Check finders
    # ------------------------------------------------------------------

    @staticmethod
    def _find_null_check(
        param_name: str, body: str, base_line: int
    ) -> Optional[int]:
        """Find a null check for param_name in body. Returns line number or None."""
        for i, line in enumerate(body.split("\n")):
            for m in _NULL_CHECK_RE.finditer(line):
                if m.group(1) == param_name:
                    return base_line + i
            for m in _NULL_CHECK_SHORT_RE.finditer(line):
                if m.group(1) == param_name:
                    return base_line + i
        return None

    @staticmethod
    def _find_bounds_check(
        param_name: str, body: str, base_line: int
    ) -> Optional[str]:
        """Find bounds comparison for param_name. Returns description or None."""
        for m in _BOUNDS_CHECK_RE.finditer(body):
            if m.group(1) == param_name:
                return f"{param_name} {m.group(2)} {m.group(3)}"
            if m.group(3) == param_name:
                return f"{m.group(1)} {m.group(2)} {param_name}"
        for m in _FOR_LOOP_RE.finditer(body):
            if m.group(1) == param_name:
                return f"{param_name} {m.group(2)} {m.group(3)} (loop)"
            if m.group(3) == param_name:
                return f"{m.group(1)} {m.group(2)} {param_name} (loop)"
        return None

    @staticmethod
    def _find_switch_check(param_name: str, body: str) -> bool:
        """Check if param_name is used in a switch statement."""
        for m in _SWITCH_RE.finditer(body):
            if m.group(1) == param_name:
                return True
        return False

    @staticmethod
    def _find_field_validation(param_name: str, body: str) -> List[str]:
        """Find struct fields accessed via param_name (->field or .field)."""
        fields = set()
        for m in _FIELD_ACCESS_RE.finditer(body):
            if m.group(1) == param_name:
                fields.add(m.group(2))
        return sorted(fields)

    @staticmethod
    def _find_caller_validation(
        func_name: str,
        param_name: str,
        all_params: List[Tuple[str, str, bool]],
        file_content: str,
    ) -> bool:
        """
        Search file_content for call sites of func_name and check if
        the argument at param_name's position is null-checked before the call.
        """
        # Find position of param_name
        pos = None
        for idx, (pn, _, _) in enumerate(all_params):
            if pn == param_name:
                pos = idx
                break
        if pos is None:
            return False

        # Look for calls to func_name
        call_re = re.compile(
            rf"\b{re.escape(func_name)}\s*\(([^)]*)\)"
        )
        for call_match in call_re.finditer(file_content):
            args = [a.strip() for a in call_match.group(1).split(",")]
            if pos < len(args):
                arg_name = args[pos].strip("& ").strip()
                # Check if there's a null check for this arg before the call
                preceding = file_content[:call_match.start()]
                last_lines = preceding[-500:]  # look at last ~500 chars
                if _NULL_CHECK_RE.search(last_lines):
                    for m in _NULL_CHECK_RE.finditer(last_lines):
                        if m.group(1) == arg_name:
                            return True
                if _NULL_CHECK_SHORT_RE.search(last_lines):
                    for m in _NULL_CHECK_SHORT_RE.finditer(last_lines):
                        if m.group(1) == arg_name:
                            return True
        return False
