import subprocess
import os
import signal
import time
import math
import statistics
import logging
import xxhash
from pathlib import Path
from collections import defaultdict
from functools import lru_cache
from typing import List, Dict, Optional, Tuple, Any, Set
from copy import copy

# External libraries
from pylspclient import LspClient, JsonRpcEndpoint, LspEndpoint
from pylspclient.lsp_pydantic_strcuts import TextDocumentItem, Position
from clang.cindex import Index, Config as ClangConfig

# Internal imports
from dependency_builder.utils import clean_uri, to_uri
from dependency_builder.config import DependencyBuilderConfig, DEFAULT_CONFIG
from dependency_builder.metrics import get_metrics
from dependency_builder.lsp_notification_handlers import (
    semantic_highlight_handler,
    skipped_ranges_handler,
    progress_handler,
    work_done_progress_create_handler
)
from dependency_builder.exceptions import CCLSStartupError

# Symbol Kind Mapping (Standard LSP + ccls extensions)
SYMBOL_KIND_MAP = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
    6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
    11: "Interface", 12: "Function", 13: "Variable", 14: "Constant", 15: "String",
    16: "Number", 17: "Boolean", 18: "Array", 19: "Object", 20: "Key",
    21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event", 25: "Operator",
    26: "TypeParameter", 255: "Unknown"
}

