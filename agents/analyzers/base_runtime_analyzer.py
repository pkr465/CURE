import re
from typing import List, Tuple, Dict, Any, Optional

class RuntimeAnalyzerBase:
    """
    Base class for runtime analyzers providing shared utilities for
    parsing function blocks and standardizing the analysis interface.
    """
    def __init__(self, debug: bool = False):
        self.debug = debug

    def analyze(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Main entry point for batch analysis of a codebase.
        """
        raise NotImplementedError("Subclasses must implement analyze()")

    def analyze_single_file(self, source: str, rel_path: str) -> Dict[str, Any]:
        """
        Analyze a single file's content in isolation.
        Returns a dictionary containing 'issues' (list) and 'metrics' (dict).
        """
        raise NotImplementedError("Subclasses must implement analyze_single_file()")

    def _get_function_blocks(self, source: str) -> List[Tuple[str, str, int]]:
        """
        Splits source into function blocks to scope analysis.
        Returns list of (func_name, body_content, start_line).
        
        Heuristic: Tracks brace balance to identify function bodies.
        """
        blocks = []
        lines = source.splitlines()
        current_func = None
        current_body = []
        brace_balance = 0
        start_line = 0
        
        # Simplified regex for C/C++ function signatures
        func_sig_pattern = re.compile(r"^\s*[\w:*]+\s+([a-zA-Z0-9_]+)\s*\([^)]*\)\s*(?:const|noexcept)?\s*\{")

        for idx, line in enumerate(lines):
            # If not currently inside a function body
            if brace_balance == 0:
                match = func_sig_pattern.search(line)
                if match:
                    current_func = match.group(1)
                    start_line = idx + 1
                    # Calculate net brace change on this line
                    brace_balance = line.count('{') - line.count('}')
                    current_body = [line]
            else:
                # Inside function body
                brace_balance += line.count('{') - line.count('}')
                current_body.append(line)
                
                if brace_balance <= 0:
                    # End of function
                    blocks.append((current_func, "\n".join(current_body), start_line))
                    brace_balance = 0
                    current_func = None
                    current_body = []

        return blocks