"""
C/C++ file discovery, processing, and language detection (enhanced)
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Set, Optional, Any
from collections import Counter
import fnmatch


class FileProcessor:
    """
    Handles C/C++ file discovery, processing, and basic analysis.

    Supports:
    - C source files (.c)
    - C++ source files (.cpp, .cc, .cxx, .c++)
    - C header files (.h)
    - C++ header files (.hpp, .hh, .hxx, .h++)
    """

    # C/C++ file extensions
    C_SOURCE_EXTS = {".c"}
    CPP_SOURCE_EXTS = {".cpp", ".cc", ".cxx", ".c++"}
    C_HEADER_EXTS = {".h"}
    CPP_HEADER_EXTS = {".hpp", ".hh", ".hxx", ".h++"}

    ALL_C_CPP_EXTS = C_SOURCE_EXTS | CPP_SOURCE_EXTS | C_HEADER_EXTS | CPP_HEADER_EXTS

    # Default exclusions for C/C++ projects
    DEFAULT_EXCLUDE_DIRS = {
        ".git",
        ".svn",
        ".hg",
        ".bzr",
        "build",
        "dist",
        "target",
        "out",
        "bin",
        "obj",
        "Debug",
        "Release",
        "x64",
        "Win32",
        "third_party",
        "external",
        "vendor",
        ".idea",
        ".vscode",
        ".vs",
        "__pycache__",
        ".pytest_cache",
        "CMakeFiles",
        ".cmake",
    }

    DEFAULT_EXCLUDE_GLOBS = [
        "*.o",
        "*.obj",
        "*.so",
        "*.dll",
        "*.dylib",
        "*.a",
        "*.lib",
        "*.exe",
        "*.bin",
        "*.log",
        "*.tmp",
        "*.temp",
        "*.zip",
        "*.tar",
        "*.gz",
        "*.bz2",
        "*.jpg",
        "*.jpeg",
        "*.png",
        "*.gif",
        "*.svg",
        "*.pdf",
        "*.doc",
        "*.docx",
        "moc_*.cpp",
        "ui_*.h",
        "qrc_*.cpp",  # Qt generated files
        "*_autogen/*",  # CMake autogen
    ]

    def __init__(
        self,
        codebase_path: str,
        max_files: int = 10000,
        exclude_dirs: Optional[List[str]] = None,
        exclude_globs: Optional[List[str]] = None,
    ):
        """
        Initialize C/C++ file processor.

        Args:
            codebase_path: Path to C/C++ codebase root
            max_files: Maximum files to process
            exclude_dirs: Additional directories to exclude (names, not paths)
            exclude_globs: Additional glob patterns to exclude (relative paths)
        """
        self.codebase_path = Path(codebase_path).resolve()
        self.max_files = max_files

        # Exclusions: use sets/lists for efficient checks
        self.exclude_dirs = self.DEFAULT_EXCLUDE_DIRS | set(exclude_dirs or [])
        self.exclude_globs = self.DEFAULT_EXCLUDE_GLOBS + (exclude_globs or [])

        # Cache
        self._file_cache: List[Dict[str, Any]] = []
        self._language_stats: Dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _is_excluded(self, path: Path) -> bool:
        """Check if path should be excluded based on directory names and glob patterns.

        Only inspects path components *relative* to the codebase root so that
        the location of the codebase itself (e.g. inside an ``out/`` directory)
        does not accidentally trigger an exclusion.
        """
        # Relative path for glob matching; guard against paths outside codebase
        try:
            rel_path = path.relative_to(self.codebase_path)
        except ValueError:
            # Path not under codebase root (e.g., symlink); treat as excluded
            return True

        # Directory exclusions — only check parts *below* codebase root
        for part in rel_path.parts:
            if part in self.exclude_dirs:
                return True

        rel_path_str = rel_path.as_posix().lower()
        for pattern in self.exclude_globs:
            if fnmatch.fnmatch(rel_path_str, pattern.lower()):
                return True

        return False

    def _detect_language(self, file_path: Path, content: str = "") -> str:
        """
        Detect C/C++ language variant from file extension and (optionally) content.

        For headers, content is used to distinguish C vs C++ heuristically.
        """
        suffix = file_path.suffix.lower()

        if suffix in self.C_SOURCE_EXTS:
            return "c"
        if suffix in self.CPP_SOURCE_EXTS:
            return "cpp"
        if suffix in self.C_HEADER_EXTS:
            if content and self._is_cpp_header_content(content):
                return "cpp_header"
            return "c_header"
        if suffix in self.CPP_HEADER_EXTS:
            return "cpp_header"

        return "unknown"

    def _is_cpp_header_content(self, content: str) -> bool:
        """Analyze header content to determine if it's C++ rather than C."""
        cpp_indicators = [
            r"\bclass\s+\w+",
            r"\bnamespace\s+\w+",
            r"\btemplate\s*<",
            r"#\s*include\s*<\s*iostream\s*>",
            r"#\s*include\s*<\s*vector\s*>",
            r"#\s*include\s*<\s*string\s*>",
            r"\bstd::\w+",
            r"\bpublic\s*:",
            r"\bprivate\s*:",
            r"\bprotected\s*:",
            r"\bvirtual\s+",
            r"\binline\s+",
            r"\boperator\s*[+\-*/=<>!]+",
            r"\bnew\s+\w+",
            r"\bdelete\s+",
        ]

        for pattern in cpp_indicators:
            if re.search(pattern, content):
                return True

        return False

    def _extract_includes(self, content: str) -> List[Dict[str, Any]]:
        """Extract #include statements from C/C++ code."""
        includes: List[Dict[str, Any]] = []

        for line_num, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()

            # System includes: #include <header>
            system_match = re.match(r"^\s*#\s*include\s*<([^>]+)>", stripped)
            if system_match:
                includes.append(
                    {
                        "file": system_match.group(1),
                        "line": line_num,
                        "type": "system",
                        "raw_line": line.rstrip("\n"),
                    }
                )
                continue

            # Local includes: #include "header"
            local_match = re.match(r'^\s*#\s*include\s*"([^"]+)"', stripped)
            if local_match:
                includes.append(
                    {
                        "file": local_match.group(1),
                        "line": line_num,
                        "type": "local",
                        "raw_line": line.rstrip("\n"),
                    }
                )

        return includes

    def _calculate_basic_metrics(self, content: str, language: str) -> Dict[str, Any]:
        """Calculate basic C/C++ code metrics."""
        lines = content.splitlines()

        metrics: Dict[str, Any] = {
            "total_lines": len(lines),
            "non_empty_lines": 0,
            "comment_lines": 0,
            "code_lines": 0,
            "preprocessor_lines": 0,
        }

        in_multiline_comment = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                # Blank line: counted only in total_lines
                continue

            metrics["non_empty_lines"] += 1

            # Multi-line comment continuation
            if in_multiline_comment:
                metrics["comment_lines"] += 1
                if "*/" in stripped:
                    in_multiline_comment = False
                continue

            # Multi-line comment start
            if stripped.startswith("/*"):
                metrics["comment_lines"] += 1
                if "*/" not in stripped:
                    in_multiline_comment = True
                continue

            # Single line comments
            if stripped.startswith("//"):
                metrics["comment_lines"] += 1
                continue

            # Preprocessor directives
            if stripped.startswith("#"):
                metrics["preprocessor_lines"] += 1
                continue

            # Everything else is code
            metrics["code_lines"] += 1

        total_non_empty = max(1, metrics["non_empty_lines"])
        metrics["comment_ratio"] = metrics["comment_lines"] / total_non_empty
        metrics["code_ratio"] = metrics["code_lines"] / total_non_empty
        metrics["preprocessor_ratio"] = metrics["preprocessor_lines"] / total_non_empty

        return metrics

    def _extract_functions(self, content: str, language: str) -> List[Dict[str, Any]]:
        """
        Extract function definitions from C/C++ code.

        This is a simplified heuristic regex-based extractor, not a full parser.
        """
        functions: List[Dict[str, Any]] = []

        # Simplified function signature pattern
        function_pattern = r"""
            (?:^|\n)                          # Start of line
            \s*                               # Optional whitespace
            (?:(?:static|extern|inline|virtual|explicit)\s+)*  # Optional qualifiers
            (?:const\s+)?                     # Optional const
            (?:\w+(?:\s*\*\s*|\s*&\s*|\s+))*  # Return type (very simplified)
            (\w+)                             # Function name (capture group 1)
            \s*\(                             # Opening parenthesis
            ([^)]*)                           # Parameters (capture group 2)
            \)\s*                             # Closing parenthesis
            (?:const\s*)?                     # Optional const (methods)
            (?:override\s*)?                  # Optional override
            (?:noexcept\s*)?                  # Optional noexcept
            \s*\{                             # Opening brace
        """

        for match in re.finditer(function_pattern, content, re.VERBOSE | re.MULTILINE):
            func_name = match.group(1)
            params = match.group(2).strip()

            # Line number of function definition
            line_num = content[: match.start()].count("\n") + 1

            # Parameter counting (heuristic)
            param_count = 0
            if params and params != "void":
                param_list = [p for p in params.split(",") if p.strip()]
                param_count = len(param_list)

            functions.append(
                {
                    "name": func_name,
                    "line": line_num,
                    "parameters": params,
                    "parameter_count": param_count,
                }
            )

        return functions

    # ------------------------------------------------------------------ #
    # Main processing
    # ------------------------------------------------------------------ #

    def process_files(self) -> List[Dict[str, Any]]:
        """Process all C/C++ files in the codebase and populate file cache."""
        if not self.codebase_path.exists():
            raise FileNotFoundError(
                f"Codebase path does not exist: {self.codebase_path}"
            )

        # Reset caches for repeatability
        self._file_cache = []
        self._language_stats = {}

        files: List[Dict[str, Any]] = []
        processed_count = 0

        print(f"Processing C/C++ files in: {self.codebase_path}")
        print(f"Supported extensions: {sorted(self.ALL_C_CPP_EXTS)}")

        for root, dirs, filenames in os.walk(self.codebase_path):
            # Filter out excluded directories (by name)
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]

            for filename in filenames:
                if processed_count >= self.max_files:
                    break

                file_path = Path(root) / filename

                # Check exclusions (dirs, globs, and out-of-tree paths)
                if self._is_excluded(file_path):
                    continue

                suffix = file_path.suffix.lower()
                if suffix not in self.ALL_C_CPP_EXTS:
                    continue

                # Read file content
                try:
                    with open(
                        file_path, "r", encoding="utf-8", errors="ignore"
                    ) as f:
                        content = f.read()
                except Exception as e:
                    print(f"Warning: Could not read {file_path}: {e}")
                    continue

                # Detect language
                language = self._detect_language(file_path, content)
                if language == "unknown":
                    continue

                # Relative path from codebase root
                try:
                    rel_path = file_path.relative_to(self.codebase_path)
                except ValueError:
                    # Should not normally happen, but skip if outside root
                    continue

                # Extract includes
                includes = self._extract_includes(content)

                # Calculate basic metrics
                metrics = self._calculate_basic_metrics(content, language)

                # Extract functions (source files only)
                functions: List[Dict[str, Any]] = []
                if language in ["c", "cpp"]:
                    functions = self._extract_functions(content, language)

                # Build file entry
                file_entry: Dict[str, Any] = {
                    "path_obj": file_path,
                    "file_name": file_path.name,
                    "file_relative_path": rel_path.as_posix(),
                    "suffix": suffix,
                    "language": language,
                    "source": content,
                    "size_bytes": len(content.encode("utf-8")),
                    "includes": includes,
                    "functions": functions,
                    "metrics": metrics,
                    "module_key": self._generate_module_key(rel_path),
                }

                files.append(file_entry)
                processed_count += 1

                # Update language stats
                self._language_stats[language] = (
                    self._language_stats.get(language, 0) + 1
                )

            if processed_count >= self.max_files:
                break

        self._file_cache = files

        print(f"Processed {len(files)} C/C++ files")
        print(f"Language distribution: {dict(self._language_stats)}")

        return files

    def _generate_module_key(self, rel_path: Path) -> str:
        """Generate a module key for dependency tracking."""
        path_str = rel_path.as_posix()
        if "." in path_str:
            path_str = path_str.rsplit(".", 1)[0]
        module_key = path_str.replace("/", ".")
        return module_key

    # ------------------------------------------------------------------ #
    # Structure analysis
    # ------------------------------------------------------------------ #

    def analyze_structure(self, file_cache: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Analyze C/C++ codebase structure and provide summary."""
        cache = file_cache or self._file_cache

        if not cache:
            return {"error": "No C/C++ files processed"}

        # If a custom cache is provided, recompute language stats from it
        lang_counts = Counter(f.get("language", "unknown") for f in cache)
        file_stats = {
            "total_files": len(cache),
            "languages": dict(lang_counts),
            "file_types": Counter(f.get("suffix", "") for f in cache),
            "total_size_bytes": sum(f.get("size_bytes", 0) for f in cache),
        }
        file_stats["avg_file_size"] = (
            file_stats["total_size_bytes"] / file_stats["total_files"]
            if file_stats["total_files"] > 0
            else 0
        )

        # Source vs header
        source_files = [f for f in cache if f.get("language") in ["c", "cpp"]]
        header_files = [
            f for f in cache if f.get("language") in ["c_header", "cpp_header"]
        ]

        file_stats.update(
            {
                "source_files": len(source_files),
                "header_files": len(header_files),
                "source_to_header_ratio": len(source_files)
                / max(1, len(header_files)),
            }
        )

        # Code metrics aggregation
        total_lines = sum(f["metrics"]["total_lines"] for f in cache)
        total_code_lines = sum(f["metrics"]["code_lines"] for f in cache)
        total_comment_lines = sum(f["metrics"]["comment_lines"] for f in cache)
        total_preprocessor_lines = sum(
            f["metrics"]["preprocessor_lines"] for f in cache
        )

        code_metrics = {
            "total_lines": total_lines,
            "total_code_lines": total_code_lines,
            "total_comment_lines": total_comment_lines,
            "total_preprocessor_lines": total_preprocessor_lines,
            "overall_comment_ratio": total_comment_lines / max(1, total_lines),
            "overall_code_ratio": total_code_lines / max(1, total_lines),
            "overall_preprocessor_ratio": total_preprocessor_lines
            / max(1, total_lines),
        }

        # Include analysis
        all_includes: List[Dict[str, Any]] = []
        for f in cache:
            all_includes.extend(f.get("includes", []))

        system_includes = [inc for inc in all_includes if inc.get("type") == "system"]
        local_includes = [inc for inc in all_includes if inc.get("type") == "local"]

        include_stats = {
            "total_includes": len(all_includes),
            "system_includes": len(system_includes),
            "local_includes": len(local_includes),
            "system_to_local_ratio": len(system_includes)
            / max(1, len(local_includes)),
        }

        system_include_names = [inc.get("file", "") for inc in system_includes]
        local_include_names = [inc.get("file", "") for inc in local_includes]

        common_includes = {
            "system": Counter(system_include_names).most_common(10),
            "local": Counter(local_include_names).most_common(10),
        }

        # Function analysis (source files only)
        all_functions: List[Dict[str, Any]] = []
        for f in source_files:
            all_functions.extend(f.get("functions", []))

        function_stats: Dict[str, Any] = {
            "total_functions": len(all_functions),
            "avg_functions_per_file": len(all_functions)
            / max(1, len(source_files)),
        }

        if all_functions:
            param_counts = [func.get("parameter_count", 0) for func in all_functions]
            function_stats.update(
                {
                    "avg_parameters_per_function": sum(param_counts)
                    / max(1, len(param_counts)),
                    "max_parameters": max(param_counts),
                    "functions_with_no_params": sum(
                        1 for count in param_counts if count == 0
                    ),
                }
            )

        # README analysis
        readme_content = self._extract_readme_content()

        # Build system detection
        build_systems = self._detect_build_systems()

        return {
            "file_stats": file_stats,
            "code_metrics": code_metrics,
            "include_stats": include_stats,
            "common_includes": common_includes,
            "function_stats": function_stats,
            "build_systems": build_systems,
            "readme_notes": readme_content,
        }

    def _extract_readme_content(self) -> str:
        """Extract README content if available (first ~1000 characters)."""
        readme_candidates = [
            "README.md",
            "README.rst",
            "README.txt",
            "README",
            "readme.md",
            "readme.rst",
            "readme.txt",
            "readme",
        ]

        for candidate in readme_candidates:
            readme_path = self.codebase_path / candidate
            if readme_path.exists():
                try:
                    content = readme_path.read_text(
                        encoding="utf-8", errors="ignore"
                    )
                    if len(content) > 1000:
                        content = content[:1000] + "..."
                    return content
                except Exception:
                    continue

        return ""

    def _detect_build_systems(self) -> List[str]:
        """Detect build systems used in the codebase (root-level scan)."""
        build_systems: List[str] = []

        build_files = {
            "CMakeLists.txt": "CMake",
            "Makefile": "Make",
            "GNUmakefile": "GNU Make",
            "makefile": "Make",
            "configure.ac": "Autotools",
            "configure.in": "Autotools",
            "Makefile.am": "Automake",
            "BUILD": "Bazel",
            "BUILD.bazel": "Bazel",
            "WORKSPACE": "Bazel",
            "meson.build": "Meson",
            "SConstruct": "SCons",
            "wscript": "Waf",
            "project.pro": "qmake",
            "CMakeCache.txt": "CMake (built)",
        }

        for build_file, system in build_files.items():
            if (self.codebase_path / build_file).exists():
                if system not in build_systems:
                    build_systems.append(system)

        # Visual Studio
        vs_patterns = ["*.vcxproj", "*.vcproj", "*.sln"]
        for pattern in vs_patterns:
            if list(self.codebase_path.glob(pattern)):
                if "Visual Studio" not in build_systems:
                    build_systems.append("Visual Studio")
                break

        return build_systems

    # ------------------------------------------------------------------ #
    # Accessors
    # ------------------------------------------------------------------ #

    def get_file_cache(self) -> List[Dict[str, Any]]:
        """Get processed file cache."""
        return self._file_cache

    def get_language_stats(self) -> Dict[str, int]:
        """Get language distribution statistics from last processing run."""
        return dict(self._language_stats)

    def get_files_by_language(self, language: str) -> List[Dict[str, Any]]:
        """Get all files for a specific language."""
        return [f for f in self._file_cache if f.get("language") == language]

    def get_source_files(self) -> List[Dict[str, Any]]:
        """Get all C/C++ source files."""
        return [f for f in self._file_cache if f.get("language") in ["c", "cpp"]]

    def get_header_files(self) -> List[Dict[str, Any]]:
        """Get all C/C++ header files."""
        return [
            f
            for f in self._file_cache
            if f.get("language") in ["c_header", "cpp_header"]
        ]