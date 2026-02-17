import re
from typing import Dict, List, Set, Any, Tuple
from .base_runtime_analyzer import RuntimeAnalyzerBase

class PotentialDeadlockAnalyzer(RuntimeAnalyzerBase):
    """
    Advanced Deadlock Detection.
    
    Capabilities:
    1. Lock Order Inversion (Graph Cycle Detection across files).
    2. Missing Unlock (Non-RAII detection).
    3. Nested Lock risks (Heuristic).
    """

    # Patterns
    _LOCK_ACQUIRE = re.compile(r"\b(pthread_mutex_lock|mtx_lock|sem_wait)\s*\(\s*&?([a-zA-Z0-9_>.]+)\s*\)")
    _LOCK_RELEASE = re.compile(r"\b(pthread_mutex_unlock|mtx_unlock|sem_post)\s*\(\s*&?([a-zA-Z0-9_>.]+)\s*\)")
    _CPP_LOCK_GUARD = re.compile(r"\b(std::lock_guard|std::unique_lock|std::scoped_lock)<[^>]+>\s+([a-zA-Z0-9_]+)\s*\(\s*([a-zA-Z0-9_>.]+)\s*\)")
    
    def analyze(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Batch analysis that builds a global lock dependency graph.
        """
        issues = []
        metrics = []
        
        # Global lock dependency graph: { "lockA": {"lockB", "lockC"} }
        lock_dependency_graph: Dict[str, Set[str]] = {}

        for entry in file_cache:
            if entry.get("suffix", "").lower() not in {".c", ".cpp", ".cc", ".h", ".hpp"}:
                continue

            rel_path = entry.get("rel_path", "unknown")
            source = entry.get("source", "")
            
            # Analyze single file
            result = self.analyze_single_file(source, rel_path)
            
            # Aggregate local issues
            issues.extend(result["issues"])
            metrics.append(result["metrics"])
            
            # Aggregate dependencies for global cycle detection
            local_deps = result.get("raw_dependencies", {})
            for lock_a, held_locks in local_deps.items():
                if lock_a not in lock_dependency_graph:
                    lock_dependency_graph[lock_a] = set()
                lock_dependency_graph[lock_a].update(held_locks)

        # Detect Cycles in Global Graph (ABBA Deadlock)
        cycle_issues = self._detect_graph_cycles(lock_dependency_graph)
        issues.extend(cycle_issues)

        return {"metrics": metrics, "issues": issues}

    def analyze_single_file(self, source: str, rel_path: str) -> Dict[str, Any]:
        """
        Performs local deadlock analysis on a single file.
        Returns local issues and raw dependency data for global aggregation.
        """
        local_issues = []
        local_lock_count = 0
        
        # Dependencies found in this file: { "lockA": {"lockB"} }
        local_dependencies: Dict[str, Set[str]] = {}

        func_blocks = self._get_function_blocks(source)
        
        for func_name, body, start_line in func_blocks:
            # 1. Analyze Lock Ordering
            deps, f_issues = self._analyze_lock_dependencies(body, start_line)
            
            for issue in f_issues:
                local_issues.append(f"{rel_path}: {issue}")
                local_lock_count += 1
            
            # Merge into file-local dependencies
            for lock_a, held_locks in deps.items():
                if lock_a not in local_dependencies:
                    local_dependencies[lock_a] = set()
                local_dependencies[lock_a].update(held_locks)

            # 2. Check for missing unlocks
            missing_unlocks = self._check_missing_unlocks(body, func_name, start_line)
            for issue in missing_unlocks:
                local_issues.append(f"{rel_path}: {issue}")
                local_lock_count += 1

        return {
            "issues": local_issues,
            "metrics": {
                "file": rel_path,
                "concurrency_issues": local_lock_count
            },
            "raw_dependencies": local_dependencies
        }

    def _analyze_lock_dependencies(self, body: str, start_line: int) -> Tuple[Dict[str, Set[str]], List[str]]:
        dependencies = {}
        issues = []
        held_locks = [] # Stack
        
        lines = body.splitlines()
        for i, line in enumerate(lines):
            # C-style Lock
            m_lock = self._LOCK_ACQUIRE.search(line)
            if m_lock:
                lock_name = m_lock.group(2)
                if held_locks:
                    parent = held_locks[-1]
                    if parent not in dependencies:
                        dependencies[parent] = set()
                    dependencies[parent].add(lock_name)
                    
                    if len(held_locks) >= 2:
                        issues.append(f"Line {start_line+i}: Deeply nested locking detected ({len(held_locks)+1} locks).")
                
                held_locks.append(lock_name)
                continue

            # C++ RAII Lock
            m_guard = self._CPP_LOCK_GUARD.search(line)
            if m_guard:
                lock_name = m_guard.group(3)
                if held_locks:
                    parent = held_locks[-1]
                    if parent not in dependencies:
                        dependencies[parent] = set()
                    dependencies[parent].add(lock_name)
                held_locks.append(lock_name)

            # Unlock
            m_unlock = self._LOCK_RELEASE.search(line)
            if m_unlock:
                lock_name = m_unlock.group(2)
                if lock_name in held_locks:
                    held_locks.remove(lock_name)

        return dependencies, issues

    def _check_missing_unlocks(self, body: str, func_name: str, start_line: int) -> List[str]:
        issues = []
        lines = body.splitlines()
        active_locks = set()
        
        for i, line in enumerate(lines):
            m_lock = self._LOCK_ACQUIRE.search(line)
            if m_lock:
                active_locks.add(m_lock.group(2))
            
            m_unlock = self._LOCK_RELEASE.search(line)
            if m_unlock:
                l = m_unlock.group(2)
                if l in active_locks:
                    active_locks.remove(l)
            
            if "return" in line and not line.strip().startswith("//"):
                if active_locks and not self._LOCK_RELEASE.search(line):
                    issues.append(f"Line {start_line+i}: Return in '{func_name}' while holding locks: {list(active_locks)}.")
        
        return issues

    def _detect_graph_cycles(self, graph: Dict[str, Set[str]]) -> List[str]:
        issues = []
        visited = set()
        recursion_stack = set()

        def dfs(node, path):
            visited.add(node)
            recursion_stack.add(node)
            path.append(node)

            if node in graph:
                for neighbor in graph[node]:
                    if neighbor not in visited:
                        if dfs(neighbor, path):
                            return True
                    elif neighbor in recursion_stack:
                        cycle_path = " -> ".join(path) + f" -> {neighbor}"
                        issues.append(f"Global Deadlock Risk (Lock Inversion): Cycle detected: {cycle_path}")
                        return True
            
            recursion_stack.remove(node)
            path.pop()
            return False

        for node in list(graph.keys()):
            if node not in visited:
                dfs(node, [])
        
        return issues