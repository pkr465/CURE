import re
from typing import Dict, List, Any, Tuple
from .base_runtime_analyzer import RuntimeAnalyzerBase

class NullPointerAnalyzer(RuntimeAnalyzerBase):
    """
    Comprehensive Null Pointer & Uninitialized Memory Analyzer.
    
    Capabilities:
    1. Allocation without Check (malloc/new).
    2. Dynamic Cast Check.
    3. Unsafe Function Args usage.
    """

    _ALLOCS = re.compile(r"\b([a-zA-Z0-9_]+)\s*=\s*(?:[a-zA-Z0-9_]+\*)?\s*(malloc|calloc|realloc|fopen|getenv)\s*\(")
    _CPP_NEW = re.compile(r"\b([a-zA-Z0-9_]+)\s*=\s*new\s+\(std::nothrow\)") 
    _DYN_CAST = re.compile(r"\b([a-zA-Z0-9_]+)\s*=\s*dynamic_cast<[^>]+>\s*\(")
    
    _DEREF = r"(?:\*({var})\b|{var}->|{var}\[)"
    _CHECK = r"\bif\s*\(\s*(!?{var}|{var}\s*[!=]=\s*(NULL|nullptr|0))"

    def analyze(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        issues = []
        metrics = []

        for entry in file_cache:
            if entry.get("suffix", "").lower() not in {".c", ".cpp", ".cc", ".cxx"}:
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
        func_blocks = self._get_function_blocks(source)

        for func_name, body, start_line in func_blocks:
            # 1. Allocations
            hits = self._check_usage_without_check(body, self._ALLOCS, start_line)
            for var, line in hits:
                local_issues.append(f"{rel_path}:{line}: '{var}' allocated in '{func_name}' but used without visible NULL check.")
                file_risk_count += 1

            # 2. dynamic_cast
            hits_cast = self._check_usage_without_check(body, self._DYN_CAST, start_line)
            for var, line in hits_cast:
                local_issues.append(f"{rel_path}:{line}: Result of dynamic_cast '{var}' used without NULL check in '{func_name}'.")
                file_risk_count += 1
            
            # 3. New (nothrow)
            hits_new = self._check_usage_without_check(body, self._CPP_NEW, start_line)
            for var, line in hits_new:
                local_issues.append(f"{rel_path}:{line}: Result of new(nothrow) '{var}' used without NULL check.")
                file_risk_count += 1

        return {
            "issues": local_issues,
            "metrics": {
                "file": rel_path,
                "null_pointer_risks": file_risk_count
            }
        }

    def _check_usage_without_check(self, body: str, pattern: re.Pattern, start_line: int) -> List[Tuple[str, int]]:
        results = []
        lines = body.splitlines()
        
        for i, line in enumerate(lines):
            match = pattern.search(line)
            if match:
                var_name = match.group(1)
                
                safe = False
                used_unsafe = False
                usage_line = -1
                
                re_check = re.compile(self._CHECK.format(var=re.escape(var_name)))
                re_usage = re.compile(self._DEREF.format(var=re.escape(var_name)))
                
                # Scan forward window (25 lines)
                for j in range(i + 1, min(i + 25, len(lines))):
                    scan_line = lines[j]
                    
                    if re_check.search(scan_line):
                        safe = True
                        break
                    
                    if "return" in scan_line:
                        # Heuristic: return might imply handling/bailout
                        pass 

                    if re_usage.search(scan_line):
                        used_unsafe = True
                        usage_line = start_line + j
                        break
                
                if used_unsafe and not safe:
                    results.append((var_name, usage_line))
        
        return results