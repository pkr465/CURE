"""
C/C++ documentation analysis (enhanced)
"""

import os
import re
from typing import Dict, List, Any, Tuple


class DocumentationAnalyzer:
    """
    Analyzes C/C++ code documentation quality focusing on:
    - Doxygen-style documentation coverage
    - Function parameter and return documentation
    - Header file documentation (public API)
    - File-level documentation
    - Comment and documentation density
    - README presence and basic quality (length-based heuristic)
    """

    # C/C++ file extensions
    C_EXTS = {".c", ".cpp", ".cc", ".cxx", ".c++"}
    H_EXTS = {".h", ".hpp", ".hh", ".hxx", ".h++"}

    def __init__(self, codebase_path: str = None, project_root: str = None):
        """Initialize documentation analyzer."""
        self.codebase_path = codebase_path or os.getcwd()
        self.project_root = project_root or os.getcwd()
        self._file_cache: List[Dict[str, Any]] = []

    def analyze(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze C/C++ documentation quality.

        Args:
            file_cache: List of processed C/C++ file entries. Each entry should include:
                - "suffix": file extension
                - "source": file contents
                - one of: "rel_path", "path", "file_relative_path", "file_name"

        Returns:
            Documentation analysis results with score, grade, metrics, and issues.
        """
        self._file_cache = file_cache or []
        return self._calculate_documentation_score()

    def _calculate_documentation_score(self) -> Dict[str, Any]:
        """
        C/C++ documentation coverage and quality score (Doxygen-centric).

        Analyzes C/C++ sources and headers for comprehensive documentation:
        - Detects Doxygen blocks (/** ... */ and /*! ... */ and ///) preceding functions, types, and macros
        - Computes coverage for public API in headers vs implementation files
        - Checks @param tags coverage, and @return for non-void functions
        - Detects file-level documentation (@file/@brief near top)
        - Aggregates per-file metrics, issues, and an overall score/grade
        """
        if not self._file_cache:
            return {"score": 0, "grade": "F", "issues": ["No files cached"]}

        # Helper: relative path
        def _rel_path(entry: Dict[str, Any]) -> str:
            p = (
                entry.get("rel_path")
                or entry.get("path")
                or entry.get("file_relative_path")
                or entry.get("file_name")
                or ""
            )
            root = (
                getattr(self, "project_root", None)
                or getattr(self, "root_dir", None)
                or str(self.codebase_path)
            )
            try:
                return os.path.relpath(p, root) if p else ""
            except Exception:
                return p or ""

        # Filter C/C++ files
        all_exts = {ext.lower() for ext in (self.C_EXTS | self.H_EXTS)}
        c_cpp_files: List[Dict[str, Any]] = []
        for f in self._file_cache:
            suffix = (f.get("suffix") or "").lower()
            if suffix in all_exts:
                c_cpp_files.append(f)

        print(
            f"DEBUG documentation: Found {len(c_cpp_files)} C/C++ files "
            f"out of {len(self._file_cache)} total"
        )

        # Handle no C/C++ files: fallback to README only
        if not c_cpp_files:
            readme_notes = ""
            if getattr(self, "summary", None):
                readme_notes = self.summary.get("readme_notes", "") or ""

            if readme_notes:
                # Basic length-based scoring
                if len(readme_notes) > 1000:
                    readme_score = 100
                elif len(readme_notes) > 500:
                    readme_score = 90
                elif len(readme_notes) > 200:
                    readme_score = 75
                else:
                    readme_score = 60

                final_score = readme_score * 0.3
                return {
                    "score": round(final_score, 1),
                    "grade": self._score_to_grade(final_score),
                    "issues": [
                        "No C/C++ files found for documentation analysis. "
                        f"Extensions checked: {sorted(self.C_EXTS | self.H_EXTS)}"
                    ],
                    "metrics": {
                        "documentation_ratio": 0.0,
                        "header_documentation_ratio": 0.0,
                        "param_full_coverage_ratio": 0.0,
                        "return_doc_ratio": 1.0,
                        "total_documentable_items": 0,
                        "documented_items": 0,
                        "header_items": 0,
                        "header_documented_items": 0,
                        "readme_length": len(readme_notes),
                        "files": [],
                        "missing_items": [],
                    },
                }
            else:
                return {
                    "score": 0.0,
                    "grade": "F",
                    "issues": [
                        "No C/C++ files found and no README. "
                        f"Extensions checked: {sorted(self.C_EXTS | self.H_EXTS)}"
                    ],
                    "metrics": {
                        "documentation_ratio": 0.0,
                        "header_documentation_ratio": 0.0,
                        "param_full_coverage_ratio": 0.0,
                        "return_doc_ratio": 1.0,
                        "total_documentable_items": 0,
                        "documented_items": 0,
                        "header_items": 0,
                        "header_documented_items": 0,
                        "readme_length": 0,
                        "files": [],
                        "missing_items": [],
                    },
                }

        # Thresholds / weights
        THR = {
            "comment_ratio_low": 0.10,     # below 10% comment lines is low
            "doc_density_low": 0.02,       # doc lines vs code lines
            "header_weight": 0.25,         # headers weighted more (public API)
            "coverage_weight": 0.40,       # overall coverage
            "file_header_weight": 0.10,    # file-level header docs
            "param_return_weight": 0.15,   # param/return completeness
            "readme_weight": 0.05,         # README weight
            "quality_weight": 0.05,        # doc block quality/density
        }

        # Regexes
        sig_re = re.compile(
            r"""
            (?P<signature>
                (?:^[ \t]*(?:template\s*<[^>]*>\s*)*)? # optional template
                [^\n;{}()]*?                           # qualifiers/return type
                (?P<name>[A-Za-z_~][\w:]*)
                \s*\(
                (?P<params>[^;{}()]*)                  # parameters (no nested parens)
                \)
                (?:\s*const)?(?:\s*noexcept)?(?:\s*->\s*[^({]+)? # qualifiers/trailing return
            )
            \s*\{                                      # function body start
        """,
            re.VERBOSE | re.MULTILINE | re.DOTALL,
        )

        decl_re = re.compile(
            r"""
            (?P<signature>
                (?:^[ \t]*(?:template\s*<[^>]*>\s*)*)?
                [^\n;{}()]*?
                (?P<name>[A-Za-z_~][\w:]*)
                \s*\(
                (?P<params>[^;{}()]*)
                \)
                (?:\s*const)?(?:\s*noexcept)?(?:\s*->\s*[^({]+)?
            )
            \s*;                                       # declaration
        """,
            re.VERBOSE | re.MULTILINE | re.DOTALL,
        )

        type_re = re.compile(r"^\s*(class|struct|enum)\s+([A-Za-z_]\w*)\b", re.MULTILINE)
        typedef_re = re.compile(
            r"^\s*typedef\b[^{;]*\b([A-Za-z_]\w*)\s*;", re.MULTILINE
        )
        macro_re = re.compile(
            r"^\s*#\s*define\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE
        )

        CONTROL_KEYWORDS = {"if", "for", "while", "switch", "catch"}

        def _count_comment_lines(src: str) -> int:
            """Count lines that are comments (// or block /* ... */ lines)."""
            lines = src.splitlines()
            count = 0
            in_ml = False

            for ln in lines:
                s = ln.strip()
                if in_ml:
                    count += 1
                    if "*/" in s:
                        in_ml = False
                    continue

                if s.startswith("//"):
                    count += 1
                    continue

                if "/*" in s:
                    count += 1
                    if "*/" not in s:
                        in_ml = True

            return count

        def _count_doxygen_lines(src: str) -> int:
            """
            Count lines that look like Doxygen documentation (/**, /*! or ///).
            This is an approximation of doc-comment density.
            """
            lines = src.splitlines()
            count = 0
            in_dox_block = False

            for ln in lines:
                s = ln.strip()
                if s.startswith("///"):
                    count += 1
                    continue

                if "/**" in s or "/*!" in s:
                    in_dox_block = True
                    count += 1
                    # If ends on same line, end block
                    if "*/" in s and s.index("*/") > s.index("/*"):
                        in_dox_block = False
                    continue

                if in_dox_block:
                    count += 1
                    if "*/" in s:
                        in_dox_block = False

            return count

        def _file_has_header_doc(src: str) -> bool:
            """Check if top-of-file has a Doxygen-style header block with @file or @brief."""
            head = "\n".join(src.splitlines()[:120])
            return (
                re.search(r"/\*\*(.|\n)*?@(file|brief)", head) is not None
                or re.search(r"///\s*@(?:file|brief)", head) is not None
            )

        def _has_doxygen_block_before(
            source: str, start_idx: int, lookback_lines: int = 15
        ) -> Tuple[bool, str, int]:
            """Check if a Doxygen block/comment exists in the preceding lines."""
            lines = source.splitlines(True)
            start_line = source.count("\n", 0, start_idx) + 1
            first_line_to_check = max(1, start_line - lookback_lines)
            segment = "".join(
                lines[first_line_to_check - 1 : start_line - 1]
            )

            # /** or /*! block
            m_block = re.search(r"/\*\*.*?\*/|/\*!\s*.*?\*/", segment, re.DOTALL)
            if m_block:
                doc_start_line = first_line_to_check
                return True, m_block.group(0), doc_start_line

            # Consecutive /// lines
            doc_lines = []
            for ln in segment.splitlines():
                if ln.strip().startswith("///"):
                    doc_lines.append(ln.strip())

            if doc_lines:
                return True, "\n".join(doc_lines), first_line_to_check

            return False, "", 0

        def _extract_param_names(params: str) -> List[str]:
            """Extract parameter names from C/C++ signature heuristically."""
            txt = params.strip()
            if not txt or txt == "void":
                return []

            # Normalize
            txt = re.sub(r"\[\[.*?\]\]", "", txt)          # remove attributes
            txt = re.sub(r"=\s*[^,]+", "", txt)            # remove defaults

            parts = [p.strip() for p in txt.split(",") if p.strip()]
            names: List[str] = []
            for p in parts:
                p2 = p.replace("*", " ").replace("&", " ")
                p2 = re.sub(
                    r"\b(const|volatile|static|register|struct|class)\b", " ", p2
                )
                ids = re.findall(r"\b[A-Za-z_]\w*\b", p2)
                if ids:
                    names.append(ids[-1])
            return names

        def _is_constructor_or_destructor(name: str, signature: str) -> bool:
            """Heuristic: constructors/destructors in C++ have name == class or start with ~."""
            if name.startswith("~"):
                return True
            m = re.search(r"([A-Za-z_]\w*)::" + re.escape(name), signature)
            return bool(m)

        def _count_doc_description_lines(doc_text: str) -> int:
            """
            Count non-tag description lines in a Doxygen block.
            Lines with only tags (@param/@return/@brief, etc.) are NOT counted.
            """
            if not doc_text:
                return 0
            cnt = 0
            for ln in doc_text.splitlines():
                s = ln.strip().lstrip("/*").lstrip("*").strip()
                if not s:
                    continue
                # skip pure tag lines
                if re.match(r"^[@\\](param|return|brief|file|ingroup|tparam|see)", s):
                    continue
                cnt += 1
            return cnt

        # Aggregation containers
        per_file: List[Dict[str, Any]] = []
        missing_items: List[Dict[str, Any]] = []

        total_items = 0
        documented_items = 0
        header_items = 0
        header_documented = 0

        # Per-kind counts
        total_func_impl = total_func_impl_doc = 0
        total_func_decl = total_func_decl_doc = 0
        total_types = total_types_doc = 0
        total_typedefs = total_typedefs_doc = 0
        total_macros = total_macros_doc = 0

        # Param/return coverage
        total_param_funcs_with_docs = 0
        func_with_full_param_docs = 0
        func_needing_return_doc = 0
        func_with_return_doc = 0

        # Doc quality / density
        total_doc_blocks = 0
        doc_blocks_with_description = 0

        print(f"DEBUG documentation: Starting analysis of {len(c_cpp_files)} files")

        for entry in c_cpp_files:
            source = entry.get("source", "") or ""
            if not source.strip():
                rel = _rel_path(entry)
                print(f"DEBUG documentation: Skipping empty file: {rel or 'unknown'}")
                continue

            suffix = (entry.get("suffix") or "").lower()
            rel = _rel_path(entry)
            is_header = suffix.endswith((".h", ".hpp", ".hh", ".hxx"))

            print(
                f"DEBUG documentation: Analyzing {rel} "
                f"({'header' if is_header else 'source'}, {len(source)} chars)"
            )

            lines = source.splitlines()
            comment_lines = _count_comment_lines(source)
            doxygen_lines = _count_doxygen_lines(source)
            code_lines = sum(1 for ln in lines if ln.strip())
            comment_ratio = comment_lines / max(1, len(lines))
            doc_density = doxygen_lines / max(1, code_lines)

            # File-level header doc
            file_header_doc = _file_has_header_doc(source)
            total_items += 1
            file_total_items = 1
            file_documented_items = 1 if file_header_doc else 0

            if file_header_doc:
                documented_items += 1
                if is_header:
                    header_documented += 1
                    header_items += 1

            # Types
            type_count = 0
            for m in type_re.finditer(source):
                kind = m.group(1)
                name = m.group(2)
                start_idx = m.start()
                has_doc, doc_text, _ = _has_doxygen_block_before(source, start_idx)

                total_items += 1
                file_total_items += 1
                type_count += 1
                total_types += 1
                if is_header:
                    header_items += 1

                if has_doc:
                    documented_items += 1
                    file_documented_items += 1
                    total_types_doc += 1
                    if is_header:
                        header_documented += 1

                    total_doc_blocks += 1
                    if _count_doc_description_lines(doc_text) >= 2:
                        doc_blocks_with_description += 1
                else:
                    missing_items.append(
                        {
                            "file": rel,
                            "line": source.count("\n", 0, start_idx) + 1,
                            "kind": kind,
                            "name": name,
                            "reason": f"Missing Doxygen for {kind} {name}",
                        }
                    )

            # Typedefs
            typedef_count = 0
            for m in typedef_re.finditer(source):
                name = m.group(1)
                start_idx = m.start()
                has_doc, doc_text, _ = _has_doxygen_block_before(source, start_idx)

                total_items += 1
                file_total_items += 1
                typedef_count += 1
                total_typedefs += 1
                if is_header:
                    header_items += 1

                if has_doc:
                    documented_items += 1
                    file_documented_items += 1
                    total_typedefs_doc += 1
                    if is_header:
                        header_documented += 1

                    total_doc_blocks += 1
                    if _count_doc_description_lines(doc_text) >= 2:
                        doc_blocks_with_description += 1
                else:
                    missing_items.append(
                        {
                            "file": rel,
                            "line": source.count("\n", 0, start_idx) + 1,
                            "kind": "typedef",
                            "name": name,
                            "reason": f"Missing Doxygen for typedef {name}",
                        }
                    )

            # Macros with parameters
            macro_count = 0
            for m in macro_re.finditer(source):
                name = m.group(1)
                start_idx = m.start()
                has_doc, doc_text, _ = _has_doxygen_block_before(source, start_idx)

                total_items += 1
                file_total_items += 1
                macro_count += 1
                total_macros += 1
                if is_header:
                    header_items += 1

                if has_doc:
                    documented_items += 1
                    file_documented_items += 1
                    total_macros_doc += 1
                    if is_header:
                        header_documented += 1

                    total_doc_blocks += 1
                    if _count_doc_description_lines(doc_text) >= 2:
                        doc_blocks_with_description += 1
                else:
                    missing_items.append(
                        {
                            "file": rel,
                            "line": source.count("\n", 0, start_idx) + 1,
                            "kind": "macro",
                            "name": name,
                            "reason": f"Missing Doxygen for macro {name}",
                        }
                    )

            # Function implementations
            func_impl_count = 0
            for m in sig_re.finditer(source):
                name = m.group("name")
                if name in CONTROL_KEYWORDS:
                    continue

                params = m.group("params") or ""
                param_names = _extract_param_names(params)
                start_idx = m.start()
                has_doc, doc_text, _ = _has_doxygen_block_before(source, start_idx)

                total_items += 1
                file_total_items += 1
                func_impl_count += 1
                total_func_impl += 1
                if is_header:
                    header_items += 1

                needs_return = not _is_constructor_or_destructor(
                    name, m.group("signature")
                )
                pre_sig = m.group("signature")
                if re.search(r"\bvoid\b", pre_sig.split(name)[0]):
                    needs_return = False

                param_tags = []
                return_tag = False
                if has_doc:
                    documented_items += 1
                    file_documented_items += 1
                    total_func_impl_doc += 1
                    if is_header:
                        header_documented += 1

                    total_doc_blocks += 1
                    if _count_doc_description_lines(doc_text) >= 2:
                        doc_blocks_with_description += 1

                    param_tags = re.findall(
                        r"[@\\]param\s+([A-Za-z_]\w*)", doc_text or ""
                    )
                    return_tag = (
                        re.search(r"[@\\]return\b", doc_text or "") is not None
                    )

                    # Param coverage
                    if param_names:
                        total_param_funcs_with_docs += 1
                        covered = sum(
                            1 for p in param_names if p in set(param_tags)
                        )
                        if covered == len(param_names):
                            func_with_full_param_docs += 1
                        else:
                            missing_params = [
                                p for p in param_names if p not in set(param_tags)
                            ]
                            missing_items.append(
                                {
                                    "file": rel,
                                    "line": source.count("\n", 0, start_idx) + 1,
                                    "kind": "function",
                                    "name": name,
                                    "reason": (
                                        f"Missing @param for "
                                        f"{len(param_names) - covered} parameter(s): "
                                        f"{', '.join(missing_params)}"
                                    ),
                                }
                            )

                    # Return coverage
                    if needs_return:
                        func_needing_return_doc += 1
                        if return_tag:
                            func_with_return_doc += 1
                        else:
                            missing_items.append(
                                {
                                    "file": rel,
                                    "line": source.count("\n", 0, start_idx) + 1,
                                    "kind": "function",
                                    "name": name,
                                    "reason": "Missing @return documentation",
                                }
                            )
                else:
                    if param_names or needs_return:
                        missing_items.append(
                            {
                                "file": rel,
                                "line": source.count("\n", 0, start_idx) + 1,
                                "kind": "function",
                                "name": name,
                                "reason": "Missing Doxygen for function",
                            }
                        )

            # Function declarations (header public API)
            func_decl_count = 0
            if is_header:
                for m in decl_re.finditer(source):
                    name = m.group("name")
                    if name in CONTROL_KEYWORDS:
                        continue

                    params = m.group("params") or ""
                    param_names = _extract_param_names(params)
                    start_idx = m.start()
                    has_doc, doc_text, _ = _has_doxygen_block_before(source, start_idx)

                    total_items += 1
                    file_total_items += 1
                    header_items += 1
                    func_decl_count += 1
                    total_func_decl += 1

                    if has_doc:
                        documented_items += 1
                        file_documented_items += 1
                        header_documented += 1
                        total_func_decl_doc += 1

                        total_doc_blocks += 1
                        if _count_doc_description_lines(doc_text) >= 2:
                            doc_blocks_with_description += 1

                        if param_names:
                            param_tags = re.findall(
                                r"[@\\]param\s+([A-Za-z_]\w*)", doc_text or ""
                            )
                            covered = sum(
                                1 for p in param_names if p in set(param_tags)
                            )
                            total_param_funcs_with_docs += 1
                            if covered == len(param_names):
                                func_with_full_param_docs += 1
                            else:
                                missing_params = [
                                    p for p in param_names if p not in set(param_tags)
                                ]
                                missing_items.append(
                                    {
                                        "file": rel,
                                        "line": source.count("\n", 0, start_idx) + 1,
                                        "kind": "function_decl",
                                        "name": name,
                                        "reason": (
                                            "Missing @param for "
                                            f"{len(param_names) - covered} parameter(s): "
                                            f"{', '.join(missing_params)}"
                                        ),
                                    }
                                )
                    else:
                        missing_items.append(
                            {
                                "file": rel,
                                "line": source.count("\n", 0, start_idx) + 1,
                                "kind": "function_decl",
                                "name": name,
                                "reason": "Missing Doxygen for function declaration (public API)",
                            }
                        )

            file_doc_ratio = file_documented_items / max(1, file_total_items)

            per_file.append(
                {
                    "file": rel,
                    "comment_ratio": round(comment_ratio, 3),
                    "doc_density": round(doc_density, 3),
                    "file_header_documented": file_header_doc,
                    "total_items": file_total_items,
                    "documented_items": file_documented_items,
                    "documentation_ratio": round(file_doc_ratio, 3),
                    "types": type_count,
                    "typedefs": typedef_count,
                    "macros": macro_count,
                    "functions_impl": func_impl_count,
                    "functions_decl": func_decl_count if is_header else 0,
                }
            )

            print(
                f"DEBUG documentation: File {rel} - Items: {file_total_items}, "
                f"Documented: {file_documented_items}, Ratio: {file_doc_ratio:.1%}, "
                f"Doc density: {doc_density:.3f}"
            )

        print(f"DEBUG documentation: Total items: {total_items}, Documented: {documented_items}")

        # README analysis
        readme_notes = ""
        if getattr(self, "summary", None):
            readme_notes = self.summary.get("readme_notes", "") or ""

        if readme_notes:
            if len(readme_notes) > 1000:
                readme_score = 100
            elif len(readme_notes) > 500:
                readme_score = 90
            elif len(readme_notes) > 200:
                readme_score = 75
            else:
                readme_score = 60
        else:
            readme_score = 0

        print(
            f"DEBUG documentation: README score: {readme_score} "
            f"(length: {len(readme_notes)})"
        )

        # Coverage ratios
        overall_doc_ratio = (documented_items / total_items) if total_items > 0 else 0.0
        header_doc_ratio = (
            header_documented / header_items if header_items > 0 else 0.0
        )

        func_impl_coverage = (
            total_func_impl_doc / total_func_impl if total_func_impl > 0 else 0.0
        )
        func_decl_coverage = (
            total_func_decl_doc / total_func_decl if total_func_decl > 0 else 0.0
        )
        type_coverage = (
            total_types_doc / total_types if total_types > 0 else 0.0
        )
        macro_coverage = (
            total_macros_doc / total_macros if total_macros > 0 else 0.0
        )

        # Param/return coverage based on functions-with-params that have docs
        param_full_ratio = (
            func_with_full_param_docs / max(1, total_param_funcs_with_docs)
            if total_param_funcs_with_docs > 0
            else 0.0
        )
        return_doc_ratio = (
            func_with_return_doc / max(1, func_needing_return_doc)
            if func_needing_return_doc > 0
            else 1.0
        )

        avg_doc_density = (
            sum(f["doc_density"] for f in per_file) / max(1, len(per_file))
        )
        quality_doc_ratio = (
            doc_blocks_with_description / total_doc_blocks
            if total_doc_blocks > 0
            else 0.0
        )

        print(
            "DEBUG documentation: Overall: "
            f"{overall_doc_ratio:.1%}, Header: {header_doc_ratio:.1%}, "
            f"Func impl: {func_impl_coverage:.1%}, Func decl: {func_decl_coverage:.1%}, "
            f"Param full: {param_full_ratio:.1%}, Return: {return_doc_ratio:.1%}, "
            f"Avg doc density: {avg_doc_density:.3f}, Quality docs: {quality_doc_ratio:.1%}"
        )

        # Scoring helpers
        def to_bucket_score(r: float) -> int:
            if r >= 0.8:
                return 100
            if r >= 0.6:
                return 85
            if r >= 0.4:
                return 65
            if r >= 0.2:
                return 45
            return 25

        coverage_score = to_bucket_score(overall_doc_ratio)
        header_score = to_bucket_score(header_doc_ratio)
        file_header_score = int(
            100
            * (
                sum(1 for f in per_file if f["file_header_documented"])
                / max(1, len(per_file))
            )
        )
        param_return_score = int(
            0.6 * to_bucket_score(param_full_ratio)
            + 0.4 * to_bucket_score(return_doc_ratio)
        )
        density_score = to_bucket_score(avg_doc_density / 0.1)  # scale up doc density
        quality_score = to_bucket_score(quality_doc_ratio)
        readme_score_weighted = readme_score

        overall_score = (
            coverage_score * THR["coverage_weight"]
            + header_score * THR["header_weight"]
            + file_header_score * THR["file_header_weight"]
            + param_return_score * THR["param_return_weight"]
            + readme_score_weighted * THR["readme_weight"]
            + 0.5 * density_score * THR["quality_weight"]
            + 0.5 * quality_score * THR["quality_weight"]
        )

        grade = self._score_to_grade(overall_score)
        print(f"DEBUG documentation: Final score: {overall_score:.1f}")

        # Issues
        issues: List[str] = []
        if overall_doc_ratio < 0.5:
            issues.append(
                f"Low overall documentation coverage: {overall_doc_ratio:.1%}"
            )
        if header_doc_ratio < 0.6:
            issues.append(
                f"Header (public API) documentation coverage is low: {header_doc_ratio:.1%}"
            )
        if func_impl_coverage < 0.5 and total_func_impl > 0:
            issues.append(
                f"Function implementation documentation coverage is low: {func_impl_coverage:.1%}"
            )
        if func_decl_coverage < 0.7 and total_func_decl > 0:
            issues.append(
                f"Function declaration (public API) documentation coverage is low: {func_decl_coverage:.1%}"
            )
        if type_coverage < 0.5 and total_types > 0:
            issues.append(
                f"Type (class/struct/enum) documentation coverage is low: {type_coverage:.1%}"
            )
        if macro_coverage < 0.5 and total_macros > 0:
            issues.append(
                f"Macro documentation coverage is low: {macro_coverage:.1%}"
            )

        low_comment_files = [
            f["file"]
            for f in per_file
            if f["comment_ratio"] < THR["comment_ratio_low"]
        ]
        if low_comment_files:
            issues.append(
                f"Low comment density in {len(low_comment_files)} file(s) "
                f"(< {int(THR['comment_ratio_low'] * 100)}%)"
            )

        doc_sparse_files = [
            f["file"] for f in per_file if f["doc_density"] < THR["doc_density_low"]
        ]
        if doc_sparse_files:
            issues.append(
                f"Very low Doxygen documentation density in {len(doc_sparse_files)} file(s)"
            )

        if missing_items:
            issues.append(
                f"{len(missing_items)} item(s) missing Doxygen or specific tags "
                "(@param/@return)"
            )

        if overall_score >= 80 and not issues:
            issues.append("Excellent documentation coverage and quality!")

        metrics = {
            "documentation_ratio": round(overall_doc_ratio, 3),
            "header_documentation_ratio": round(header_doc_ratio, 3),
            "function_impl_coverage_ratio": round(func_impl_coverage, 3),
            "function_decl_coverage_ratio": round(func_decl_coverage, 3),
            "type_coverage_ratio": round(type_coverage, 3),
            "macro_coverage_ratio": round(macro_coverage, 3),
            "param_full_coverage_ratio": round(param_full_ratio, 3),
            "return_doc_ratio": round(return_doc_ratio, 3),
            "average_doc_density": round(avg_doc_density, 3),
            "quality_doc_blocks_ratio": round(quality_doc_ratio, 3),
            "total_documentable_items": total_items,
            "documented_items": documented_items,
            "header_items": header_items,
            "header_documented_items": header_documented,
            "readme_length": len(readme_notes),
            "files": per_file,
            "missing_items": missing_items[:200],  # cap size
        }

        return {
            "score": round(overall_score, 1),
            "grade": grade,
            "metrics": metrics,
            "issues": issues,
        }

    @staticmethod
    def _score_to_grade(score: float) -> str:
        """Convert numeric score to letter grade."""
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"