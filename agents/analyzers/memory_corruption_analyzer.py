import re
from typing import Dict, List, Any
from .base_runtime_analyzer import RuntimeAnalyzerBase

class MemoryCorruptionAnalyzer(RuntimeAnalyzerBase):
    """
    Comprehensive Memory Safety Analyzer.
    
    Capabilities:
    1. Use-After-Free (UAF).
    2. Double Free.
    3. Realloc Leak.
    4. Format String Vulnerabilities.
    5. Stack Buffer Overflow (Heuristic).
    """
    
    _FREE = re.compile(r"\b(free|delete|delete\[\])\s*\(\s*([a-zA-Z0-9_>.]+)\s*\)")
    _REALLOC_UNSAFE = re.compile(r"\b([a-zA-Z0-9_]+)\s*=\s*realloc\s*\(\s*\1\s*,")
    _PRINTF_UNSAFE = re.compile(r"\b(printf|sprintf|fprintf)\s*\(\s*(?!\"|\w+\s*\()([a-zA-Z0-9_>.]+)\s*[,)]")
    _FIXED_BUFFER = re.compile(r"\bchar\s+([a-zA-Z0-9_]+)\s*\[\s*\d+\s*\]\s*;")
    _STRCPY_UNSAFE = re.compile(r"\b(strcpy|strcat|sprintf)\s*\(\s*([a-zA-Z0-9_]+)\s*,")

    def analyze(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        issues = []
        metrics = []

        for entry in file_cache:
            if entry.get("suffix", "").lower() not in {".c", ".cpp", ".cc"}:
                continue

            rel_path = entry.get("rel_path", "unknown")
            source = entry.get("source", "")
            
            result = self.analyze_single_file(source, rel_path)
            issues.extend(result["issues"])
            metrics.append(result["metrics"])

        return {"metrics": metrics, "issues": issues}

    def analyze_single_file(self, source: str, rel_path: str) -> Dict[str, Any]:
        local_issues = []
        file_risk_count = 0
        
        # 1. Global/Line-based checks
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if self._REALLOC_UNSAFE.search(line):
                local_issues.append(f"{rel_path}:{i+1}: Unsafe realloc pattern detected 'p = realloc(p,...)'. Leaks on failure.")
                file_risk_count += 1
            
            m_fmt = self._PRINTF_UNSAFE.search(line)
            if m_fmt:
                local_issues.append(f"{rel_path}:{i+1}: Potential Format String Vulnerability. User input '{m_fmt.group(2)}' passed as format.")
                file_risk_count += 1

        # 2. Block-based checks
        func_blocks = self._get_function_blocks(source)
        for func_name, body, start_line in func_blocks:
            
            # A. Lifecycle (UAF, Double Free)
            uaf_issues = self._check_lifecycle_issues(body, start_line)
            for issue in uaf_issues:
                local_issues.append(f"{rel_path}:{issue}")
                file_risk_count += 1
            
            # B. Stack Overflow
            overflow_issues = self._check_stack_overflow(body, start_line)
            for issue in overflow_issues:
                local_issues.append(f"{rel_path}:{issue}")
                file_risk_count += 1

        return {
            "issues": local_issues,
            "metrics": {
                "file": rel_path,
                "memory_issues": file_risk_count
            }
        }

    def _check_lifecycle_issues(self, body: str, start_line: int) -> List[str]:
        results = []
        lines = body.splitlines()
        freed_vars = {} # {var_name: line_number}

        for i, line in enumerate(lines):
            # Detect Free
            m_free = self._FREE.search(line)
            if m_free:
                var = m_free.group(2)
                current_line = start_line + i
                
                if var in freed_vars:
                    prev_line = freed_vars[var]
                    results.append(f"{current_line}: Double Free detected on '{var}'. Previously freed at line {prev_line}.")
                else:
                    freed_vars[var] = current_line
                continue
            
            # Detect Re-assignment
            for var in list(freed_vars.keys()):
                if re.search(rf"\b{re.escape(var)}\s*=", line):
                    del freed_vars[var]

            # Detect Usage (UAF)
            for var in freed_vars:
                if re.search(rf"\b{re.escape(var)}\b", line) and not line.strip().startswith("//"):
                    if "free" not in line and "delete" not in line:
                        results.append(f"{start_line+i}: Use-After-Free detected. Variable '{var}' used after being freed at line {freed_vars[var]}.")
        
        return results

    def _check_stack_overflow(self, body: str, start_line: int) -> List[str]:
        results = []
        lines = body.splitlines()
        declared_buffers = set()

        for i, line in enumerate(lines):
            m_decl = self._FIXED_BUFFER.search(line)
            if m_decl:
                declared_buffers.add(m_decl.group(1))
            
            m_cpy = self._STRCPY_UNSAFE.search(line)
            if m_cpy:
                target_var = m_cpy.group(2)
                func = m_cpy.group(1)
                if target_var in declared_buffers:
                    results.append(f"{start_line+i}: Potential Stack Buffer Overflow. Unsafe function '{func}' used on fixed-size buffer '{target_var}'.")
        
        return results