class CCLSCodeNavigator:
    """
    A wrapper around the ccls Language Server and libclang to navigate C/C++ codebases.
    Provides functionality for definition lookup, call hierarchy, and tokenization.
    """

    def __init__(self, project_root: str, cache_path: str, logger: logging.Logger,
                 options: List[str] = None, config: DependencyBuilderConfig = None):
        self.project_root = os.path.abspath(project_root)
        self.cache_path = os.path.abspath(cache_path)
        self.ccls_options = options if options else []
        self.logger = logger
        self.config = config or DEFAULT_CONFIG
        self._metrics = get_metrics()

        self.opened_docs: Set[str] = set()
        self.index: Optional[Index] = None

        # 1. Initialize LibClang (required for tokenization)
        self._init_libclang()

        # 2. Start CCLS Process
        self.ccls_process = self._start_ccls_process()
        if not self.ccls_process:
            raise CCLSStartupError("ccls subprocess returned None")
        self._metrics.record_process_start()

        # 3. Setup JSON-RPC
        self.json_rpc_endpoint = JsonRpcEndpoint(self.ccls_process.stdin, self.ccls_process.stdout)

        self.notify_callbacks = {
            "window/workDoneProgress/create": work_done_progress_create_handler(logger),
            "$/progress": progress_handler(logger),
            "$ccls/publishSkippedRanges": skipped_ranges_handler(logger),
            "$ccls/publishSemanticHighlight": semantic_highlight_handler(logger),
            # Fix: Handle diagnostics to prevent 'Notify method not found' errors
            "textDocument/publishDiagnostics": lambda *args: None
        }

        self.lsp_endpoint = LspEndpoint(
            self.json_rpc_endpoint,
            notify_callbacks=self.notify_callbacks,
            timeout=self.config.lsp_endpoint_timeout
        )
        self.lsp_client = LspClient(self.lsp_endpoint)

        # 4. Initialize LSP Session
        self._initialize_lsp_session()
        self.lsp_client.initialized()

    def _init_libclang(self):
        """Attempts to load libclang from configured search paths."""
        try:
            # Try creating index directly
            try:
                self.index = Index.create()
                return
            except Exception:
                pass

            # Search configured paths (includes LIBCLANG_PATH env if set via config.from_env())
            possible_paths = list(self.config.libclang_search_paths)

            # Also check env directly for backward compatibility
            if os.environ.get("LIBCLANG_PATH"):
                possible_paths.insert(0, os.environ["LIBCLANG_PATH"])

            lib_loaded = False
            for path in possible_paths:
                if os.path.exists(path):
                    ClangConfig.set_library_file(path)
                    self.logger.info(f"libclang path set to: {path}")
                    self.index = Index.create()
                    lib_loaded = True
                    break

            if not lib_loaded:
                self.logger.warning(
                    "Could not find libclang.so. Tokenization features may fail. "
                    f"Searched: {possible_paths}"
                )
        except Exception as e:
            self.logger.error(f"Error initializing libclang: {e}")

    def _start_ccls_process(self) -> Optional[subprocess.Popen]:
        """Starts the ccls subprocess using configured executable and options."""
        try:
            self.logger.info("Starting ccls subprocess...")
            cmd = [
                self.config.ccls_executable,
                f"--log-file={self.config.ccls_log_file}",
                f"-v={self.config.ccls_verbosity}",
            ] + self.ccls_options

            # SECURITY FIX: Use start_new_session=True instead of preexec_fn=os.setsid
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )
            time.sleep(self.config.ccls_process_startup_delay)
            return process
        except Exception as e:
            self.logger.exception(f"Failed to start ccls: {e}")
            return None

    def _initialize_lsp_session(self):
        """Sends the initialize request to the LSP server."""
        try:
            root_uri = f"file://{self.project_root}"
            capabilities = {
                "workspace": {"workspaceFolders": True},
                "textDocument": {
                    "synchronization": {"didSave": True},
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                }
            }
            init_opts = {
                "cache": {"directory": self.cache_path},
                "index": {"threads": self.config.effective_index_threads},
                "client": {"snippetSupport": False}
            }
            
            self.logger.info("Sending LSP initialize request...")
            self.lsp_client.initialize(
                processId=self.ccls_process.pid,
                rootPath=self.project_root,
                rootUri=root_uri,
                capabilities=capabilities,
                initializationOptions=init_opts,
                workspaceFolders=[{"uri": root_uri, "name": Path(self.project_root).name}],
                trace="off"
            )
            time.sleep(0.5)
        except Exception as e:
            self.logger.exception(f"Failed to initialize LSP session: {e}")
            raise

    # --- Utility Methods ---

    def _clean_uri(self, uri: str) -> str:
        """Converts LSP URI to local file path (Absolute). Delegates to shared utils."""
        return clean_uri(uri)

    def _to_uri(self, path: str) -> str:
        """Converts local file path to LSP URI. Delegates to shared utils."""
        return to_uri(path)

    @lru_cache(maxsize=DEFAULT_CONFIG.file_cache_maxsize)
    def read_file(self, path: str) -> str:
        """
        Reads file content with caching. 
        Safeguarded against directories to prevent IsADirectoryError.
        """
        p = Path(path)
        if not p.exists():
            # Downgraded from error to warning to reduce log noise
            self.logger.warning(f"File not found: {path}")
            return ""
        
        if p.is_dir():
            self.logger.debug(f"Attempted to read directory as file: {path}")
            return ""
            
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            self.logger.error(f"Could not read file {path}: {e}")
            return ""

    def clear_file_cache(self) -> None:
        """Clear the read_file LRU cache to prevent memory leaks in long-running sessions."""
        self.read_file.cache_clear()
        self.logger.debug("File read cache cleared")

    def create_doc(self, path: str) -> Optional[TextDocumentItem]:
        """
        Creates a TextDocumentItem from a path (absolute or relative).
        """
        try:
            if os.path.isabs(path):
                src_path = path
            else:
                src_path = os.path.join(self.project_root, path)
            
            # Normalize path
            src_path = os.path.normpath(src_path)
            
            # Validate existence before operations to avoid errors
            if not os.path.exists(src_path):
                # START NEW LOGIC
                filename = os.path.basename(path)
                
                # Search for the file recursively in project_root
                found_paths = list(Path(self.project_root).rglob(filename))
                
                if found_paths:
                    # Use the first match (or add logic to handle duplicates)
                    src_path = str(found_paths[0])
                else:
                    self.logger.warning(f"create_doc failed, file does not exist: {src_path}")
                    return None

            # Prevent operations on directories
            if os.path.isdir(src_path):
                # Downgraded to debug
                self.logger.debug(f"create_doc called on directory, skipping: {src_path}")
                return None

            text = self.read_file(src_path)
            
            return TextDocumentItem(
                uri=self._to_uri(src_path), 
                languageId="cpp", 
                version=1, 
                text=text
            )
        except Exception as e:
            self.logger.error(f"Failed to create document for {path}: {e}")
            return None

    def openDoc(self, doc: TextDocumentItem) -> None:
        """Sends textDocument/didOpen if not already opened."""
        try:
            if doc.uri not in self.opened_docs:
                self.lsp_client.didOpen(doc)
                self.opened_docs.add(doc.uri)
        except Exception as e:
            self.logger.error(f"Failed to open document {doc.uri}: {e}")

    # --- LSP Features ---

    def getDocumentSymbolsKeySymbols(self, doc: TextDocumentItem) -> Dict[str, List[Dict]]:
        """Returns symbols grouped by name."""
        try:
            results = self.lsp_client.lsp_endpoint.call_method(
                "textDocument/documentSymbol", textDocument=doc
            )
            symbols = defaultdict(list)
            for item in results:
                kind_id = item.get("kind", 255)
                item["kind"] = SYMBOL_KIND_MAP.get(kind_id, "Unknown")
                symbols[item["name"]].append(item)
            return symbols
        except Exception as e:
            self.logger.error(f"Failed to get symbols: {e}")
            return defaultdict(list)

    def getDocumentSymbolsKeyLines(self, doc: TextDocumentItem) -> Dict[int, List[Dict]]:
        """Returns symbols grouped by start line."""
        try:
            results = self.lsp_client.lsp_endpoint.call_method(
                "textDocument/documentSymbol", textDocument=doc
            )
            symbols = defaultdict(list)
            for item in results:
                kind_id = item.get("kind", 255)
                item["kind"] = SYMBOL_KIND_MAP.get(kind_id, "Unknown")
                line = item["location"]["range"]["start"]["line"]
                symbols[line].append(item)
            return symbols
        except Exception:
            return defaultdict(list)

    def getDocandPosFromSymbol(self, sym: Dict) -> Tuple[Optional[TextDocumentItem], Optional[Position]]:
        """Extracts Document and Position from a symbol dictionary."""
        try:
            uri = sym.get("location", {}).get("uri") or sym.get("uri")
            rng = sym.get("location", {}).get("range") or sym.get("range")
            
            if not uri or not rng:
                return None, None

            file_path = self._clean_uri(uri)
            text = self.read_file(file_path)
            
            doc = TextDocumentItem(uri=uri, languageId="cpp", version=1, text=text)
            
            # Calculate 'center' of the symbol for token matching
            avg_char = math.floor(statistics.mean([
                rng["start"]["character"], 
                rng["end"]["character"]
            ]))
            pos = Position(line=rng["start"]["line"], character=avg_char)
            
            return doc, pos
        except Exception as e:
            self.logger.error(f"Error resolving symbol pos: {e}")
            return None, None

    def getDefinition(self, doc: TextDocumentItem, pos: Position) -> str:
        """Fetches the source code of the definition at the given position."""
        try:
            self.openDoc(doc)
            # Role 8 = Definition in ccls
            result_dict = self.lsp_client.lsp_endpoint.call_method(
                "$ccls/navigate", textDocument=doc, position=pos, role=8
            )
            
            # Fallback: Try role 1 (Declaration) if Definition empty
            if not result_dict:
                # Use a copy to avoid mutating the caller's Position object
                shifted_pos = copy(pos)
                shifted_pos.character += 1
                result_dict = self.lsp_client.lsp_endpoint.call_method(
                    "$ccls/navigate", textDocument=doc, position=shifted_pos, role=1
                )

            if not result_dict:
                return ""

            target = result_dict[0]
            target_path = self._clean_uri(target["uri"])
            text = self.read_file(target_path).splitlines()
            
            start_line = target["range"]["start"]["line"]
            end_line = target["range"]["end"]["line"]
            
            # Safeguard against OOB
            if start_line < len(text):
                return "\n".join(text[start_line : end_line + 1])
            return ""
        except Exception as e:
            self.logger.error(f"Failed to get definition text: {e}")
            return ""

    def getCallee(self, doc: TextDocumentItem, pos: Position, level: int = 5) -> Any:
        try:
            self.openDoc(doc)
            return self.lsp_client.lsp_endpoint.call_method(
                "$ccls/call", textDocument=doc, position=pos, callee=True, hierarchy=True, levels=level
            )
        except Exception as e:
            self.logger.error(f"Failed to get callee: {e}")
            return None

    def getCaller(self, doc: TextDocumentItem, pos: Position, level: int = 5) -> Any:
        try:
            self.openDoc(doc)
            return self.lsp_client.lsp_endpoint.call_method(
                "$ccls/call", textDocument=doc, position=pos, callee=False, hierarchy=True, levels=level
            )
        except Exception as e:
            self.logger.error(f"Failed to get caller: {e}")
            return None

    def getDefinitionFromToken(self, doc: TextDocumentItem, pos: Position) -> Tuple[Optional[TextDocumentItem], Optional[Position], str]:
        """Wrapper to get definition details starting from a token position."""
        try:
            syms = self.lsp_client.lsp_endpoint.call_method(
                "textDocument/definition", textDocument=doc, position=pos
            )
            if not syms:
                return None, None, ""
            
            ddoc, dpos = self.getDocandPosFromSymbol(syms[0])
            if ddoc and dpos:
                definition = self.getDefinition(ddoc, dpos)
                return ddoc, dpos, definition
            return None, None, ""
        except Exception as e:
            self.logger.error(f"Failed getDefinitionFromToken: {e}")
            return None, None, ""

    def getTokens(self, source_code: str, doc: TextDocumentItem, pos: Position) -> Dict[str, Any]:
        """
        Uses libclang to parse a snippet of code and extract tokens.
        Useful for analyzing variables within a function body.
        """
        tokens = {}
        if not self.index:
            self.logger.warning("Libclang index not initialized, cannot get tokens.")
            return tokens

        try:
            # Parse the snippet as a virtual C++ file
            # Use .cpp extension to ensure C++ parsing mode
            virt_name = self.config.virtual_snippet_filename
            tu = self.index.parse(virt_name, unsaved_files=[(virt_name, source_code)])
            
            for token in tu.get_tokens(extent=tu.cursor.extent):
                if token.kind.name in {"PUNCTUATION", "COMMENT", "KEYWORD", "LITERAL"}:
                    continue
                
                # Calculate absolute line in the original file
                abs_line = pos.line + token.extent.start.line - 1
                
                # Calculate avg column
                avg_col = math.floor(statistics.mean([token.extent.end.column, token.extent.start.column]))

                # Generate a unique ID for the token
                token_id = xxhash.xxh64(f"{doc.uri}:{token.spelling}:{abs_line}").hexdigest()

                tokens[token.spelling] = {
                    'id': token_id,
                    'name': token.spelling,
                    'uri': doc.uri,
                    'line': abs_line,
                    'character': avg_col,
                    'callType': 0,
                    'numChildren': 0,
                    'children': [],
                    "kind": token.cursor.kind.name,
                }
            return tokens
        except Exception as e:
            self.logger.error(f"Tokenization failed: {e}")
            return {}

    def get_name(self, doc: TextDocumentItem, pos: Position) -> str:
        """Attempts to retrieve the symbol name via Hover request."""
        try:
            res = self.lsp_client.lsp_endpoint.call_method(
                "textDocument/hover", textDocument=doc, position=pos, role=8
            )
            
            if not res or not res.get('contents'):
                return ""

            contents = res['contents']
            # Handle various Hover content formats
            try:
                if isinstance(contents, list) and contents:
                    first = contents[0]
                    last = contents[-1]
                    # Logic from original snippet to extract value from dict or list
                    if isinstance(first, dict) and 'value' in first:
                        return first['value'].strip()
                    elif isinstance(last, dict) and 'value' in last:
                        return last['value'].strip()
                    else:
                        return str(first).strip()
                elif isinstance(contents, dict):
                    return contents.get('value', '').strip()
                else:
                    return str(contents).strip()
            except Exception:
                # Fallback
                if isinstance(contents, list) and contents:
                    return str(contents[0]).strip()
                return str(contents).strip()

        except Exception as e:
            self.logger.error(f"Failed to get_name: {e}")
            return ""

    def _process_call_flow_child(self, token: Dict, parent_name: str, current_depth: int, max_depth: int, visited: Set) -> Optional[Dict]:
        """Helper to process a child token and recurse."""
        abs_path = self._clean_uri(token["uri"])
        token_doc = self.create_doc(abs_path)
        if not token_doc: 
            return None

        # Find where this token is defined
        tdoc, tpos, _ = self.getDefinitionFromToken(
            token_doc, 
            Position(line=token["line"], character=token["character"])
        )
        
        if tdoc and tpos:
            if token["name"] == parent_name:
                # Return ID only to update root if self-reference
                return {"id": token["id"], "is_self": True}
            else:
                # Child Node
                child = {
                    'id': token["id"],
                    'name': token["name"],
                    'location': {'uri': token["uri"], 'range': {'start': {'line': token["line"], 'character': token["character"]}, 'end': {'line': token["line"], 'character': token["character"]}}},
                    'callType': token["callType"],
                    'numChildren': 0,
                    'children': []
                }
                
                if current_depth < max_depth:
                    nested = self.create_call_flow_from_token(
                        tdoc, tpos, current_depth=current_depth+1, max_depth=max_depth, visited=visited
                    )
                    if nested and nested.get("children"):
                        child['children'] = nested['children']
                        child['numChildren'] = len(child['children'])
                
                return {"node": child, "is_self": False}
        return None

    def create_call_flow_from_token(self, doc: TextDocumentItem, pos: Position, current_depth: int = 0, max_depth: int = 1, visited: Set = None) -> Optional[Dict]:
        """
        Recursively builds a call flow graph by inspecting tokens inside a function's definition.
        REFACTORED: Reduced complexity by extracting child processing.
        """
        if visited is None:
            visited = set()
            
        node_id = (doc.uri, pos.line, pos.character)
        if node_id in visited:
            return None
        visited.add(node_id)
        
        try:
            definition = self.getDefinition(doc, pos)
            tokens = self.getTokens(definition, doc, pos)
            name = self.get_name(doc, pos).strip()
            
            # Root Node
            call_flow = {
                'id': None, 'name': name,
                'location': {'uri': doc.uri, 'range': {'start': {'line': pos.line, 'character': pos.character}, 'end': {'line': pos.line, 'character': pos.character}}},
                'callType': None, 'numChildren': 0, 'children': []
            }
            
            for token in tokens.values():
                result = self._process_call_flow_child(token, name, current_depth, max_depth, visited)
                if result:
                    if result.get("is_self"):
                        call_flow["id"] = result["id"]
                    elif result.get("node"):
                        call_flow["children"].append(result["node"])

            call_flow["numChildren"] = len(call_flow["children"])
            
            # Generate ID if missing (fallback)
            if not call_flow.get("id"):
                call_flow["id"] = xxhash.xxh64(f"{doc.uri}:{name}:{pos.line}").hexdigest()
                
            return call_flow
            
        except Exception as e:
            self.logger.error(f"Error creating call flow: {e}")
            return None

    def get_references_recursive(self, doc: TextDocumentItem, pos: Position, visited: Set = None, depth: int = 0, max_depth: int = None) -> List[Dict]:
        """Recursive find references."""
        if visited is None:
            visited = set()
        if max_depth is None:
            max_depth = self.config.max_reference_depth

        if depth > max_depth:
            return []

        current_key = f"{doc.uri}:{pos.line}:{pos.character}"
        if current_key in visited:
            return []
        visited.add(current_key)

        results = []
        try:
            refs = self.lsp_client.lsp_endpoint.call_method(
                "textDocument/references", textDocument=doc, position=pos, context={"includeDeclaration": True}
            )
            if not refs: 
                return []

            for res in refs:
                child_doc, child_pos = self.getDocandPosFromSymbol(res)
                if not child_doc: 
                    continue
                    
                child_name = self.get_name(child_doc, child_pos).strip()
                node_id = xxhash.xxh64(f"{child_doc.uri}:{child_name}:{res['range']['start']['line']}").hexdigest()

                node = {
                    'id': node_id,
                    'name': child_name,
                    'location': {'uri': child_doc.uri, 'range': res['range']},
                    'children': self.get_references_recursive(child_doc, child_pos, visited, depth + 1, max_depth)
                }
                node['numChildren'] = len(node['children'])
                results.append(node)

        except Exception as e:
            self.logger.error(f"Recursive ref fetch failed: {e}")
            
        return results

    def killCCLSProcess(self):
        """Cleanly shuts down the LSP client and kills the ccls process."""
        # 1. Attempt graceful LSP shutdown
        try:
            self.logger.info("Shutting down CCLS...")
            self.lsp_client.shutdown()
            self.lsp_client.exit()
        except Exception:
            pass

        # 2. Clean up the subprocess
        try:
            if self.ccls_process and self.ccls_process.poll() is None:
                # Process is still running - try SIGTERM first
                try:
                    pgid = os.getpgid(self.ccls_process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    self.ccls_process.wait(timeout=self.config.sigterm_timeout)
                except subprocess.TimeoutExpired:
                    # SIGTERM didn't work, escalate to SIGKILL
                    self.logger.warning("CCLS did not respond to SIGTERM, sending SIGKILL")
                    try:
                        pgid = os.getpgid(self.ccls_process.pid)
                        os.killpg(pgid, signal.SIGKILL)
                        self.ccls_process.wait(timeout=self.config.sigkill_timeout)
                    except Exception:
                        pass
                except ProcessLookupError:
                    # Process already exited
                    pass
        except Exception as e:
            self.logger.error(f"Error during process cleanup: {e}")

        # 3. Record metrics
        try:
            self._metrics.record_process_kill()
        except Exception:
            pass

        # 4. Clear the file read cache to free memory
        try:
            self.clear_file_cache()
        except Exception:
            pass

    def __del__(self):
        """Destructor to ensure process cleanup."""
        try:
            self.killCCLSProcess()
        except Exception:
            # Suppress errors during GC - resources may already be cleaned up
            pass