import logging
import os
import xxhash
from collections import deque
from typing import Optional, Dict, Any, List, Union, Set

# External imports
from pylspclient.lsp_pydantic_strcuts import TextDocumentItem, Position

# Internal imports
from dependency_builder.ccls_code_navigator import CCLSCodeNavigator
from dependency_builder.utils import clean_uri, resolve_file_path
from dependency_builder.config import DependencyBuilderConfig, DEFAULT_CONFIG
from dependency_builder.metrics import get_metrics


class CCLSDependencyBuilder:
    """
    Orchestrates the retrieval of code dependencies (functions, variables, types)
    using the CCLSCodeNavigator. Builds call graphs and token-level dependency trees.
    """

    def __init__(self, project_root: str, cache_path: str, logger: logging.Logger,
                 config: DependencyBuilderConfig = None):
        self.project_root = os.path.abspath(project_root)
        self.cache_path = os.path.abspath(cache_path)
        self.logger = logger
        self.config = config or DEFAULT_CONFIG
        self._metrics = get_metrics()

    def _resolve_file_path(self, input_path: str) -> str:
        """Delegates to shared utility for file path resolution."""
        return resolve_file_path(input_path, self.project_root)

    def _clean_uri(self, uri: str) -> str:
        """Delegates to shared utility for URI cleaning."""
        return clean_uri(uri)

    # Class-level constants as fallback (prefer config values)
    MAX_BFS_DEPTH = DEFAULT_CONFIG.max_bfs_depth
    MAX_NODES_PER_LEVEL = DEFAULT_CONFIG.max_nodes_per_level

    def _get_dependency_bfs(self, nav: CCLSCodeNavigator, call_flow: Dict[str, Any], max_level: int = 1, is_parent: bool = False) -> Dict[str, Any]:
        """
        BFS traversal of the call graph. Collects dependencies at each level.
        Includes depth guards and node limits to prevent runaway traversal.
        """
        dependencies: Dict[int, Dict[str, Any]] = {}
        q: deque = deque()
        level = 0
        q.append(call_flow)
        seen: Set[str] = set()

        # Enforce absolute max depth even if caller requests more
        effective_max = min(max_level, self.config.max_bfs_depth)

        while q:
            current_level_size = len(q)
            if level > effective_max:
                break

            nodes_this_level = 0
            for _ in range(current_level_size):
                csym = q.popleft()
                if not csym or not csym.get("name") or csym["name"] in seen:
                    continue

                # Guard against too many nodes at one level
                if nodes_this_level >= self.config.max_nodes_per_level:
                    self.logger.warning(f"BFS node limit reached at level {level}")
                    break

                cdoc, cpos = nav.getDocandPosFromSymbol(csym)
                if not cdoc or not cpos:
                    continue

                nav.openDoc(cdoc)

                try:
                    locs = nav.lsp_client.lsp_endpoint.call_method(
                        "textDocument/definition", textDocument=cdoc, position=cpos
                    )
                except Exception:
                    continue

                # Safety: check locs is a non-empty list before indexing
                if not locs or not isinstance(locs, list) or len(locs) == 0:
                    continue

                loc = locs[0]
                if not isinstance(loc, dict) or "uri" not in loc or "range" not in loc:
                    continue

                def_path = self._clean_uri(loc["uri"])
                ddoc = nav.create_doc(def_path)

                if not ddoc:
                    continue

                start_info = loc["range"].get("start", {})
                dpos = Position(
                    line=start_info.get("line", 0),
                    character=start_info.get("character", 0),
                )
                cdef = nav.getDefinition(ddoc, dpos)

                if level not in dependencies:
                    dependencies[level] = {}

                # Include character position in hash to avoid collisions on overloaded symbols
                cid = csym.get(
                    "id",
                    xxhash.xxh64(
                        f"{cdoc.uri}:{csym['name']}:{cpos.line}:{cpos.character}"
                    ).hexdigest(),
                )

                dependencies[level][cid] = {
                    "name": csym["name"],
                    "definition": cdef,
                    "file": def_path,
                    "uri": ddoc.uri,
                    "start": start_info,
                    "kind": "FUNCTION_DECL",
                }

                for child in csym.get("children", []):
                    q.append(child)
                seen.add(csym["name"])
                nodes_this_level += 1

            level += 1

        return dependencies

    def _fetch_dependencies_for_symbol(self, nav: CCLSCodeNavigator, doc: TextDocumentItem, pos: Position, level: int) -> Optional[Dict[str, Any]]:
        """Helper to fetch dependencies for a specific symbol position."""
        call_flow = nav.getCallee(doc, pos, level)
        definition = nav.getDefinition(doc, pos)
        component_name = nav.get_name(doc, pos)

        if not call_flow:
            call_flow = nav.create_call_flow_from_token(doc, pos)
        
        if not call_flow:
            call_flow = {
                "name": component_name,
                "location": {
                    "uri": doc.uri,
                    "range": {
                        "start": {"line": pos.line, "character": pos.character},
                        "end": {
                            "line": pos.line + (len(definition.splitlines()) if definition else 0),
                            "character": len(definition.splitlines()[-1]) if definition else 0
                        }
                    }
                }
            }
        
        successors = self._get_dependency_bfs(nav, call_flow, max_level=level)
        
        # Optional: Predecessors (Callers)
        parent_call_flow = nav.getCaller(doc, pos, level)
        predecessors = self._get_dependency_bfs(nav, parent_call_flow, max_level=level, is_parent=True) if parent_call_flow else {}

        return {
            "name": call_flow.get("name", component_name),
            "file": self._clean_uri(doc.uri),
            "definition": definition,
            "dependencies": {
                "successors": successors,
                "predecessors": predecessors
            }
        }

    def get_dependency_component(self, file_path: str, component_name: str, level: int = 1) -> Optional[Dict[str, Any]]:
        abs_file_path = self._resolve_file_path(file_path)

        # FIX: Explicitly reject directories before initializing navigator
        if os.path.isdir(abs_file_path):
            self.logger.warning(f"Skipping dependency check on directory: {abs_file_path}")
            return None

        nav = CCLSCodeNavigator(self.project_root, self.cache_path, self.logger)
        try:
            doc = nav.create_doc(abs_file_path)
            if not doc:
                self.logger.error(f"File not found or not readable: {abs_file_path}")
                return None
            
            nav.openDoc(doc)
            docSymbols = nav.getDocumentSymbolsKeySymbols(doc)
            
            if component_name not in docSymbols or not docSymbols[component_name]:
                self.logger.warning(f"Component '{component_name}' not found in {abs_file_path}.")
                return None

            sym_data = docSymbols[component_name][0]
            doc, pos = nav.getDocandPosFromSymbol(sym_data)
            
            return self._fetch_dependencies_for_symbol(nav, doc, pos, level)
            
        except Exception as e:
            self.logger.exception(f"Error in get_dependency_component: {e}")
            return None
        finally:
            nav.killCCLSProcess()

    def get_dependency_line_char(self, file_path: str, line_no: int, character_no: int, level: int = 1) -> Optional[Dict[str, Any]]:
        abs_file_path = self._resolve_file_path(file_path)

        # FIX: Explicitly reject directories
        if os.path.isdir(abs_file_path):
            self.logger.warning(f"Skipping dependency check on directory: {abs_file_path}")
            return None

        nav = CCLSCodeNavigator(self.project_root, self.cache_path, self.logger)
        try:
            doc = nav.create_doc(abs_file_path)
            if not doc:
                return None
            
            nav.openDoc(doc)
            pos = Position(line=line_no, character=character_no)
            
            return self._fetch_dependencies_for_symbol(nav, doc, pos, level)
        except Exception as e:
            self.logger.exception(f"Error in get_dependency_line_char: {e}")
            return None
        finally:
            nav.killCCLSProcess()

    def get_dependency_diff(self, file_path: str, start: int = 0, end: Union[int, float] = float("inf"), level: int = 1) -> List[Dict[str, Any]]:
        """
        Fetch dependencies for ALL tokens found within a range of lines.
        Used for analyzing specific chunks of code (e.g., in git diffs or LLM context).
        """
        abs_file_path = self._resolve_file_path(file_path)

        # FIX: Explicitly reject directories
        if os.path.isdir(abs_file_path):
            self.logger.warning(f"Skipping dependency diff on directory: {abs_file_path}")
            return []

        nav = CCLSCodeNavigator(self.project_root, self.cache_path, self.logger)
        try:
            doc = nav.create_doc(abs_file_path)
            if not doc:
                self.logger.error(f"Could not open doc for diff: {abs_file_path}")
                return []
            
            nav.openDoc(doc)
            content = nav.read_file(abs_file_path)
            lines = content.splitlines()
            if not lines:
                return []

            end_idx = min(len(lines), int(end) + 1) if end != float("inf") else len(lines)
            diff_content = "\n".join(lines[start:end_idx])
            
            # Get tokens for the snippet
            tokens = nav.getTokens(diff_content, doc, Position(line=start, character=0))

            results = []
            seen_defs = set()
            
            for token in tokens.values():
                # Avoid duplicates
                if token["name"] in seen_defs:
                    continue
                
                # Resolve token definition
                try:
                    ddoc, dpos, _ = nav.getDefinitionFromToken(
                        doc, 
                        Position(line=token["line"], character=token.get("character", 0))
                    )
                except Exception:
                    continue
                
                if ddoc and dpos:
                    seen_defs.add(token["name"])
                    
                    # Fetch dependencies for this token's definition
                    deps = self._fetch_dependencies_for_symbol(nav, ddoc, dpos, level)
                    if deps:
                        results.append(deps)
                        
            return results
            
        except Exception as e:
            self.logger.exception(f"Error in get_dependency_diff: {e}")
            return []
        finally:
            nav.killCCLSProcess()