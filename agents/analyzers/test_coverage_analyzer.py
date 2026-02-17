"""
C/C++ test coverage analysis (enhanced)
"""

import re
import os
from pathlib import Path
from typing import Dict, List, Any, Set, Tuple
from collections import Counter


class TestCoverageAnalyzer:
    """
    Analyzes C/C++ test coverage and testing practices:
    - Test file detection and framework identification
    - Build system integration analysis
    - Coverage artifact detection
    - Source-to-test mapping heuristics
    - Test quality assessment (assertion density, tests without assertions)
    """

    # C/C++ file extensions
    C_EXTS = {".c", ".cpp", ".cc", ".cxx", ".c++"}
    H_EXTS = {".h", ".hpp", ".hh", ".hxx", ".h++"}

    def __init__(self, codebase_path: str = None, project_root: str = None):
        """Initialize test coverage analyzer."""
        self.codebase_path = codebase_path or os.getcwd()
        self.project_root = project_root or os.getcwd()
        self._file_cache: List[Dict[str, Any]] = []

    def analyze(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze C/C++ test coverage and testing practices.

        Args:
            file_cache: List of processed C/C++ file entries

        Returns:
            Test coverage analysis results with score, grade, metrics and issues
        """
        self._file_cache = file_cache or []
        return self._calculate_test_coverage_score()

    def _calculate_test_coverage_score(self) -> Dict[str, Any]:
        """
        C/C++ test coverage heuristic scoring.

        This function analyzes a C/C++ codebase for evidence of unit tests, build
        integration of those tests, and coverage outputs. It is a static heuristic
        (no execution) intended to surface gaps and provide an actionable score.
        """
        if not getattr(self, "_file_cache", None):
            return {
                "score": 0.0,
                "grade": "F",
                "issues": ["No files cached"],
                "metrics": {},
            }

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

        all_files = self._file_cache

        # Filter C/C++ files for source/test analysis
        all_exts = {ext.lower() for ext in (self.C_EXTS | self.H_EXTS)}
        c_cpp_files: List[Dict[str, Any]] = []
        for f in self._file_cache:
            suffix = (f.get("suffix") or "").lower()
            if suffix in all_exts:
                c_cpp_files.append(f)

        print(
            f"DEBUG test_coverage: Found {len(c_cpp_files)} C/C++ files out of "
            f"{len(self._file_cache)} total"
        )
        print(
            f"DEBUG test_coverage: Extensions being checked: "
            f"{sorted(self.C_EXTS | self.H_EXTS)}"
        )

        # Score weights
        W = {
            "test_ratio": 0.35,
            "frameworks": 0.15,
            "build_integration": 0.15,
            "mapping": 0.15,
            "assertion_density": 0.10,
            "coverage_artifacts": 0.10,
        }

        def _is_source_file(entry: Dict[str, Any]) -> bool:
            suf = (entry.get("suffix") or "").lower()
            return suf in {".c", ".cpp", ".cc", ".cxx"}

        def _is_header_file(entry: Dict[str, Any]) -> bool:
            suf = (entry.get("suffix") or "").lower()
            return suf in {".h", ".hpp", ".hh", ".hxx"}

        def _likely_test_file(entry: Dict[str, Any]) -> bool:
            """
            Detect test files by common conventions:
            - Names: *_test.*, test_*, *.test.*, *_spec.*, spec_*
            - Paths: tests/, test/, unittest/, unit_tests/, gtests/, integration_tests/
            Consider both source and header files (header-only tests).
            """
            name = (entry.get("file_name") or "").lower()
            path = _rel_path(entry).lower()

            # Only C/C++ files
            if not (_is_source_file(entry) or _is_header_file(entry)):
                return False

            patterns_name = ["test_", "_test", ".test.", "_spec", "spec_"]
            patterns_path = [
                "/tests/",
                "/test/",
                "/unittest/",
                "/unit_tests/",
                "/gtests/",
                "/integration_tests/",
                "/itests/",
            ]

            if any(p in name for p in patterns_name):
                return True
            if any(p in path for p in patterns_path):
                return True

            return False

        # Framework regex patterns
        FW_PATTERNS = {
            "gtest": [
                r"#\s*include\s*<\s*gtest/gtest\.h\s*>",
                r"\bTEST(?:_F|_P)?\s*\(",
                r"\bEXPECT_[A-Z_]+\s*\(",
                r"\bASSERT_[A-Z_]+\s*\(",
            ],
            "catch2": [
                r'#\s*include\s*<\s*catch2/catch\.hpp\s*>|#\s*include\s*["<]catch\.hpp[">]',
                r"\bTEST_CASE\s*\(",
                r"\bSECTION\s*\(",
                r"\bREQUIRE(?:_THROWS|_FALSE|_NOTHROW)?\s*\(",
                r"\bCHECK(?:_FALSE)?\s*\(",
            ],
            "boost_test": [
                r"#\s*include\s*<\s*boost/test/unit_test\.hpp\s*>",
                r"\bBOOST_(?:AUTO|FIXTURE)_TEST_CASE\s*\(",
                r"\bBOOST_TEST_MODULE\b",
                r"\bBOOST_CHECK(?:_[A-Z_]+)?\s*\(",
            ],
            "cunit": [
                r"\bCU_(?:initialize_registry|add_suite|add_test|basic_run_tests)\b",
                r'#\s*include\s*["<]CUnit/CUnit\.h[">]',
            ],
            "unity": [
                r'#\s*include\s*["<]unity\.h[">]',
                r"\bUNITY_BEGIN\s*\(",
                r"\bRUN_TEST\s*\(",
                r"\bTEST_ASSERT(?:_[A-Z_]+)?\s*\(",
            ],
            "cpputest": [
                r'#\s*include\s*["<]CppUTest/TestHarness\.h[">]',
                r"\bTEST_GROUP\s*\(",
                r"\bTEST\s*\(",
                r"\bCHECK(?:_TRUE|_FALSE)?\s*\(",
            ],
        }

        ASSERTION_PATTERNS = [
            r"\bEXPECT_[A-Z_]+\s*\(",
            r"\bASSERT_[A-Z_]+\s*\(",
            r"\bREQUIRE(?:_[A-Z_]+)?\s*\(",
            r"\bCHECK(?:_[A-Z_]+)?\s*\(",
            r"\bBOOST_CHECK(?:_[A-Z_]+)?\s*\(",
            r"\bTEST_ASSERT(?:_[A-Z_]+)?\s*\(",
            r"\bCU_ASSERT(?:_[A-Z_]+)?\s*\(",
        ]

        TESTCASE_PATTERNS = [
            r"\bTEST(?:_F|_P)?\s*\(",
            r"\bTEST_CASE\s*\(",
            r"\bBOOST_(?:AUTO|FIXTURE)_TEST_CASE\s*\(",
            r"\bCU_add_test\b",
            r"\bRUN_TEST\s*\(",
            r"\bTEST\s*\(",  # CppUTest
        ]

        def _detect_frameworks(source: str) -> List[str]:
            found = []
            for fw, pats in FW_PATTERNS.items():
                if any(re.search(p, source) for p in pats):
                    found.append(fw)
            return found

        def _count_matches(source: str, patterns: List[str]) -> int:
            return sum(len(list(re.finditer(p, source))) for p in patterns)

        def _detect_build_integration(files: List[Dict[str, Any]]) -> Tuple[Dict[str, bool], bool]:
            """
            Detect build-system test integration across repo.
            Returns:
                (flags, any_build_files_present)
            """
            flags = {
                "cmake_enable_testing": False,
                "cmake_add_test": False,
                "bazel_cc_test": False,
                "makefile_test_target": False,
            }
            any_build_files = False

            for e in files:
                fname = (e.get("file_name") or "").lower()
                src = e.get("source") or ""

                # CMake
                if fname == "cmakelists.txt":
                    any_build_files = True
                    if re.search(r"\benable_testing\s*\(", src, re.IGNORECASE):
                        flags["cmake_enable_testing"] = True
                    if re.search(r"\badd_test\s*\(", src, re.IGNORECASE):
                        flags["cmake_add_test"] = True

                # Bazel
                if fname in {"build", "build.bazel"}:
                    any_build_files = True
                    if re.search(r"\bcc_test\s*\(", src):
                        flags["bazel_cc_test"] = True

                # Make
                if fname in {"makefile", "gnumakefile"}:
                    any_build_files = True
                    if re.search(
                        r"^\s*(test|check):", src, re.MULTILINE | re.IGNORECASE
                    ):
                        flags["makefile_test_target"] = True

            return flags, any_build_files

        def _detect_coverage_artifacts(files: List[Dict[str, Any]]) -> Dict[str, Any]:
            art = {
                "gcda_gcno_files": 0,
                "lcov_info_files": 0,
                "coverage_html": 0,
                "paths": [],
            }

            for e in files:
                fname = (e.get("file_name") or "").lower()
                path = _rel_path(e).lower()

                if fname.endswith(".gcda") or fname.endswith(".gcno"):
                    art["gcda_gcno_files"] += 1
                    art["paths"].append(_rel_path(e))

                if fname.endswith("lcov.info") or fname.endswith("coverage.info"):
                    art["lcov_info_files"] += 1
                    art["paths"].append(_rel_path(e))

                if "coverage" in path and fname.endswith(".html"):
                    art["coverage_html"] += 1
                    art["paths"].append(_rel_path(e))

            return art

        def _basename_without_ext(path: str) -> str:
            base = os.path.basename(path)
            dot = base.find(".")
            return base[:dot] if dot != -1 else base

        def _map_sources_to_tests(
            sources: List[Dict[str, Any]], tests: List[Dict[str, Any]]
        ) -> Tuple[int, List[str]]:
            """Heuristic mapping: count sources with a matching test file by base name."""
            tested = 0
            untested_paths: List[str] = []

            test_basenames = set()
            for t in tests:
                rp = _rel_path(t)
                b = _basename_without_ext(rp.lower())
                test_basenames.add(b)

            for s in sources:
                rp = _rel_path(s)
                b = _basename_without_ext(rp.lower())
                candidates = {
                    f"{b}_test",
                    f"test_{b}",
                    f"{b}.test",
                    f"{b}_spec",
                    f"spec_{b}",
                }
                if any(c in test_basenames for c in candidates):
                    tested += 1
                else:
                    untested_paths.append(rp)

            return tested, untested_paths

        # ----------------------------- Scan ------------------------------------
        source_files: List[Dict[str, Any]] = []
        test_files: List[Dict[str, Any]] = []
        frameworks_global: Set[str] = set()
        test_cases_per_file: Dict[str, int] = {}
        assertions_per_file: Dict[str, int] = {}
        tests_without_assertions: List[str] = []

        print(
            f"DEBUG test_coverage: Starting analysis of {len(c_cpp_files)} C/C++ files"
        )

        for entry in c_cpp_files:
            source = entry.get("source") or ""
            if not source.strip():
                print(
                    f"DEBUG test_coverage: Skipping empty file: "
                    f"{entry.get('file_relative_path', 'unknown')}"
                )
                continue

            rel = _rel_path(entry)

            if _is_source_file(entry) or _is_header_file(entry):
                if _likely_test_file(entry):
                    test_files.append(entry)
                    print(f"DEBUG test_coverage: Found test file: {rel}")

                    fws = _detect_frameworks(source)
                    for fw in fws:
                        frameworks_global.add(fw)

                    tc = _count_matches(source, TESTCASE_PATTERNS)
                    asrt = _count_matches(source, ASSERTION_PATTERNS)
                    test_cases_per_file[rel] = tc
                    assertions_per_file[rel] = asrt

                    if asrt == 0 and tc > 0:
                        tests_without_assertions.append(rel)

                    print(
                        f"DEBUG test_coverage: Test file {rel} - "
                        f"Frameworks: {fws}, Test cases: {tc}, Assertions: {asrt}"
                    )
                else:
                    if _is_source_file(entry):
                        source_files.append(entry)
                        print(f"DEBUG test_coverage: Found source file: {rel}")

        print(
            f"DEBUG test_coverage: Found {len(source_files)} source files, "
            f"{len(test_files)} test files"
        )
        print(
            f"DEBUG test_coverage: Frameworks detected: {sorted(frameworks_global)}"
        )

        # Build integration and coverage artifacts
        build_flags, any_build_files = _detect_build_integration(all_files)
        coverage_artifacts = _detect_coverage_artifacts(all_files)

        print(f"DEBUG test_coverage: Build integration: {build_flags}")
        print(f"DEBUG test_coverage: Coverage artifacts: {coverage_artifacts}")

        # Source-to-test mapping
        tested_sources_count, untested_sources = _map_sources_to_tests(
            source_files, test_files
        )
        print(
            f"DEBUG test_coverage: Source-to-test mapping: "
            f"{tested_sources_count}/{len(source_files)} sources have tests"
        )

        # ----------------------------- Metrics ----------------------------------
        total_sources = len(source_files)
        total_tests = len(test_files)
        test_ratio = (total_tests / total_sources) if total_sources > 0 else 0.0
        avg_test_cases = (
            sum(test_cases_per_file.values()) / max(1, total_tests)
            if total_tests > 0
            else 0.0
        )
        avg_assertions = (
            sum(assertions_per_file.values()) / max(1, total_tests)
            if total_tests > 0
            else 0.0
        )
        mapping_ratio = (
            tested_sources_count / max(1, total_sources) if total_sources > 0 else 0.0
        )
        frameworks_list = sorted(list(frameworks_global))

        # Additional depth metrics
        assertion_values = list(assertions_per_file.values())
        min_assertions = min(assertion_values) if assertion_values else 0
        max_assertions = max(assertion_values) if assertion_values else 0
        thin_test_files = [
            f for f, a in assertions_per_file.items() if a > 0 and a < 2
        ]  # tests with very few assertions

        # ----------------------------- Scoring ----------------------------------
        def bucket_score(r: float) -> int:
            if r >= 0.8:
                return 100
            if r >= 0.6:
                return 85
            if r >= 0.4:
                return 70
            if r >= 0.2:
                return 50
            if r > 0.0:
                return 30
            return 0

        s_test_ratio = bucket_score(test_ratio)

        # Framework score
        if frameworks_list:
            s_framework = 100
        elif total_tests > 0 and avg_assertions > 0:
            s_framework = 40  # ad-hoc tests without known framework
        else:
            s_framework = 0

        # Build integration score
        s_build = 0
        if (
            build_flags["cmake_add_test"]
            or build_flags["bazel_cc_test"]
            or build_flags["makefile_test_target"]
        ):
            s_build = 100
        elif build_flags["cmake_enable_testing"]:
            s_build = 50

        s_mapping = int(round(mapping_ratio * 100))

        # Assertion density score
        if avg_assertions >= 8:
            s_assert = 100
        elif avg_assertions >= 4:
            s_assert = 85
        elif avg_assertions >= 2:
            s_assert = 60
        elif avg_assertions >= 1:
            s_assert = 40
        elif total_tests > 0:
            s_assert = 20
        else:
            s_assert = 0

        # Coverage artifacts score
        ca = coverage_artifacts
        if ca["lcov_info_files"] > 0 or ca["coverage_html"] > 0:
            s_cov = 100
        elif ca["gcda_gcno_files"] > 5:
            s_cov = 80
        elif ca["gcda_gcno_files"] > 0:
            s_cov = 60
        else:
            s_cov = 0

        print(
            "DEBUG test_coverage: Scores - "
            f"Test ratio: {s_test_ratio}, Framework: {s_framework}, "
            f"Build: {s_build}, Mapping: {s_mapping}, "
            f"Assertions: {s_assert}, Coverage: {s_cov}"
        )

        score = (
            s_test_ratio * W["test_ratio"]
            + s_framework * W["frameworks"]
            + s_build * W["build_integration"]
            + s_mapping * W["mapping"]
            + s_assert * W["assertion_density"]
            + s_cov * W["coverage_artifacts"]
        )

        issues: List[str] = []

        # Hard floor if no tests at all
        if total_tests == 0:
            issues.append("No C/C++ test files detected by naming/path conventions")
            score = 0.0

        if test_ratio < 0.3 and total_sources > 0:
            issues.append(f"Low test file ratio: {test_ratio:.1%}")

        if not frameworks_list and total_tests > 0:
            issues.append(
                "No recognized C/C++ unit test framework detected "
                "(consider GoogleTest, Catch2, Boost.Test, CUnit, Unity, CppUTest)"
            )

        if total_tests > 0 and any_build_files:
            if not (
                build_flags["cmake_add_test"]
                or build_flags["bazel_cc_test"]
                or build_flags["makefile_test_target"]
            ):
                issues.append(
                    "Build integration missing: add CMake add_test(), Bazel cc_test(), "
                    "or Makefile test/check targets"
                )
        elif total_tests > 0 and not any_build_files:
            issues.append(
                "Tests present but no build-system files detected; ensure tests are integrated into CI"
            )

        if mapping_ratio < 0.5 and total_sources > 0:
            issues.append(
                f"Less than half of sources appear to have matching tests ({mapping_ratio:.1%})"
            )

        if avg_assertions < 2 and total_tests > 0:
            issues.append(
                f"Low assertion density: {avg_assertions:.2f} assertions per test file on average"
            )

        if (
            ca["gcda_gcno_files"] == 0
            and ca["lcov_info_files"] == 0
            and ca["coverage_html"] == 0
            and total_tests > 0
        ):
            issues.append(
                "No coverage artifacts detected (gcov/lcov). "
                "Consider enabling coverage reports in CI."
            )

        if tests_without_assertions:
            issues.append(
                f"{len(tests_without_assertions)} test file(s) contain test cases with zero assertions"
            )

        if thin_test_files:
            issues.append(
                f"{len(thin_test_files)} test file(s) have very few assertions (<2); "
                "consider adding stronger checks"
            )

        # Positive feedback
        if score >= 80 and total_tests > 0:
            issues.append("Excellent test coverage setup!")
        elif score >= 60 and total_tests > 0:
            issues.append(
                "Good test coverage foundation - consider expanding test cases "
                "and coverage depth"
            )

        score = max(0.0, min(100.0, score))
        grade = self._score_to_grade(score)

        print(f"DEBUG test_coverage: Final score: {score:.1f}")

        top_tests = sorted(
            [
                {
                    "file": f,
                    "test_cases": test_cases_per_file.get(f, 0),
                    "assertions": assertions_per_file.get(f, 0),
                }
                for f in test_cases_per_file.keys()
            ],
            key=lambda x: (x["test_cases"], x["assertions"]),
            reverse=True,
        )[:20]

        metrics = {
            "total_source_files": total_sources,
            "total_test_files": total_tests,
            "test_ratio": round(test_ratio, 3),
            "frameworks_detected": frameworks_list,
            "build_integration": build_flags,
            "build_files_present": any_build_files,
            "coverage_artifacts": coverage_artifacts,
            "avg_test_cases_per_test_file": round(avg_test_cases, 2),
            "avg_assertions_per_test_file": round(avg_assertions, 2),
            "min_assertions_per_test_file": min_assertions,
            "max_assertions_per_test_file": max_assertions,
            "mapping_ratio": round(mapping_ratio, 3),
            "tested_sources_count": tested_sources_count,
            "untested_sources": untested_sources[:200],
            "tests_without_assertions": tests_without_assertions[:200],
            "thin_test_files": thin_test_files[:200],
            "top_test_files": top_tests,
        }

        return {
            "score": round(score, 1),
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