"""
Call graph analysis adapter using CCLS code navigator.

Builds a complete call graph and computes metrics including fan-in/fan-out analysis,
cycle detection, call depth analysis, and identification of architectural issues.
"""

import logging
import sys
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict

from agents.adapters.base_adapter import BaseStaticAdapter


class CallGraphAdapter(BaseStaticAdapter):
    """
    Analyzes call graph structure to identify architectural issues.

    Metrics computed:
    - Fan-in/fan-out degrees for each function
    - Detection of high-coupling God functions
    - Identification of functions with excessive responsibilities
    - Cycle detection in call graph
    - Maximum call depth analysis
    - Orphan and leaf function classification
    """

    def __init__(self, debug: bool = False):
        """Initialize call graph adapter."""
        super().__init__("call_graph", debug=debug)

    def analyze(
        self,
        file_cache: List[Dict[str, Any]],
        ccls_navigator: Optional[Any] = None,
        dependency_graph: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze call graph structure.

        Args:
            file_cache: List of file entries with metadata
            ccls_navigator: CCLSCodeNavigator instance for code analysis
            dependency_graph: Optional dependency graph (unused here)

        Returns:
            Standard adapter result dict with call graph metrics
        """
        # 1. Validation Checks
        if not file_cache:
            return self._create_neutral_result("No files to analyze")

        if ccls_navigator is None:
            return self._handle_tool_unavailable(
                "CCLSCodeNavigator",
                "Call graph analysis requires CCLS; ensure ccls is installed and indexed",
            )

        # 2. Build Graph
        graph = self._build_call_graph(file_cache, ccls_navigator)

        if not graph:
            return self._create_neutral_result("No functions or call graph found")

        # 3. Compute Metrics
        metrics_data = self._compute_metrics(graph)

        # 4. Generate Report
        return self._generate_report(graph, metrics_data)

    def _build_call_graph(
        self, file_cache: List[Dict[str, Any]], ccls_navigator: Any
    ) -> Dict[str, Set[str]]:
        """Phase 1: Build call graph adjacency list from files."""
        graph: Dict[str, Set[str]] = defaultdict(set)
        files_processed = 0
        
        for entry in file_cache:
            if files_processed >= 200:  # Soft limit to prevent timeouts on huge repos
                self.logger.warning("Reached file processing limit (200). Stopping graph build.")
                break

            language = entry.get("language", "").lower()
            # Fallback if language not set
            if not language and entry.get("file_path", "").endswith((".c", ".cpp", ".cc", ".h")):
                language = "cpp"

            if language not in ("c", "cpp"):
                continue

            file_path = entry.get("file_path") or entry.get("file_name")
            if not file_path:
                continue

            try:
                self._process_file_symbols(file_path, ccls_navigator, graph)
                files_processed += 1
            except Exception as e:
                self.logger.warning(f"Error processing file {file_path}: {e}")
                continue

        # Ensure all referenced functions exist as keys in the graph
        # (even if we didn't scan their definition file, they are nodes)
        all_callees = set()
        for callees in graph.values():
            all_callees.update(callees)
        
        for callee in all_callees:
            if callee not in graph:
                graph[callee] = set()
                
        return dict(graph)

    def _process_file_symbols(
        self, file_path: str, ccls_navigator: Any, graph: Dict[str, Set[str]]
    ) -> None:
        """Helper to process symbols within a single file."""
        doc = ccls_navigator.create_doc(file_path)
        if doc is None:
            return

        ccls_navigator.openDoc(doc)
        symbols_dict = ccls_navigator.getDocumentSymbolsKeySymbols(doc)

        if not symbols_dict:
            return

        functions_processed = 0
        # Iterate over symbols
        for func_name, symbol_list in symbols_dict.items():
            if functions_processed >= 50:  # Per-file limit
                break

            for symbol in symbol_list:
                kind = symbol.get("kind")
                # Accept Function (12), Method (6), Constructor (9)
                if kind in ("Function", "Method", "Constructor"):
                    
                    # Ensure node exists
                    if func_name not in graph:
                        graph[func_name] = set()

                    # Get callees
                    try:
                        doc_result, pos = ccls_navigator.getDocandPosFromSymbol(symbol)
                        if doc_result and pos:
                            callee_tree = ccls_navigator.getCallee(doc_result, pos, level=1)
                            callee_names = self._extract_names_from_tree(callee_tree)

                            for callee_name in callee_names:
                                if callee_name != func_name:  # Ignore self-recursion for the graph edges
                                    graph[func_name].add(callee_name)
                    except Exception as e:
                        self.logger.debug(f"Error getting callees for {func_name}: {e}")

                    functions_processed += 1
                    # Only process the first definition of the function in this file
                    break 

    def _compute_metrics(self, graph: Dict[str, Set[str]]) -> Dict[str, Any]:
        """Phase 2: Compute graph metrics (fan-in, fan-out, cycles, depth)."""
        
        # Calculate degrees
        out_degree = {func: len(callees) for func, callees in graph.items()}
        in_degree = defaultdict(int)
        
        # Initialize all nodes in in_degree map
        for func in graph:
            in_degree[func] = 0
            
        for func, callees in graph.items():
            for callee in callees:
                in_degree[callee] += 1

        # Identify problematic functions
        high_fan_in = {func for func, count in in_degree.items() if count > 20}
        high_fan_out = {func for func, count in out_degree.items() if count > 15}
        leaf_functions = {func for func, count in out_degree.items() if count == 0}
        orphan_functions = {func for func, count in in_degree.items() if count == 0}

        # Detect cycles
        cycles = set()
        visited_global = set()
        rec_stack = set()
        
        # Increase recursion limit slightly for deep graphs if necessary, 
        # though iterative approaches are safer. We'll use the existing DFS 
        # but catch RecursionError just in case.
        sys.setrecursionlimit(max(sys.getrecursionlimit(), 2000))
        
        try:
            for start_func in graph:
                if start_func not in visited_global:
                    self._dfs_detect_cycles(
                        start_func, graph, visited_global, rec_stack, cycles
                    )
        except RecursionError:
            self.logger.error("Recursion limit hit during cycle detection")

        # Compute max depth
        max_call_depth = self._compute_max_depth(graph, in_degree)

        return {
            "in_degree": in_degree,
            "out_degree": out_degree,
            "high_fan_in": high_fan_in,
            "high_fan_out": high_fan_out,
            "leaf_functions": leaf_functions,
            "orphan_functions": orphan_functions,
            "cycles": cycles,
            "max_call_depth": max_call_depth
        }

    def _generate_report(
        self, graph: Dict[str, Set[str]], metrics: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Phase 3: Generate detail entries and calculate score."""
        
        details: List[Dict[str, Any]] = []
        issues: List[str] = []
        
        in_degree = metrics["in_degree"]
        out_degree = metrics["out_degree"]
        high_fan_in = metrics["high_fan_in"]
        high_fan_out = metrics["high_fan_out"]
        cycles = metrics["cycles"]
        max_call_depth = metrics["max_call_depth"]

        # 1. High fan-in
        for func in sorted(high_fan_in):
            details.append(self._make_detail(
                file="",
                function=func,
                line=0,
                description=f"Function '{func}' has high fan-in ({in_degree[func]}); possible God function",
                severity="medium",
                category="call_graph",
            ))

        if high_fan_in:
            issues.append(f"Found {len(high_fan_in)} functions with high fan-in (>20)")

        # 2. High fan-out
        for func in sorted(high_fan_out):
            details.append(self._make_detail(
                file="",
                function=func,
                line=0,
                description=f"Function '{func}' has high fan-out ({out_degree[func]}); excessive complexity",
                severity="medium",
                category="call_graph",
            ))

        if high_fan_out:
            issues.append(f"Found {len(high_fan_out)} functions with high fan-out (>15)")

        # 3. Cycles
        for func in sorted(cycles):
            details.append(self._make_detail(
                file="",
                function=func,
                line=0,
                description=f"Function '{func}' is part of a call cycle",
                severity="high",
                category="call_graph",
            ))

        if cycles:
            issues.append(f"Found {len(cycles)} functions involved in call cycles")

        if not issues:
            issues = ["No architectural issues detected in call graph"]

        # Score Calculation
        score = 100.0
        score -= len(high_fan_in) * 2  # Reduced penalty
        score -= len(high_fan_out) * 3
        score -= len(cycles) * 10      # Heavy penalty for cycles
        score -= max(0, max_call_depth - 10) * 1 # Light penalty for depth
        score = max(0.0, min(100.0, score))

        # Aggregate Metrics for Display
        total_edges = sum(len(callees) for callees in graph.values())
        func_count = len(graph)
        
        output_metrics = {
            "functions_in_graph": func_count,
            "edges": total_edges,
            "avg_fan_in": round(total_edges / func_count, 2) if func_count else 0,
            "avg_fan_out": round(total_edges / func_count, 2) if func_count else 0,
            "max_fan_in": max(in_degree.values()) if in_degree else 0,
            "max_fan_out": max(out_degree.values()) if out_degree else 0,
            "high_fan_in_count": len(high_fan_in),
            "high_fan_out_count": len(high_fan_out),
            "cycle_count": len(cycles),
            "max_call_depth": max_call_depth,
            "leaf_count": len(metrics["leaf_functions"]),
            "orphan_count": len(metrics["orphan_functions"]),
        }

        return {
            "score": score,
            "grade": self._score_to_grade(score),
            "metrics": output_metrics,
            "issues": issues,
            "details": details,
            "tool_available": True,
        }

    def _create_neutral_result(self, message: str) -> Dict[str, Any]:
        """Returns a neutral (passing) result."""
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
        Handles both list and dict inputs robustly.
        """
        if collected is None:
            collected = []

        if not tree:
            return collected

        # Case 1: List of nodes (Multiple roots or siblings)
        if isinstance(tree, list):
            for item in tree:
                self._extract_names_from_tree(item, collected)
            return collected

        # Case 2: Single node (Dict)
        if isinstance(tree, dict):
            if "name" in tree:
                name = tree["name"]
                if name and name not in collected:
                    collected.append(name)

            children = tree.get("children", [])
            # Children might be a list or a dict (unlikely but possible in some schemas)
            if isinstance(children, list):
                for child in children:
                    self._extract_names_from_tree(child, collected)
            elif isinstance(children, dict):
                self._extract_names_from_tree(children, collected)

        return collected

    def _dfs_detect_cycles(
        self,
        node: str,
        graph: Dict[str, Set[str]],
        visited: Set[str],
        rec_stack: Set[str],
        cycles: Set[str],
    ) -> None:
        """
        DFS-based cycle detection.
        """
        visited.add(node)
        rec_stack.add(node)

        for neighbor in graph.get(node, set()):
            if neighbor not in visited:
                self._dfs_detect_cycles(neighbor, graph, visited, rec_stack, cycles)
            elif neighbor in rec_stack:
                # Cycle found
                cycles.add(node)
                cycles.add(neighbor)

        rec_stack.remove(node)

    def _compute_max_depth(
        self, graph: Dict[str, Set[str]], in_degree: Dict[str, Any]
    ) -> int:
        """
        Compute maximum call depth from root functions.
        """
        memo: Dict[str, int] = {}

        def dfs_depth(node: str, visited: Set[str]) -> int:
            if node in memo:
                return memo[node]

            if node in visited:
                return 0  # Break cycle

            visited.add(node)

            callees = graph.get(node, set())
            if not callees:
                memo[node] = 1
                return 1

            # Get max depth of children
            max_child_depth = 0
            for callee in callees:
                # Optimization: Pass a copy of visited path only
                d = dfs_depth(callee, visited.copy())
                if d > max_child_depth:
                    max_child_depth = d
            
            depth = 1 + max_child_depth
            memo[node] = depth
            return depth

        # Roots are functions with in-degree 0
        # If no roots (pure cycle graph), pick arbitrary nodes (not perfect but prevents 0 result)
        roots = [func for func, count in in_degree.items() if count == 0]
        
        if not roots and graph:
            # If full cyclic graph, just pick the first few nodes to start search
            roots = list(graph.keys())[:5]

        max_depth = 0
        for root in roots:
            try:
                depth = dfs_depth(root, set())
                max_depth = max(max_depth, depth)
            except RecursionError:
                continue

        return max_depth