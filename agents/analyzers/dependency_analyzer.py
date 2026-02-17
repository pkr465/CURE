"""
C/C++ dependency graph building and analysis (enhanced, configurable, component-aware)
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Any, Optional, Tuple
from collections import defaultdict
import networkx as nx


# --------------------------------------------------------------------- #
# Configuration and layer rules
# --------------------------------------------------------------------- #

@dataclass
class LayerRule:
    """
    Simple architectural layering rule.

    Example:
        LayerRule(
            layer_name="core",
            path_prefixes=["src/core", "lib/core"],
            allowed_dependencies=["core", "utils"],
        )
    """
    layer_name: str
    path_prefixes: List[str]
    allowed_dependencies: List[str]


@dataclass
class AnalyzerConfig:
    """
    Configuration for the C/C++ dependency analyzer.
    """
    # Project root (normalized absolute path or relative base)
    project_root: str = "."

    # Include search paths (similar to -I flags), relative to project_root or absolute
    include_paths: List[str] = field(default_factory=list)

    # System include paths (e.g., /usr/include, toolchain paths)
    # Currently informational; could be used for better system header classification
    system_include_paths: List[str] = field(default_factory=list)

    # Directories to ignore, relative to project_root (e.g., ["tests", "build"])
    ignore_dirs: List[str] = field(default_factory=list)

    # Whether to mark unresolved local includes as "missing" external nodes
    mark_unresolved_as_missing: bool = True

    # Hotspot thresholds (tunable per project)
    high_fan_in_threshold: int = 5
    high_fan_out_threshold: int = 15
    heavy_std_threshold: int = 10

    # Enable/disable advanced metrics (centrality, etc.) for performance reasons
    enable_advanced_metrics: bool = True

    # Architectural layer rules (optional, for layering checks)
    layer_rules: List[LayerRule] = field(default_factory=list)


# --------------------------------------------------------------------- #
# Dependency Analyzer
# --------------------------------------------------------------------- #

class DependencyAnalyzer:
    """
    Builds and analyzes dependency graphs specifically for C/C++ codebases.

    Focuses on:
    - #include statement analysis (system and local)
    - Header-source file relationships
    - Circular dependency and strongly-connected component detection
    - Module coupling analysis (fan-in/fan-out)
    - Optional layer rule and component (directory) analysis
    """

    # Standard C/C++ system headers
    STANDARD_C_HEADERS = {
        "assert.h",
        "ctype.h",
        "errno.h",
        "float.h",
        "limits.h",
        "locale.h",
        "math.h",
        "setjmp.h",
        "signal.h",
        "stdarg.h",
        "stddef.h",
        "stdio.h",
        "stdlib.h",
        "string.h",
        "time.h",
        "iso646.h",
        "wchar.h",
        "wctype.h",
    }

    STANDARD_CPP_HEADERS = {
        "algorithm",
        "array",
        "atomic",
        "bitset",
        "chrono",
        "codecvt",
        "complex",
        "condition_variable",
        "deque",
        "exception",
        "forward_list",
        "fstream",
        "functional",
        "future",
        "initializer_list",
        "iomanip",
        "ios",
        "iosfwd",
        "iostream",
        "istream",
        "iterator",
        "limits",
        "list",
        "locale",
        "map",
        "memory",
        "mutex",
        "new",
        "numeric",
        "ostream",
        "queue",
        "random",
        "ratio",
        "regex",
        "set",
        "sstream",
        "stack",
        "stdexcept",
        "streambuf",
        "string",
        "strstream",
        "system_error",
        "thread",
        "tuple",
        "type_traits",
        "typeindex",
        "typeinfo",
        "unordered_map",
        "unordered_set",
        "utility",
        "valarray",
        "vector",
    }

    # C++ versions of C headers
    CPP_C_HEADERS = {
        "cassert",
        "cctype",
        "cerrno",
        "cfloat",
        "climits",
        "clocale",
        "cmath",
        "csetjmp",
        "csignal",
        "cstdarg",
        "cstddef",
        "cstdio",
        "cstdlib",
        "cstring",
        "ctime",
        "ciso646",
        "cwchar",
        "cwctype",
    }

    def __init__(self, config: Optional[AnalyzerConfig] = None):
        """Initialize C/C++ dependency analyzer with optional configuration."""
        self.config = config or AnalyzerConfig(project_root=".")
        self.graph = nx.DiGraph()
        self.module_metadata: Dict[str, Dict[str, Any]] = {}
        self.external_dependencies: Set[str] = set()
        self.internal_modules: Set[str] = set()
        self.header_source_map: Dict[str, List[str]] = defaultdict(list)

        # Internal indexes for fast lookup
        self._path_index: Dict[str, Dict[str, Any]] = {}
        self._basename_index: Dict[str, List[Dict[str, Any]]] = {}

    # --------------------------------------------------------------------- #
    # Graph building
    # --------------------------------------------------------------------- #

    def build_graph(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Build comprehensive C/C++ dependency graph from file cache.

        Args:
            file_cache: List of processed C/C++ file entries. Expected keys:
                - file_name
                - file_relative_path
                - language ("c", "cpp", "c_header", "cpp_header")
                - size_bytes
                - metrics
                - functions
                - includes: list[{"file": str, "type": "local"/"system", ...}]
                - module_key (optional)

        Returns:
            Dictionary representation of dependency graph with metadata and analysis.
        """
        # Reset graph state
        self.graph.clear()
        self.module_metadata.clear()
        self.external_dependencies.clear()
        self.internal_modules.clear()
        self.header_source_map.clear()
        self._path_index.clear()
        self._basename_index.clear()

        if not file_cache:
            return {"analysis": {"total_nodes": 0, "total_edges": 0}}

        # Normalize and index file_cache
        self._build_indexes(file_cache)

        # First pass: Create nodes for all files and build header-source mapping
        self._create_nodes(file_cache)
        self._build_header_source_mapping(file_cache)

        # Second pass: Extract dependencies and create edges
        self._extract_dependencies(file_cache)

        # Third pass: Analyze graph structure
        graph_analysis = self._analyze_graph_structure()

        # Convert to dictionary format
        graph_dict = self._convert_to_dict()
        graph_dict["analysis"] = graph_analysis

        return graph_dict

    def _build_indexes(self, file_cache: List[Dict[str, Any]]) -> None:
        """Build internal indexes for fast path and basename lookups."""
        for fe in file_cache:
            rel_path = fe.get("file_relative_path") or fe.get("file_name", "")
            rel_path = rel_path.replace("\\", "/")
            fe["file_relative_path"] = rel_path  # normalize in place

            # Path index
            self._path_index[rel_path] = fe

            # Basename index
            base = os.path.basename(rel_path)
            self._basename_index.setdefault(base, []).append(fe)

    def _create_nodes(self, file_cache: List[Dict[str, Any]]) -> None:
        """Create nodes for all C/C++ files in the cache."""
        for file_entry in file_cache:
            # Skip ignored directories if configured
            rel_path = file_entry.get("file_relative_path", "")
            if self._is_ignored(rel_path):
                continue

            module_key = file_entry.get("module_key") or self._generate_module_key(
                file_entry
            )

            # Track as internal module
            self.internal_modules.add(module_key)

            language = file_entry.get("language", "unknown")
            includes = file_entry.get("includes", [])

            metadata = {
                "file_name": file_entry.get("file_name", ""),
                "file_relative_path": rel_path,
                "language": language,
                "size_bytes": file_entry.get("size_bytes", 0),
                "metrics": file_entry.get("metrics", {}),
                "functions": file_entry.get("functions", []),
                "external": False,
                "dependencies": [],
                "dependents": [],
                "include_count": len(includes),
                "is_header": language in ["c_header", "cpp_header"],
                "is_source": language in ["c", "cpp"],
            }

            self.graph.add_node(module_key, **metadata)
            self.module_metadata[module_key] = metadata

    def _is_ignored(self, rel_path: str) -> bool:
        """Return True if this path should be ignored based on config.ignore_dirs."""
        if not self.config.ignore_dirs:
            return False
        rel_path = rel_path.replace("\\", "/")
        for ignored in self.config.ignore_dirs:
            ignored_norm = ignored.replace("\\", "/")
            if rel_path.startswith(ignored_norm.rstrip("/") + "/") or rel_path == ignored_norm:
                return True
        return False

    def _generate_module_key(self, file_entry: Dict[str, Any]) -> str:
        """
        Generate a stable module key from file entry.

        Uses file_relative_path if present; strips extension and converts path
        separators to '.' to create a logical module id.
        """
        rel_path = file_entry.get("file_relative_path") or file_entry.get(
            "file_name", ""
        )
        rel_path = rel_path.replace("\\", "/")
        if "." in rel_path:
            rel_path = rel_path.rsplit(".", 1)[0]
        module_key = rel_path.replace("/", ".")
        return module_key

    def _build_header_source_mapping(self, file_cache: List[Dict[str, Any]]) -> None:
        """Build mapping between header files and their corresponding source files."""
        files_by_base: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        for file_entry in file_cache:
            rel_path = file_entry.get("file_relative_path", "")
            if self._is_ignored(rel_path):
                continue

            base_name = rel_path.rsplit(".", 1)[0] if "." in rel_path else rel_path
            files_by_base[base_name].append(file_entry)

        for base_name, files in files_by_base.items():
            headers = [
                f for f in files if f.get("language") in ["c_header", "cpp_header"]
            ]
            sources = [f for f in files if f.get("language") in ["c", "cpp"]]

            for header in headers:
                header_key = header.get("module_key") or self._generate_module_key(
                    header
                )
                source_keys = [
                    s.get("module_key") or self._generate_module_key(s) for s in sources
                ]
                self.header_source_map[header_key] = source_keys

    def _extract_dependencies(self, file_cache: List[Dict[str, Any]]) -> None:
        """Extract dependencies from all C/C++ files and create edges."""
        for file_entry in file_cache:
            rel_path = file_entry.get("file_relative_path", "")
            if self._is_ignored(rel_path):
                continue

            module_key = file_entry.get("module_key") or self._generate_module_key(
                file_entry
            )
            if module_key not in self.graph:
                # Might be ignored or filtered
                continue

            includes = file_entry.get("includes", [])

            for include in includes:
                include_file = include.get("file", "")
                include_type = include.get("type", "local")  # 'system' or 'local'

                if not include_file:
                    continue

                if include_type == "system":
                    # System includes: standard library vs third_party
                    if self._is_standard_header(include_file):
                        external_key = f"std.{include_file}"
                        self._add_external_dependency(
                            external_key, include_file, "standard"
                        )
                        self._add_dependency_edge(
                            module_key, external_key, "system_include", include
                        )
                    else:
                        external_key = f"external.{include_file}"
                        self._add_external_dependency(
                            external_key, include_file, "third_party"
                        )
                        self._add_dependency_edge(
                            module_key, external_key, "system_include", include
                        )
                else:
                    # Local includes - resolve to internal module if possible
                    resolved_module = self._resolve_local_include(
                        include_file, file_entry
                    )
                    if resolved_module and resolved_module != module_key:
                        self._add_dependency_edge(
                            module_key, resolved_module, "local_include", include
                        )
                    elif self.config.mark_unresolved_as_missing:
                        # Unresolved local include
                        external_key = f"missing.{include_file}"
                        self._add_external_dependency(
                            external_key, include_file, "missing"
                        )
                        self._add_dependency_edge(
                            module_key, external_key, "local_include", include
                        )

    # --------------------------------------------------------------------- #
    # Include resolution and external nodes
    # --------------------------------------------------------------------- #

    def _is_standard_header(self, header_name: str) -> bool:
        """Check if header is a standard C/C++ header."""
        header_base = header_name.split("/")[-1]
        return (
            header_base in self.STANDARD_C_HEADERS
            or header_base in self.STANDARD_CPP_HEADERS
            or header_base in self.CPP_C_HEADERS
        )

    def _resolve_local_include(
        self,
        include_file: str,
        current_file: Dict[str, Any],
    ) -> Optional[str]:
        """
        Resolve local include to internal module key using heuristics and config.

        Preference order:
        1) Exact relative path from current file's directory.
        2) Exact path from project root (as is).
        3) Exact path in configured include_paths.
        4) Any file with same basename, preferring headers and those in same dir tree.

        Returns None if no unambiguous match found.
        """
        current_rel = current_file.get("file_relative_path", "").replace("\\", "/")
        current_dir = os.path.dirname(current_rel)
        include_norm = include_file.replace("\\", "/")
        include_basename = os.path.basename(include_norm)

        candidate_paths: List[str] = []

        # 1. Current directory
        if current_dir:
            candidate_paths.append(os.path.join(current_dir, include_norm).replace("\\", "/"))

        # 2. As relative to project root
        candidate_paths.append(include_norm)

        # 3. User include paths (-I)
        for inc_dir in self.config.include_paths:
            inc_dir_norm = inc_dir.replace("\\", "/")
            candidate_paths.append(
                os.path.join(inc_dir_norm, include_norm).replace("\\", "/")
            )

        # Exact path matches (using pre-built index)
        exact_matches: List[Dict[str, Any]] = [
            self._path_index[p] for p in candidate_paths if p in self._path_index
        ]

        if exact_matches:
            chosen = exact_matches[0]
            return chosen.get("module_key") or self._generate_module_key(chosen)

        # 4. Basename matches (fallback heuristic)
        basename_matches: List[Dict[str, Any]] = self._basename_index.get(
            include_basename, []
        )

        if basename_matches:
            def sort_key(fe: Dict[str, Any]) -> Tuple[int, int, int]:
                rel_path = fe.get("file_relative_path", "").replace("\\", "/")
                is_header = 1 if fe.get("language") in ["c_header", "cpp_header"] else 0
                same_tree = 1 if current_dir and rel_path.startswith(current_dir) else 0
                return (-is_header, -same_tree, len(rel_path))

            basename_matches.sort(key=sort_key)
            chosen = basename_matches[0]
            return chosen.get("module_key") or self._generate_module_key(chosen)

        return None

    def _add_external_dependency(
        self, external_key: str, dep_name: str, dep_category: str
    ) -> None:
        """Add external dependency node if not already present."""
        if external_key in self.graph:
            return

        metadata = {
            "file_name": dep_name,
            "file_relative_path": dep_name,
            "language": "external",
            "external": True,
            "category": dep_category,  # 'standard', 'third_party', 'missing'
            "dependencies": [],
            "dependents": [],
            "is_header": True,
            "is_source": False,
            "include_count": 0,
        }

        self.graph.add_node(external_key, **metadata)
        self.module_metadata[external_key] = metadata
        self.external_dependencies.add(external_key)

    def _add_dependency_edge(
        self,
        from_module: str,
        to_module: str,
        dep_type: str,
        include_info: Dict[str, Any],
    ) -> None:
        """Add dependency edge between modules with include metadata."""
        if (
            from_module == to_module
            or not self.graph.has_node(from_module)
            or not self.graph.has_node(to_module)
        ):
            return

        edge_data = {
            "type": dep_type,
            "line": include_info.get("line"),
            "include_file": include_info.get("file", ""),
            "raw_line": include_info.get("raw_line", ""),
        }

        # If edge already exists, we keep the first metadata (could be extended to list)
        if not self.graph.has_edge(from_module, to_module):
            self.graph.add_edge(from_module, to_module, **edge_data)

        # Update metadata lists for quick access
        if to_module not in self.module_metadata[from_module]["dependencies"]:
            self.module_metadata[from_module]["dependencies"].append(to_module)

        if from_module not in self.module_metadata[to_module]["dependents"]:
            self.module_metadata[to_module]["dependents"].append(from_module)

    # --------------------------------------------------------------------- #
    # Graph analysis
    # --------------------------------------------------------------------- #

    def _analyze_graph_structure(self) -> Dict[str, Any]:
        """Analyze the C/C++ dependency graph structure."""
        analysis: Dict[str, Any] = {}

        total_nodes = self.graph.number_of_nodes()
        total_edges = self.graph.number_of_edges()

        analysis["total_nodes"] = total_nodes
        analysis["total_edges"] = total_edges
        analysis["internal_nodes"] = len(self.internal_modules)
        analysis["external_nodes"] = len(self.external_dependencies)

        internal_nodes = [n for n in self.graph.nodes() if n in self.internal_modules]

        # File type breakdown
        header_nodes = [
            n for n in internal_nodes if self.graph.nodes[n].get("is_header", False)
        ]
        source_nodes = [
            n for n in internal_nodes if self.graph.nodes[n].get("is_source", False)
        ]

        analysis["header_files"] = len(header_nodes)
        analysis["source_files"] = len(source_nodes)
        analysis["header_to_source_ratio"] = len(header_nodes) / max(
            1, len(source_nodes)
        )

        # Connectivity (full graph and internal-only)
        if total_nodes > 0:
            try:
                analysis["is_connected"] = nx.is_weakly_connected(self.graph)
                analysis["number_of_components"] = (
                    nx.number_weakly_connected_components(self.graph)
                )
            except Exception:
                analysis["is_connected"] = False
                analysis["number_of_components"] = 0

        internal_subgraph = self.graph.subgraph(self.internal_modules).copy()
        if internal_nodes:
            try:
                analysis["is_internal_connected"] = nx.is_weakly_connected(
                    internal_subgraph
                )
                analysis["internal_components"] = (
                    nx.number_weakly_connected_components(internal_subgraph)
                )
            except Exception:
                analysis["is_internal_connected"] = False
                analysis["internal_components"] = 0

        # Strongly connected components and cycles (internal only, SCC-driven)
        try:
            sccs = list(nx.strongly_connected_components(internal_subgraph))
            big_sccs = [sorted(list(c)) for c in sccs if len(c) > 1]
            analysis["scc_count"] = len(big_sccs)
            analysis["scc_examples"] = big_sccs[:10]

            analysis["has_cycles"] = len(big_sccs) > 0
            analysis["cycle_count"] = len(big_sccs)
            analysis["largest_cycle_size"] = max((len(c) for c in big_sccs), default=0)

            # For each SCC, try to get one representative cycle (limited)
            cycles = []
            for comp in big_sccs[:10]:
                if len(comp) > 1:
                    try:
                        sub = internal_subgraph.subgraph(comp)
                        some_cycle = next(nx.simple_cycles(sub), None)
                        if some_cycle:
                            cycles.append(some_cycle)
                    except Exception:
                        pass
            analysis["cycles"] = cycles
        except Exception:
            analysis["has_cycles"] = False
            analysis["cycle_count"] = 0
            analysis["largest_cycle_size"] = 0
            analysis["cycles"] = []
            analysis["scc_count"] = 0
            analysis["scc_examples"] = []

        # Include type breakdown
        include_types: Dict[str, int] = {}
        for _, _, edge_data in self.graph.edges(data=True):
            t = edge_data.get("type", "unknown")
            include_types[t] = include_types.get(t, 0) + 1
        analysis["include_types"] = include_types

        # External dependency categories
        external_categories: Dict[str, int] = {}
        for ext_node in self.external_dependencies:
            cat = self.graph.nodes[ext_node].get("category", "unknown")
            external_categories[cat] = external_categories.get(cat, 0) + 1
        analysis["external_categories"] = external_categories

        # Fan-in / fan-out (internal only)
        if internal_nodes:
            fan_in: Dict[str, int] = {}
            fan_out: Dict[str, int] = {}

            for node in internal_nodes:
                fan_out[node] = len(
                    [
                        succ
                        for succ in self.graph.successors(node)
                        if succ in self.internal_modules
                    ]
                )
                fan_in[node] = len(
                    [
                        pred
                        for pred in self.graph.predecessors(node)
                        if pred in self.internal_modules
                    ]
                )

            analysis["avg_fan_out"] = sum(fan_out.values()) / max(1, len(fan_out))
            analysis["max_fan_out"] = max(fan_out.values()) if fan_out else 0
            analysis["avg_fan_in"] = sum(fan_in.values()) / max(1, len(fan_in))
            analysis["max_fan_in"] = max(fan_in.values()) if fan_in else 0

            analysis["top_fan_out"] = sorted(
                fan_out.items(), key=lambda x: x[1], reverse=True
            )[:10]
            analysis["top_fan_in"] = sorted(
                fan_in.items(), key=lambda x: x[1], reverse=True
            )[:10]

        # Header-source relationship analysis
        analysis["header_source_pairs"] = len(self.header_source_map)

        orphaned_headers: List[str] = []
        for header_key, source_keys in self.header_source_map.items():
            if not source_keys:
                orphaned_headers.append(header_key)

        all_source_bases: Set[str] = set()
        all_header_bases: Set[str] = set()

        for node in internal_nodes:
            node_data = self.graph.nodes[node]
            base = node.rsplit(".", 1)[0] if "." in node else node
            if node_data.get("is_source"):
                all_source_bases.add(base)
            elif node_data.get("is_header"):
                all_header_bases.add(base)

        orphaned_source_bases = all_source_bases - all_header_bases

        analysis["orphaned_headers"] = len(orphaned_headers)
        analysis["orphaned_sources"] = len(orphaned_source_bases)
        analysis["orphaned_header_list"] = orphaned_headers[:100]
        analysis["orphaned_source_bases"] = list(sorted(orphaned_source_bases))[:100]

        # Advanced centrality metrics (optional, can be expensive on large graphs)
        if internal_nodes and self.config.enable_advanced_metrics:
            try:
                deg_centrality = nx.degree_centrality(internal_subgraph)
                analysis["top_degree_central"] = sorted(
                    deg_centrality.items(), key=lambda x: x[1], reverse=True
                )[:10]
            except Exception:
                analysis["top_degree_central"] = []
        else:
            analysis["top_degree_central"] = []

        # Directory / component-level graph analysis
        dir_graph = self.build_directory_level_graph()
        analysis["directory_graph_nodes"] = dir_graph.number_of_nodes()
        analysis["directory_graph_edges"] = dir_graph.number_of_edges()
        try:
            if dir_graph.number_of_nodes() > 0:
                analysis["directory_scc_count"] = sum(
                    1
                    for c in nx.strongly_connected_components(dir_graph)
                    if len(c) > 1
                )
            else:
                analysis["directory_scc_count"] = 0
        except Exception:
            analysis["directory_scc_count"] = 0

        # Layer rule violations (if layer_rules configured)
        layer_violations = self.check_layer_violations()
        analysis["layer_violations"] = len(layer_violations)
        analysis["layer_violation_examples"] = layer_violations[:20]

        return analysis

    # --------------------------------------------------------------------- #
    # Directory / component level graph
    # --------------------------------------------------------------------- #

    def build_directory_level_graph(self) -> nx.DiGraph:
        """
        Build a higher-level graph where each node is a directory (or component)
        and edges indicate that at least one file in dir A includes a file in dir B.
        """
        dir_graph = nx.DiGraph()

        # Add directory nodes for internal modules
        for node in self.internal_modules:
            if not self.graph.has_node(node):
                continue
            path = self.graph.nodes[node].get("file_relative_path", "")
            directory = os.path.dirname(path).replace("\\", "/")
            dir_graph.add_node(directory)

        # Add edges between directories
        for src, dst in self.graph.edges():
            if src not in self.internal_modules or dst not in self.internal_modules:
                continue
            src_path = self.graph.nodes[src].get("file_relative_path", "")
            dst_path = self.graph.nodes[dst].get("file_relative_path", "")
            src_dir = os.path.dirname(src_path).replace("\\", "/")
            dst_dir = os.path.dirname(dst_path).replace("\\", "/")
            if src_dir and dst_dir and src_dir != dst_dir:
                if not dir_graph.has_edge(src_dir, dst_dir):
                    dir_graph.add_edge(src_dir, dst_dir, count=0)
                dir_graph[src_dir][dst_dir]["count"] += 1

        return dir_graph

    # --------------------------------------------------------------------- #
    # Layer rule checking
    # --------------------------------------------------------------------- #

    def check_layer_violations(self) -> List[Dict[str, Any]]:
        """
        Check dependencies against configured layer rules.
        Returns a list of violations with details.

        A violation occurs when a file in layer A depends on a file in layer B
        and B is not in A's allowed_dependencies.
        """
        if not self.config.layer_rules:
            return []

        # Map directory prefix -> layer
        dir_to_layer: Dict[str, str] = {}
        for rule in self.config.layer_rules:
            for prefix in rule.path_prefixes:
                dir_to_layer[prefix.replace("\\", "/")] = rule.layer_name

        def get_layer_for_path(path: str) -> Optional[str]:
            path = path.replace("\\", "/")
            best = None
            best_len = -1
            for prefix, layer in dir_to_layer.items():
                if path.startswith(prefix) and len(prefix) > best_len:
                    best = layer
                    best_len = len(prefix)
            return best

        # Node -> layer mapping
        layer_by_node: Dict[str, Optional[str]] = {}
        for node in self.internal_modules:
            if not self.graph.has_node(node):
                continue
            path = self.graph.nodes[node].get("file_relative_path", "")
            layer_by_node[node] = get_layer_for_path(path)

        # Quick map: layer_name -> rule
        rule_by_layer = {r.layer_name: r for r in self.config.layer_rules}

        violations: List[Dict[str, Any]] = []
        for src, dst in self.graph.edges():
            if src not in self.internal_modules or dst not in self.internal_modules:
                continue
            src_layer = layer_by_node.get(src)
            dst_layer = layer_by_node.get(dst)
            if not src_layer or not dst_layer or src_layer == dst_layer:
                continue
            rule = rule_by_layer.get(src_layer)
            if rule and dst_layer not in rule.allowed_dependencies:
                violations.append(
                    {
                        "from_module": src,
                        "to_module": dst,
                        "from_layer": src_layer,
                        "to_layer": dst_layer,
                        "edge_type": self.graph[src][dst].get("type", "unknown"),
                    }
                )

        return violations

    # --------------------------------------------------------------------- #
    # Conversion and documentation
    # --------------------------------------------------------------------- #

    def _convert_to_dict(self) -> Dict[str, Any]:
        """Convert graph to dictionary representation keyed by module name."""
        graph_dict: Dict[str, Any] = {}

        for node in self.graph.nodes():
            node_data = dict(self.graph.nodes[node])

            # Outgoing dependencies
            dependencies = list(self.graph.successors(node))
            node_data["dependencies"] = dependencies

            # Incoming dependencies
            dependents = list(self.graph.predecessors(node))
            node_data["dependents"] = dependents

            # Edge details
            dependency_details = []
            for dep in dependencies:
                edge_data = self.graph.get_edge_data(node, dep, {})
                dependency_details.append(
                    {
                        "module": dep,
                        "type": edge_data.get("type", "unknown"),
                        "line": edge_data.get("line"),
                        "include_file": edge_data.get("include_file", ""),
                    }
                )
            node_data["dependency_details"] = dependency_details

            # Header-source mapping
            if node in self.header_source_map:
                node_data["corresponding_sources"] = self.header_source_map[node]

            graph_dict[node] = node_data

        return graph_dict

    def document_graph(self, dependency_graph: Dict[str, Any]) -> Dict[str, Any]:
        """Generate documentation for the C/C++ dependency graph."""
        docs: Dict[str, Any] = {}

        analysis = dependency_graph.get("analysis", {})

        docs["overview"] = {
            "total_modules": analysis.get("total_nodes", 0),
            "internal_modules": analysis.get("internal_nodes", 0),
            "external_dependencies": analysis.get("external_nodes", 0),
            "header_files": analysis.get("header_files", 0),
            "source_files": analysis.get("source_files", 0),
            "total_includes": analysis.get("total_edges", 0),
            "has_circular_dependencies": analysis.get("has_cycles", False),
            "circular_dependency_count": analysis.get("cycle_count", 0),
            "header_to_source_ratio": analysis.get("header_to_source_ratio", 0.0),
            "internal_components": analysis.get("internal_components", 0),
            "directory_graph_nodes": analysis.get("directory_graph_nodes", 0),
            "directory_graph_edges": analysis.get("directory_graph_edges", 0),
            "layer_violations": analysis.get("layer_violations", 0),
        }

        docs["include_analysis"] = {
            "include_types": analysis.get("include_types", {}),
            "external_categories": analysis.get("external_categories", {}),
        }

        docs["modules"] = {}
        for module_name, module_data in dependency_graph.items():
            if module_name == "analysis":
                continue

            dependencies = module_data.get("dependencies", [])
            dependents = module_data.get("dependents", [])

            docs["modules"][module_name] = {
                "file_path": module_data.get("file_relative_path", ""),
                "language": module_data.get("language", "unknown"),
                "is_external": module_data.get("external", False),
                "is_header": module_data.get("is_header", False),
                "is_source": module_data.get("is_source", False),
                "depends_on_count": len(dependencies),
                "depended_by_count": len(dependents),
                "depends_on": dependencies,
                "depended_by": dependents,
                "include_count": module_data.get("include_count", 0),
                "function_count": len(module_data.get("functions", [])),
                "description": self._generate_module_description(
                    module_name, module_data
                ),
            }

        docs["hotspots"] = self._identify_cpp_hotspots(dependency_graph)

        return docs

    def _generate_module_description(
        self, module_name: str, module_data: Dict[str, Any]
    ) -> str:
        """Generate a human-readable description for a C/C++ module."""
        if module_data.get("external", False):
            category = module_data.get("category", "unknown")
            return f"External {category} dependency: {module_name}"

        file_path = module_data.get("file_relative_path", "")
        language = module_data.get("language", "unknown")
        dep_count = len(module_data.get("dependencies", []))
        dependent_count = len(module_data.get("dependents", []))

        file_type = "header" if module_data.get("is_header") else "source"

        description = f"{language.upper()} {file_type} file at {file_path}."
        if module_data.get("is_source"):
            func_count = len(module_data.get("functions", []))
            description += f" Contains {func_count} function(s)."

        description += f" Includes {dep_count} file(s), included by {dependent_count} file(s)."
        return description

    def _identify_cpp_hotspots(
        self, dependency_graph: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Identify C/C++ specific dependency hotspots and problematic patterns."""
        hotspots = {
            "high_fan_in_headers": [],
            "high_fan_out_files": [],
            "circular_dependencies": [],
            "orphaned_headers": [],
            "orphaned_sources": [],
            "missing_includes": [],
            "heavy_standard_users": [],
            "central_modules": [],
        }

        analysis = dependency_graph.get("analysis", {})

        fan_in_threshold = self.config.high_fan_in_threshold
        fan_out_threshold = self.config.high_fan_out_threshold
        std_threshold = self.config.heavy_std_threshold

        # High fan-in headers
        for module_name, module_data in dependency_graph.items():
            if (
                module_name != "analysis"
                and not module_data.get("external", False)
                and module_data.get("is_header", False)
            ):
                dependents = module_data.get("dependents", [])
                if len(dependents) >= fan_in_threshold:
                    hotspots["high_fan_in_headers"].append(
                        {
                            "module": module_name,
                            "dependent_count": len(dependents),
                            "file_path": module_data.get("file_relative_path", ""),
                            "language": module_data.get("language", ""),
                        }
                    )

        # High fan-out files
        for module_name, module_data in dependency_graph.items():
            if module_name != "analysis" and not module_data.get("external", False):
                dependencies = module_data.get("dependencies", [])
                if len(dependencies) >= fan_out_threshold:
                    hotspots["high_fan_out_files"].append(
                        {
                            "module": module_name,
                            "dependency_count": len(dependencies),
                            "file_path": module_data.get("file_relative_path", ""),
                            "language": module_data.get("language", ""),
                        }
                    )

        # Circular dependencies (from analysis cycles)
        cycles = analysis.get("cycles", [])
        for cycle in cycles:
            hotspots["circular_dependencies"].append(
                {"modules": cycle, "size": len(cycle)}
            )

        # Orphaned headers/sources (from analysis)
        orphan_header_list = analysis.get("orphaned_header_list", [])
        for h in orphan_header_list:
            node_data = dependency_graph.get(h, {})
            hotspots["orphaned_headers"].append(
                {
                    "module": h,
                    "file_path": node_data.get("file_relative_path", ""),
                    "language": node_data.get("language", ""),
                }
            )

        orphan_source_bases = analysis.get("orphaned_source_bases", [])
        hotspots["orphaned_sources"] = orphan_source_bases

        # Missing includes
        for module_name, module_data in dependency_graph.items():
            if (
                module_name != "analysis"
                and module_data.get("external", False)
                and module_data.get("category") == "missing"
            ):
                dependents = module_data.get("dependents", [])
                hotspots["missing_includes"].append(
                    {
                        "missing_file": module_data.get("file_name", ""),
                        "referenced_by": dependents,
                        "reference_count": len(dependents),
                    }
                )

        # Heavy standard library users
        for module_name, module_data in dependency_graph.items():
            if module_name != "analysis" and not module_data.get("external", False):
                dependencies = module_data.get("dependencies", [])
                std_deps = [
                    dep
                    for dep in dependencies
                    if dep.startswith("std.")
                    and dependency_graph.get(dep, {}).get("external", False)
                ]
                if len(std_deps) >= std_threshold:
                    hotspots["heavy_standard_users"].append(
                        {
                            "module": module_name,
                            "std_include_count": len(std_deps),
                            "total_include_count": len(dependencies),
                            "file_path": module_data.get("file_relative_path", ""),
                        }
                    )

        # Centrality-based hotspots (if available)
        top_degree_central = analysis.get("top_degree_central", [])
        for m, score in top_degree_central:
            if (
                m in dependency_graph
                and not dependency_graph[m].get("external", False)
            ):
                hotspots["central_modules"].append(
                    {
                        "module": m,
                        "centrality_score": score,
                        "file_path": dependency_graph[m].get(
                            "file_relative_path", ""
                        ),
                    }
                )

        # Sorting
        hotspots["high_fan_in_headers"].sort(
            key=lambda x: x["dependent_count"], reverse=True
        )
        hotspots["high_fan_out_files"].sort(
            key=lambda x: x["dependency_count"], reverse=True
        )
        hotspots["circular_dependencies"].sort(key=lambda x: x["size"], reverse=True)
        hotspots["missing_includes"].sort(
            key=lambda x: x["reference_count"], reverse=True
        )
        hotspots["heavy_standard_users"].sort(
            key=lambda x: x["std_include_count"], reverse=True
        )
        hotspots["central_modules"].sort(
            key=lambda x: x["centrality_score"], reverse=True
        )

        return hotspots

    # --------------------------------------------------------------------- #
    # Modularization proposals and validation
    # --------------------------------------------------------------------- #

    def propose_modularization(self, dependency_graph: Dict[str, Any]) -> Dict[str, Any]:
        """Propose C/C++ specific modularization improvements."""
        proposals: Dict[str, Any] = {}

        hotspots = self._identify_cpp_hotspots(dependency_graph)

        # High fan-out files
        for file_info in hotspots["high_fan_out_files"]:
            module_name = file_info["module"]
            proposals[module_name] = {
                "action": "split_includes",
                "reason": (
                    f"High include count ({file_info['dependency_count']} includes)"
                ),
                "priority": (
                    "high" if file_info["dependency_count"] > 25 else "medium"
                ),
                "suggestions": [
                    "Group related includes into separate headers",
                    "Use forward declarations where possible",
                    "Consider using PIMPL idiom to reduce header dependencies",
                    "Split large files into focused modules",
                ],
            }

        # High fan-in headers
        for header_info in hotspots["high_fan_in_headers"]:
            module_name = header_info["module"]
            if module_name not in proposals:
                proposals[module_name] = {
                    "action": "extract_interface",
                    "reason": (
                        f"Widely included header "
                        f"({header_info['dependent_count']} dependents)"
                    ),
                    "priority": "medium",
                    "suggestions": [
                        "Extract minimal interface header",
                        "Move implementation details to separate headers",
                        "Use forward declarations in public interface",
                        "Consider splitting into multiple focused headers",
                    ],
                }

        # Circular dependencies
        for cycle_info in hotspots["circular_dependencies"]:
            for module_name in cycle_info["modules"]:
                if module_name not in proposals:
                    proposals[module_name] = {
                        "action": "break_cycle",
                        "reason": (
                            f"Part of circular dependency (size: {cycle_info['size']})"
                        ),
                        "priority": "critical",
                        "suggestions": [
                            "Use forward declarations to break cycles",
                            "Extract common interface or base class",
                            "Move shared code to separate module",
                            "Consider dependency inversion",
                        ],
                    }

        # Missing includes
        for missing_info in hotspots["missing_includes"]:
            for dependent_module in missing_info["referenced_by"]:
                if dependent_module not in proposals:
                    proposals[dependent_module] = {
                        "action": "fix_includes",
                        "reason": (
                            f"References missing file: {missing_info['missing_file']}"
                        ),
                        "priority": "high",
                        "suggestions": [
                            f"Create missing header file: {missing_info['missing_file']}",
                            "Fix include path or filename",
                            "Remove unused include if not needed",
                            "Check build system configuration",
                        ],
                    }

        # Central modules (high degree centrality)
        for central_info in hotspots.get("central_modules", []):
            module_name = central_info["module"]
            if module_name not in proposals:
                proposals[module_name] = {
                    "action": "reduce_coupling",
                    "reason": (
                        "Module is highly central in the include graph "
                        f"(centrality score: {central_info['centrality_score']:.3f})"
                    ),
                    "priority": "high",
                    "suggestions": [
                        "Review dependencies and remove unnecessary includes",
                        "Break out reusable parts into separate modules",
                        "Limit transitive dependencies via umbrella headers",
                    ],
                }

        return proposals

    def validate_modularization(
        self, dependency_graph: Dict[str, Any], modularization_plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate proposed C/C++ modularization changes."""
        validation = {
            "overall_score": 70,  # Base score
            "issues": [],
            "recommendations": [],
            "metrics": {},
        }

        analysis = dependency_graph.get("analysis", {})

        # Circular dependencies
        if analysis.get("has_cycles", False):
            cycle_count = analysis.get("cycle_count", 0)
            validation["issues"].append(
                f"Still has {cycle_count} strongly connected component(s) indicating circular dependencies"
            )
            validation["overall_score"] -= min(cycle_count * 5, 25)
        else:
            validation["overall_score"] += 15
            validation["recommendations"].append(
                "No circular include dependency SCCs detected"
            )

        # Header-to-source ratio
        header_to_source_ratio = analysis.get("header_to_source_ratio", 0.0)
        if header_to_source_ratio > 3.0:
            validation["issues"].append(
                f"High header-to-source ratio ({header_to_source_ratio:.1f}:1) - "
                "consider consolidating headers"
            )
            validation["overall_score"] -= 10
        elif header_to_source_ratio < 0.5:
            validation["issues"].append(
                f"Low header-to-source ratio ({header_to_source_ratio:.1f}:1) - "
                "potentially missing header files or overuse of source-only interfaces"
            )
            validation["overall_score"] -= 5

        # Missing includes
        external_categories = analysis.get("external_categories", {})
        missing_count = external_categories.get("missing", 0)
        if missing_count > 0:
            validation["issues"].append(
                f"{missing_count} missing include file(s) referenced"
            )
            validation["overall_score"] -= min(missing_count * 3, 20)
        else:
            validation["recommendations"].append(
                "No missing include files detected"
            )

        # Max fan-out
        max_fan_out = analysis.get("max_fan_out", 0)
        if max_fan_out > 20:
            validation["issues"].append(
                f"High coupling detected - max includes per file: {max_fan_out}"
            )
            validation["overall_score"] -= min((max_fan_out - 20) * 2, 15)
        else:
            validation["recommendations"].append(
                f"Max fan-out is reasonable ({max_fan_out} includes per file)"
            )

        # Connectivity
        if not analysis.get("is_internal_connected", True):
            validation["issues"].append(
                "Internal include graph is not fully connected - isolated components "
                "or subsystems detected (may be acceptable for modular architectures)"
            )
            # This might be acceptable in modular systems; use small penalty
            validation["overall_score"] -= 5

        # Architectural layer violations
        layer_violations = analysis.get("layer_violations", 0)
        if layer_violations > 0:
            validation["issues"].append(
                f"{layer_violations} architectural layer rule violation(s)"
            )
            validation["overall_score"] -= min(layer_violations * 2, 20)
        else:
            if self.config.layer_rules:
                validation["recommendations"].append(
                    "No layer rule violations detected"
                )

        # Clamp score
        validation["overall_score"] = max(0, min(100, validation["overall_score"]))

        validation["metrics"] = {
            "total_files": analysis.get("internal_nodes", 0),
            "header_files": analysis.get("header_files", 0),
            "source_files": analysis.get("source_files", 0),
            "circular_dependencies_sccs": analysis.get("cycle_count", 0),
            "missing_includes": missing_count,
            "max_fan_out": max_fan_out,
            "avg_fan_out": analysis.get("avg_fan_out", 0.0),
            "avg_fan_in": analysis.get("avg_fan_in", 0.0),
            "header_to_source_ratio": header_to_source_ratio,
            "internal_components": analysis.get("internal_components", 0),
            "directory_graph_nodes": analysis.get("directory_graph_nodes", 0),
            "directory_graph_edges": analysis.get("directory_graph_edges", 0),
            "layer_violations": layer_violations,
        }

        return validation