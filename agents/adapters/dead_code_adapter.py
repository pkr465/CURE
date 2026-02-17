"""
Dead code detection adapter using CCLS code navigator.

Identifies functions that are not reachable from known entry points via call graph analysis.
Uses BFS traversal to mark reachable functions, treating unreachable ones as potential dead code.
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from agents.adapters.base_adapter import BaseStaticAdapter


class DeadCodeAdapter(BaseStaticAdapter):
    """
    Detects dead code by identifying functions unreachable from entry points.

    Algorithm:
    1. Extract all function definitions from C/C++ files
    2. Identify entry points (main, test functions, header exports)
    3. BFS traversal from entry points to mark reachable functions
    4. Report all unreachable functions as dead code
    """

    def __init__(self, debug: bool = False):
        """Initialize dead code adapter."""
        super().__init__("dead_code", debug=debug)

    def analyze(
        self,
        file_cache: List[Dict[str, Any]],
        ccls_navigator: Optional[Any] = None,
        dependency_graph: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze files for dead code.

        Args:
            file_cache: List of file entries with metadata
            ccls_navigator: CCLSCodeNavigator instance for code analysis
            dependency_graph: Optional dependency graph (unused here)

        Returns:
            Standard adapter result dict with dead code findings
        """
        # 1. Validation Checks
        if not file_cache:
            return self._create_neutral_result("No files to analyze")

        if ccls_navigator is None:
            return self._handle_tool_unavailable(
                "CCLSCodeNavigator",
                "Dead code analysis requires CCLS; ensure ccls is installed and indexed",
            )

        # 2. Phase 1: Extract all function definitions
        all_functions = self._extract_functions(file_cache, ccls_navigator)
        
        if not all_functions:
            return self._create_neutral_result("No functions found in analyzed files")

        # 3. Phase 2: Identify entry points
        entry_points = self._identify_entry_points(all_functions)

        # 4. Phase 3: BFS reachability analysis
        reachable = self._perform_reachability_analysis(
            ccls_navigator, all_functions, entry_points
        )

        # 5. Phase 4: Report unreachable functions
        unreachable_keys = set(all_functions.keys()) - reachable
        
        return self._generate_report(all_functions, entry_points, reachable, unreachable_keys)

    def _extract_functions(
        self, file_cache: List[Dict[str, Any]], ccls_navigator: Any
    ) -> Dict[str, Tuple[str, int]]:
        """Extracts all function symbols from the provided files."""
        all_functions: Dict[str, Tuple[str, int]] = {}
        
        for entry in file_cache:
            language = entry.get("language", "").lower()
            # Simple extension check if language field is missing
            if not language:
                path = entry.get("file_path", "")
                if path.endswith((".c", ".cpp", ".cc", ".h", ".hpp")):
                    language = "cpp"

            if language not in ("c", "cpp"):
                continue

            file_path = entry.get("file_path") or entry.get("file_name")
            if not file_path:
                continue

            try:
                # Use absolute path for ccls consistency
                doc = ccls_navigator.create_doc(file_path)
                if doc is None:
                    continue

                ccls_navigator.openDoc(doc)
                symbols_dict = ccls_navigator.getDocumentSymbolsKeySymbols(doc)

                if not symbols_dict:
                    continue

                rel_path = entry.get("file_relative_path", file_path)
                
                # Iterate over all symbols to find functions
                for func_name, symbol_list in symbols_dict.items():
                    for symbol in symbol_list:
                        # Check specific kind "Function" (ID 12) or "Method" (ID 6)
                        # The navigator maps these to strings
                        kind = symbol.get("kind")
                        if kind in ("Function", "Method", "Constructor"):
                            location = symbol.get("location", {})
                            range_info = location.get("range", {})
                            start_info = range_info.get("start", {})
                            line = start_info.get("line", 0)

                            # Create a unique key for the function
                            compound_key = f"{rel_path}::{func_name}"
                            all_functions[compound_key] = (file_path, line)
                            
                            # Only register the first definition/declaration found in this file
                            # to avoid duplicates from overloads (simplification)
                            break 

            except Exception as e:
                self.logger.warning(
                    f"Error extracting functions from {file_path}: {e}"
                )
                continue
                
        if self.debug:
            self.logger.debug(f"Extracted {len(all_functions)} functions total.")
            
        return all_functions

    def _identify_entry_points(self, all_functions: Dict[str, Tuple[str, int]]) -> Set[str]:
        """Identifies potential entry points (main, tests, headers)."""
        entry_points: Set[str] = set()
        known_entries = {"main", "_start", "__libc_start_main", "WinMain", "DllMain", "setup", "loop"}

        for compound_key, (file_path, _) in all_functions.items():
            func_name = compound_key.split("::")[-1]

            # 1. Standard Entry Points
            if func_name in known_entries:
                entry_points.add(compound_key)
                continue

            # 2. Test Functions (common convention)
            if func_name.lower().startswith("test") or "test" in func_name.lower():
                entry_points.add(compound_key)
                continue

            # 3. Header Files (Public API assumption)
            # Functions defined in headers are often templates or inlines intended for use elsewhere
            if file_path.endswith((".h", ".hpp", ".hxx", ".hh")):
                entry_points.add(compound_key)
                continue

        if not entry_points and all_functions:
            self.logger.warning("No standard entry points found. Dead code analysis may be inaccurate.")
        elif self.debug:
            self.logger.debug(f"Identified {len(entry_points)} entry points.")

        return entry_points

    def _perform_reachability_analysis(
        self, 
        ccls_navigator: Any, 
        all_functions: Dict[str, Tuple[str, int]], 
        entry_points: Set[str]
    ) -> Set[str]:
        """Performs BFS to find all functions reachable from entry points."""
        reachable: Set[str] = set()
        visited: Set[str] = set()
        queue: List[str] = list(entry_points)
        
        # Pre-populate reachable with entry points
        reachable.update(entry_points)
        visited.update(entry_points)

        processed_count = 0

        while queue:
            current_key = queue.pop(0)
            processed_count += 1
            
            # Defensive check for very large graphs
            if processed_count > 10000:
                self.logger.warning("Reachability analysis hit safety limit (10000 nodes). stopping traversal.")
                break

            try:
                # Retrieve doc info for the current function
                if current_key not in all_functions:
                    continue
                    
                file_path, _ = all_functions[current_key]
                func_name = current_key.split("::")[-1]
                
                doc = ccls_navigator.create_doc(file_path)
                if not doc:
                    continue
                
                # Get the specific symbol position to query call hierarchy
                # We need to look it up again to get the exact position object required by ccls
                symbols_dict = ccls_navigator.getDocumentSymbolsKeySymbols(doc)
                symbol_list = symbols_dict.get(func_name)
                
                if not symbol_list:
                    continue
                    
                # Use the first matching symbol
                symbol = symbol_list[0]
                doc_ref, pos = ccls_navigator.getDocandPosFromSymbol(symbol)
                
                if not doc_ref or not pos:
                    continue

                # Query Callees (who does this function call?)
                # level=1 is usually sufficient for BFS one step at a time
                callee_tree = ccls_navigator.getCallee(doc_ref, pos, level=1)
                
                if not callee_tree:
                    continue

                callee_names = self._extract_names_from_tree(callee_tree)

                # Map names back to compound keys
                for callee_name in callee_names:
                    # HEURISTIC: Find this callee in our known functions list
                    # This is O(N) per edge, which is slow. Optimized by checking suffixes.
                    
                    found_target = False
                    
                    # 1. Try exact match if possible (requires knowing file, which we don't always from just name)
                    # 2. Search in all_functions
                    # TODO: Optimization - create a reverse lookup map {func_name: [keys]}
                    
                    # Current simplified matching: match by name
                    for candidate_key in all_functions:
                        if candidate_key in visited:
                            continue
                            
                        # Check if candidate ends with the callee name
                        if candidate_key.endswith(f"::{callee_name}"):
                            visited.add(candidate_key)
                            reachable.add(candidate_key)
                            queue.append(candidate_key)
                            found_target = True
                            # Don't break; multiple files might have same function name (overloading/static)
                            # Ideally we'd resolve exact file, but ccls tree name doesn't always give full path easily here

            except Exception as e:
                self.logger.debug(f"Error processing reachability for {current_key}: {e}")
                continue
                
        return reachable

    def _generate_report(
        self, 
        all_functions: Dict[str, Tuple[str, int]], 
        entry_points: Set[str], 
        reachable: Set[str], 
        unreachable_keys: Set[str]
    ) -> Dict[str, Any]:
        """Generates the final analysis report."""
        
        details: List[Dict[str, Any]] = []
        issues: List[str] = []
        
        # If no dead code found
        if not unreachable_keys:
            return {
                "score": 100.0,
                "grade": "A",
                "metrics": {
                    "total_functions": len(all_functions),
                    "entry_points": len(entry_points),
                    "reachable_count": len(reachable),
                    "dead_count": 0,
                    "dead_percentage": 0.0
                },
                "issues": ["No unreachable functions detected"],
                "details": [],
                "tool_available": True,
            }

        # Process findings
        for compound_key in sorted(unreachable_keys):
            file_path, line = all_functions[compound_key]
            func_name = compound_key.split("::")[-1]

            detail = self._make_detail(
                file=file_path,
                function=func_name,
                line=line,
                description=f"Function '{func_name}' is unreachable from entry points",
                severity="medium",
                category="dead_code",
            )
            details.append(detail)

        issues.append(
            f"Found {len(unreachable_keys)} unreachable functions (potential dead code)"
        )

        # Calculate score: Start at 100, deduct points.
        dead_count = len(unreachable_keys)
        total_funcs = len(all_functions)
        
        # Heuristic: If > 50% dead, score drops significantly.
        # Penalty: 3 points per dead function, capped at 0.
        score = max(0.0, 100.0 - (dead_count * 3.0))

        grade = self._score_to_grade(score)

        metrics = {
            "total_functions": total_funcs,
            "entry_points": len(entry_points),
            "reachable_count": len(reachable),
            "dead_count": dead_count,
            "dead_percentage": (dead_count / total_funcs * 100) if total_funcs > 0 else 0.0,
        }

        return {
            "score": score,
            "grade": grade,
            "metrics": metrics,
            "issues": issues,
            "details": details,
            "tool_available": True,
        }

    def _create_neutral_result(self, message: str) -> Dict[str, Any]:
        """Returns a neutral (passing) result when no analysis is possible/needed."""
        return {
            "score": 100.0,
            "grade": "A",
            "metrics": {},
            "issues": [message],
            "details": [],
            "tool_available": True
        }

    def _extract_names_from_tree(
        self, tree: Any, collected: Optional[List[str]] = None
    ) -> List[str]:
        """
        Extract function names from call tree structure.
        Handles both list (multiple roots) and dict (single root) inputs.
        """
        if collected is None:
            collected = []

        if not tree:
            return collected

        # Handle list input (common from ccls output)
        if isinstance(tree, list):
            for item in tree:
                self._extract_names_from_tree(item, collected)
            return collected

        # Handle dict input (node)
        if isinstance(tree, dict):
            # Add current node's name if present
            if "name" in tree:
                name = tree["name"]
                if name and name not in collected:
                    collected.append(name)

            # Recurse on children
            children = tree.get("children", [])
            if isinstance(children, list):
                for child in children:
                    self._extract_names_from_tree(child, collected)
            elif isinstance(children, dict):
                 self._extract_names_from_tree(children, collected)

        return collected