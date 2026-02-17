"""
C/C++ security vulnerability analysis (enhanced)
"""

import os
import re
from typing import Dict, List, Any
from collections import Counter
import math


class SecurityAnalyzer:
    """
    Analyzes C/C++ code for security vulnerabilities focusing on:
    - ScanBan-aligned banned API detection
    - Buffer overflow and unsafe string/memory usage
    - Command execution / injection risks
    - Format string vulnerabilities
    - Cryptographic weaknesses and TLS misconfiguration
    - Insecure PRNG and input conversion
    - Insecure file permissions and temp files
    """

    # C/C++ file extensions
    C_EXTS = {".c", ".cpp", ".cc", ".cxx", ".c++"}
    H_EXTS = {".h", ".hpp", ".hh", ".hxx", ".h++"}

    def __init__(self, codebase_path: str = None, project_root: str = None):
        """Initialize security analyzer with vulnerability patterns."""
        self.codebase_path = codebase_path or os.getcwd()
        self.project_root = project_root or os.getcwd()
        self._file_cache: List[Dict[str, Any]] = []

    def analyze(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze C/C++ code for security vulnerabilities.

        Args:
            file_cache: List of processed C/C++ file entries

        Returns:
            Security analysis results with score, grade, metrics, issues
        """
        self._file_cache = file_cache or []
        return self._calculate_security_score()

    def _calculate_security_score(self) -> Dict[str, Any]:
        """
        Security analysis for C/C++ codebases using ScanBan-aligned rules and common vulnerability patterns.

        Overview:
        - Scans C/C++ files for banned/unsafe APIs and high-impact risky constructs.
        - Produces severity-weighted scoring and detailed per-file violations.

        Limitations:
        - Heuristic, regex-based static scan; no full dataflow/taint analysis.
        - Use as triage/early warning and complement with Klocwork/Clang-Tidy.
        """
        if not self._file_cache:
            return {
                "score": 0.0,
                "grade": "F",
                "issues": ["No files cached"],
                "metrics": {
                    "files_analyzed": 0,
                    "total_violations": 0,
                    "risk_points": 0,
                    "critical_rule_present": False,
                    "top_violation_types": [],
                    "violations_by_file": {},
                    "severity_breakdown": {},
                    "rule_counts": {},
                    "files_with_violations": 0,
                    "clean_files": 0,
                    "files_with_critical": 0,
                    "files_with_high": 0,
                },
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

        # Filter C/C++ files
        all_exts = {ext.lower() for ext in (self.C_EXTS | self.H_EXTS)}
        c_cpp_files: List[Dict[str, Any]] = []
        for f in self._file_cache:
            suffix = (f.get("suffix") or "").lower()
            if suffix in all_exts:
                c_cpp_files.append(f)

        print(
            f"DEBUG security: Found {len(c_cpp_files)} C/C++ files out of "
            f"{len(self._file_cache)} total"
        )
        print(
            f"DEBUG security: Extensions being checked: {sorted(self.C_EXTS | self.H_EXTS)}"
        )

        if not c_cpp_files:
            return {
                "score": 100.0,  # No files => no issues detected by this analyzer
                "grade": "A",
                "metrics": {
                    "files_analyzed": 0,
                    "total_violations": 0,
                    "risk_points": 0,
                    "critical_rule_present": False,
                    "top_violation_types": [],
                    "violations_by_file": {},
                    "severity_breakdown": {},
                    "rule_counts": {},
                    "files_with_violations": 0,
                    "clean_files": 0,
                    "files_with_critical": 0,
                    "files_with_high": 0,
                },
                "issues": [
                    "No C/C++ files found for security analysis. "
                    f"Extensions checked: {sorted(self.C_EXTS | self.H_EXTS)}"
                ],
            }

        # Severity weights for scoring (per occurrence)
        severity_weight = {"critical": 10, "high": 6, "medium": 3, "low": 1}

        # Rule catalog
        # Each rule: id, sev, category, desc, pat, fix, optional flags
        rules = [
            # QCT ScanBan banned string APIs (BA00x) - buffer/overflow risk
            {
                "id": "BA001",
                "sev": "high",
                "category": "banned_api",
                "desc": "strcpy banned; use strlcpy()",
                "pat": r"\bstrcpy\s*\(",
                "fix": "Replace with strlcpy(dest, src, sizeof(dest))",
            },
            {
                "id": "BA002",
                "sev": "high",
                "category": "banned_api",
                "desc": "strcat banned; use strlcat()",
                "pat": r"\bstrcat\s*\(",
                "fix": "Replace with strlcat(dest, src, sizeof(dest))",
            },
            {
                "id": "BA003",
                "sev": "high",
                "category": "banned_api",
                "desc": "strncpy banned; use strlcpy()",
                "pat": r"\bstrncpy\s*\(",
                "fix": "Replace with strlcpy(dest, src, sizeof(dest))",
            },
            {
                "id": "BA004",
                "sev": "high",
                "category": "banned_api",
                "desc": "strncat banned; use strlcat()",
                "pat": r"\bstrncat\s*\(",
                "fix": "Replace with strlcat(dest, src, sizeof(dest))",
            },
            {
                "id": "BA005",
                "sev": "high",
                "category": "banned_api",
                "desc": "wstrcpy banned; use wstrlcpy()",
                "pat": r"\bwstrcpy\s*\(",
                "fix": "Replace with wstrlcpy",
            },
            {
                "id": "BA006",
                "sev": "high",
                "category": "banned_api",
                "desc": "wstrcat banned; use wstrlcat()",
                "pat": r"\bwstrcat\s*\(",
                "fix": "Replace with wstrlcat",
            },
            {
                "id": "BA007",
                "sev": "high",
                "category": "banned_api",
                "desc": "wstrncpy banned; use wstrlcpy()",
                "pat": r"\bwstrncpy\s*\(",
                "fix": "Replace with wstrlcpy",
            },
            {
                "id": "BA008",
                "sev": "high",
                "category": "banned_api",
                "desc": "wstrncat banned; use wstrlcat()",
                "pat": r"\bwstrncat\s*\(",
                "fix": "Replace with wstrlcat",
            },
            {
                "id": "BA009",
                "sev": "high",
                "category": "banned_api",
                "desc": "sprintf banned; use snprintf()",
                "pat": r"\bsprintf\s*\(",
                "fix": "Replace with snprintf(dest, sizeof(dest), ...)",
            },
            {
                "id": "BA010",
                "sev": "high",
                "category": "banned_api",
                "desc": "vsprintf banned; use vsnprintf()",
                "pat": r"\bvsprintf\s*\(",
                "fix": "Replace with vsnprintf(dest, sizeof(dest), fmt, args)",
            },
            {
                "id": "BA011",
                "sev": "high",
                "category": "banned_api",
                "desc": "wsprintf banned; use wsnprintf()",
                "pat": r"\bwsprintf\s*\(",
                "fix": "Replace with wsnprintf",
            },
            {
                "id": "BA012",
                "sev": "critical",
                "category": "banned_api",
                "desc": "gets banned; use fgets()",
                "pat": r"\bgets\s*\(",
                "fix": "Replace with fgets(buf, sizeof(buf), stdin)",
            },
            {
                "id": "BA013",
                "sev": "medium",
                "category": "banned_api",
                "desc": "strtok banned; use strtok_r()",
                "pat": r"\bstrtok\s*\(",
                "fix": "Replace with strtok_r(buf, delim, &saveptr)",
            },

            # Command execution (injection risk)
            {
                "id": "CMD001",
                "sev": "critical",
                "category": "command_injection",
                "desc": "system() used; OS command injection risk",
                "pat": r"\bsystem\s*\(",
                "fix": "Avoid shell; use execve/posix_spawn with fixed argv or remove",
            },
            {
                "id": "CMD002",
                "sev": "critical",
                "category": "command_injection",
                "desc": "popen() used; OS command injection risk",
                "pat": r"\bpopen\s*\(",
                "fix": "Avoid shell; use safe libraries/APIs",
            },
            {
                "id": "CMD003",
                "sev": "critical",
                "category": "command_injection",
                "desc": "exec* family used; OS command execution",
                "pat": r"\bexec[vpl]e?\w*\s*\(",
                "fix": "Ensure fixed argv/env and no user-controlled command",
            },

            # Insecure temporary files
            {
                "id": "TMP001",
                "sev": "high",
                "category": "insecure_temp",
                "desc": "mktemp used; insecure temporary file",
                "pat": r"\bmktemp\s*\(",
                "fix": "Use mkstemp() or secure temp APIs",
            },
            {
                "id": "TMP002",
                "sev": "high",
                "category": "insecure_temp",
                "desc": "tmpnam used; insecure temporary file",
                "pat": r"\btmpnam\s*\(",
                "fix": "Use mkstemp() or secure temp APIs",
            },

            # Insecure PRNG
            {
                "id": "RNG001",
                "sev": "medium",
                "category": "prng",
                "desc": "rand() used; not cryptographically secure",
                "pat": r"\brand\s*\(",
                "fix": "Use a CSPRNG (e.g., getrandom, /dev/urandom, crypto APIs)",
            },
            {
                "id": "RNG002",
                "sev": "low",
                "category": "prng",
                "desc": "srand()/srandom() used; predictable seeding",
                "pat": r"\bs(rand|random)\s*\(",
                "fix": "Avoid PRNG for security; use CSPRNG",
            },

            # Format-string vulnerabilities: variable as format string
            {
                "id": "FS001",
                "sev": "high",
                "category": "format_string",
                "desc": "printf with variable format",
                "pat": r"\bprintf\s*\(\s*[A-Za-z_]\w*\s*(?:,|\))",
                "fix": "Use fixed format string and pass variable as argument",
            },
            {
                "id": "FS002",
                "sev": "high",
                "category": "format_string",
                "desc": "fprintf with variable format",
                "pat": r"\bfprintf\s*\(\s*[^,]+,\s*[A-Za-z_]\w*\s*(?:,|\))",
                "fix": "Use fixed format string and pass variable as argument",
            },
            {
                "id": "FS003",
                "sev": "high",
                "category": "format_string",
                "desc": "snprintf with variable format",
                "pat": r"\bsn?printf\s*\(\s*[^,]+,\s*[^,]+,\s*[A-Za-z_]\w*\s*(?:,|\))",
                "fix": "Use fixed format string and pass variable as argument",
            },
            {
                "id": "FS004",
                "sev": "high",
                "category": "format_string",
                "desc": "syslog with variable format",
                "pat": r"\bsyslog\s*\(\s*[^,]+,\s*[A-Za-z_]\w*\s*(?:,|\))",
                "fix": "Use fixed format string and pass variable as argument",
            },

            # scanf/sscanf with %s without width
            {
                "id": "IO001",
                "sev": "high",
                "category": "input_validation",
                "desc": "scanf %s without width",
                "pat": r'\bscanf\s*\(\s*"(?:[^"%]|%%)*%s(?:[^"%]|%%)*"\s*,',
                "fix": "Specify width: %Ns; or use fgets + strtol",
            },
            {
                "id": "IO002",
                "sev": "high",
                "category": "input_validation",
                "desc": "sscanf %s without width",
                "pat": r'\bsscanf\s*\(\s*"(?:[^"%]|%%)*%s(?:[^"%]|%%)*"\s*,',
                "fix": "Specify width: %Ns; or tokenize safely",
            },

            # Hard-coded secrets (heuristic)
            {
                "id": "SEC001",
                "sev": "high",
                "category": "hardcoded_secret",
                "desc": "Hardcoded password",
                "pat": r'\b(?:char|const\s+char)\s*[*\s]*password\s*=\s*"(?:[^"\\]|\\.)+"',
                "fix": "Remove hardcoded credentials; use secure storage",
            },
            {
                "id": "SEC002",
                "sev": "high",
                "category": "hardcoded_secret",
                "desc": "Hardcoded API key",
                "pat": r'\b(?:char|const\s+char)\s*[*\s]*api_?key\s*=\s*"(?:[^"\\]|\\.)+"',
                "fix": "Remove hardcoded keys; use secure provisioning",
            },
            {
                "id": "SEC003",
                "sev": "high",
                "category": "hardcoded_secret",
                "desc": "Hardcoded secret",
                "pat": r'\b(?:char|const\s+char)\s*[*\s]*secret\s*=\s*"(?:[^"\\]|\\.)+"',
                "fix": "Remove secrets from source; use vault",
            },
            {
                "id": "SEC004",
                "sev": "high",
                "category": "hardcoded_secret",
                "desc": "Hardcoded token",
                "pat": r'\b(?:char|const\s+char)\s*[*\s]*token\s*=\s*"(?:[^"\\]|\\.)+"',
                "fix": "Remove tokens; use secure exchange",
            },

            # Weak crypto (MD5/SHA-1)
            {
                "id": "CRY001",
                "sev": "high",
                "category": "crypto",
                "desc": "MD5 used; cryptographically weak",
                "pat": r"\b(EVP_md5|MD5_Init|MD5Update|MD5_Final)\b",
                "fix": "Use SHA-256 or better",
            },
            {
                "id": "CRY002",
                "sev": "high",
                "category": "crypto",
                "desc": "SHA-1 used; cryptographically weak",
                "pat": r"\b(EVP_sha1|SHA1_Init|SHA1Update|SHA1Final)\b",
                "fix": "Use SHA-256 or better",
            },

            # TLS/SSL verification disabled
            {
                "id": "TLS001",
                "sev": "critical",
                "category": "tls",
                "desc": "OpenSSL cert verification disabled",
                "pat": r"\bSSL_CTX_set_verify\s*\([^,]+,\s*SSL_VERIFY_NONE\b",
                "fix": "Require peer verification",
            },
            {
                "id": "TLS002",
                "sev": "critical",
                "category": "tls",
                "desc": "OpenSSL cert verification disabled",
                "pat": r"\bSSL_set_verify\s*\([^,]+,\s*SSL_VERIFY_NONE\b",
                "fix": "Require peer verification",
            },
            {
                "id": "TLS003",
                "sev": "critical",
                "category": "tls",
                "desc": "libcurl peer verification disabled",
                "pat": r"\bcurl_easy_setopt\s*\([^,]+,\s*CURLOPT_SSL_VERIFYPEER\s*,\s*0\s*\)",
                "fix": "Set CURLOPT_SSL_VERIFYPEER to 1",
            },
            {
                "id": "TLS004",
                "sev": "critical",
                "category": "tls",
                "desc": "libcurl host verification disabled",
                "pat": r"\bcurl_easy_setopt\s*\([^,]+,\s*CURLOPT_SSL_VERIFYHOST\s*,\s*0\s*\)",
                "fix": "Set CURLOPT_SSL_VERIFYHOST to 2",
            },

            # World-writable/overly-permissive permissions
            {
                "id": "PERM001",
                "sev": "medium",
                "category": "file_permissions",
                "desc": "open() creates world-writable file (066x)",
                "pat": r"\bopen\s*\([^,]+,\s*[^,]+,\s*0?66[0-7]\s*\)",
                "fix": "Use 0640/0600 and set umask",
            },
            {
                "id": "PERM002",
                "sev": "medium",
                "category": "file_permissions",
                "desc": "chmod() sets world perms (077x)",
                "pat": r"\bchmod\s*\([^,]+,\s*0?77[0-7]\s*\)",
                "fix": "Avoid world perms; least privilege",
            },
            {
                "id": "PERM003",
                "sev": "medium",
                "category": "file_permissions",
                "desc": "mkdir() with 0777",
                "pat": r"\bmkdir\s*\([^,]+,\s*0?777\s*\)",
                "fix": "Use 0755/0700 as appropriate",
            },
            {
                "id": "PERM004",
                "sev": "low",
                "category": "file_permissions",
                "desc": "umask(0) or umask(000)",
                "pat": r"\bumask\s*\(\s*0+\s*\)",
                "fix": "Avoid permissive umask; set restrictive default",
            },

            # Poor input conversion (cleanup/policy)
            {
                "id": "INP001",
                "sev": "low",
                "category": "input_validation",
                "desc": "atoi used; no error handling",
                "pat": r"\batoi\s*\(",
                "fix": "Use strtol/strtoul and check errno",
            },
            {
                "id": "INP002",
                "sev": "low",
                "category": "input_validation",
                "desc": "atol used; no error handling",
                "pat": r"\batol\s*\(",
                "fix": "Use strtol and check errno",
            },
            {
                "id": "INP003",
                "sev": "low",
                "category": "input_validation",
                "desc": "atof used; no error handling",
                "pat": r"\batof\s*\(",
                "fix": "Use strtod and check errno",
            },

            # QCT cleanup policies (memcpy/memmove)
            {
                "id": "QCT001",
                "sev": "high",
                "category": "cleanup_policy",
                "desc": "memcpy used; prefer memscpy()",
                "pat": r"\bmemcpy\s*\(",
                "fix": "Use memscpy(dst, dst_size, src, src_size)",
            },
            {
                "id": "QCT002",
                "sev": "high",
                "category": "cleanup_policy",
                "desc": "memmove used; prefer memsmove()",
                "pat": r"\bmemmove\s*\(",
                "fix": "Use memsmove(dst, dst_size, src, src_size)",
            },

            # Known non-standard reimplementations to replace
            {
                "id": "RE001",
                "sev": "medium",
                "category": "cleanup_policy",
                "desc": "Non-standard strncpy/strncat family detected",
                "pat": r"\b(OSCRTLSTRNCAT|OSCRTLSTRNCPY_S|OSCRTLSTRCPY|rtxStrcat|rtxStrncat|rtxStrcpy|rtxStrncpy)\b",
                "fix": "Replace with CoreBSP libstd strlcpy/strlcat",
            },
            {
                "id": "RE002",
                "sev": "medium",
                "category": "cleanup_policy",
                "desc": "Non-standard UTF8 str* detected",
                "pat": r"\b(rtxUTF8Strcpy|rtxUTF8Strncpy)\b",
                "fix": "Replace with approved safe API",
            },
            {
                "id": "RE003",
                "sev": "medium",
                "category": "cleanup_policy",
                "desc": "Non-standard wide str* detected",
                "pat": r"\b(pbm_wstrncpy|pbm_wstrncat)\b",
                "fix": "Replace with approved wide safe API",
            },
            {
                "id": "RE004",
                "sev": "medium",
                "category": "cleanup_policy",
                "desc": "Non-standard 'safe' API detected",
                "pat": r"\b(std_vstrlprintf|std_strlprintf|std_strlcpy|std_strlcat|std_snprintf|fs_strlcpy|fs_strlcat|gllc_strlcat|w_char_strlcpy)\b",
                "fix": "Use CoreBSP libstd stringl.h implementation",
            },

            # Heuristics for misuse of safe APIs
            {
                "id": "HX001",
                "sev": "medium",
                "category": "safe_api_misuse",
                "desc": "strlcpy/strlcat size computed with strlen()",
                "pat": r"\bstrl(?:cpy|cat)\s*\([^,]+,\s*[^,]+,\s*strlen\s*\(",
                "fix": "Use sizeof(dest) or known buffer size",
            },
            {
                "id": "HX002",
                "sev": "medium",
                "category": "safe_api_misuse",
                "desc": "snprintf/vsnprintf size computed with strlen()",
                "pat": r"\bvs?snprintf\s*\([^,]+,\s*strlen\s*\(",
                "fix": "Use sizeof(dest)",
            },
            {
                "id": "HX003",
                "sev": "medium",
                "category": "safe_api_misuse",
                "desc": "memscpy/memsmove with strlen()",
                "pat": r"\bmems(?:cpy|move)\s*\([^,]+,\s*[^,]+,\s*[^,]+,\s*strlen\s*\(",
                "fix": "If copying strings, use strlcpy/strlcat",
            },
            {
                "id": "HX004",
                "sev": "medium",
                "category": "safe_api_misuse",
                "desc": "memcpy with strlen()",
                "pat": r"\bmemcpy\s*\([^,]+,\s*[^,]+,\s*strlen\s*\(",
                "fix": "Use strlcpy or memscpy with explicit sizes",
            },
            {
                "id": "HX005",
                "sev": "low",
                "category": "safe_api_misuse",
                "desc": "Return value of safe API not checked (strlcpy/strlcat)",
                "pat": r"^[ \t]*strl(?:cpy|cat)\s*\([^;]*\);\s*$",
                "fix": "Capture and check for truncation",
                "flags": re.MULTILINE,
            },
            {
                "id": "HX006",
                "sev": "low",
                "category": "safe_api_misuse",
                "desc": "Return value of snprintf/vsnprintf not checked",
                "pat": r"^[ \t]*vs?snprintf\s*\([^;]*\);\s*$",
                "fix": "Check return value for truncation/errors",
                "flags": re.MULTILINE,
            },
            {
                "id": "HX007",
                "sev": "low",
                "category": "safe_api_misuse",
                "desc": "Return value of memscpy/memsmove not checked",
                "pat": r"^[ \t]*mems(?:cpy|move)\s*\([^;]*\);\s*$",
                "fix": "Verify bytes copied/truncation",
                "flags": re.MULTILINE,
            },
        ]

        # Strip comments but keep strings to reduce false positives in comments
        def _strip_comments_keep_strings(src: str) -> str:
            out: List[str] = []
            i, n = 0, len(src)
            in_sl = in_ml = in_str = in_chr = False
            esc = False

            while i < n:
                ch = src[i]
                nxt = src[i + 1] if i + 1 < n else ""

                if in_sl:
                    if ch == "\n":
                        in_sl = False
                        out.append("\n")
                    i += 1
                    continue

                if in_ml:
                    if ch == "*" and nxt == "/":
                        in_ml = False
                        i += 2
                        continue
                    if ch == "\n":
                        out.append("\n")
                    i += 1
                    continue

                if in_str:
                    out.append(ch)
                    if not esc and ch == '"':
                        in_str = False
                    esc = (ch == "\\" and not esc)
                    i += 1
                    continue

                if in_chr:
                    out.append(ch)
                    if not esc and ch == "'":
                        in_chr = False
                    esc = (ch == "\\" and not esc)
                    i += 1
                    continue

                if ch == "/" and nxt == "/":
                    in_sl = True
                    i += 2
                    continue

                if ch == "/" and nxt == "*":
                    in_ml = True
                    i += 2
                    continue

                if ch == '"':
                    in_str = True
                    esc = False
                    out.append(ch)
                    i += 1
                    continue

                if ch == "'":
                    in_chr = True
                    esc = False
                    out.append(ch)
                    i += 1
                    continue

                out.append(ch)
                i += 1

            return "".join(out)

        # Pre-compile rules with flags
        compiled_rules = []
        for r in rules:
            flags = re.IGNORECASE
            if "flags" in r:
                flags |= r["flags"]
            compiled = {**r, "regex": re.compile(r["pat"], flags)}
            compiled_rules.append(compiled)

        print(
            f"DEBUG security: Starting analysis of {len(c_cpp_files)} files "
            f"with {len(compiled_rules)} rules"
        )

        files_analyzed = 0
        violations_by_file: Dict[str, List[Dict[str, Any]]] = {}
        rule_counts: Counter = Counter()

        # Severity and per-file severity tracking
        severity_breakdown = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        files_with_critical = 0
        files_with_high = 0

        # Scan files
        for entry in c_cpp_files:
            source = entry.get("source") or ""
            if not source.strip():
                print(
                    f"DEBUG security: Skipping empty file: "
                    f"{entry.get('file_relative_path', 'unknown')}"
                )
                continue

            files_analyzed += 1
            rel = _rel_path(entry)

            code = _strip_comments_keep_strings(source)
            code_lines = code.splitlines()
            print(
                f"DEBUG security: Analyzing {rel} "
                f"({len(source)} chars, {len(code)} chars after comment stripping)"
            )

            file_hits: List[Dict[str, Any]] = []
            file_rule_counts: Counter = Counter()
            file_severity_seen = {"critical": False, "high": False}

            for r in compiled_rules:
                matches = list(r["regex"].finditer(code))
                if not matches:
                    continue

                for m in matches:
                    start = m.start()
                    line_idx = code.count("\n", 0, start)
                    line = line_idx + 1
                    last_nl = code.rfind("\n", 0, start)
                    col = (start - (last_nl + 1)) if last_nl >= 0 else start

                    line_text = code_lines[line_idx] if 0 <= line_idx < len(code_lines) else ""
                    snippet = line_text.strip()[:200]

                    hit = {
                        "rule": r["id"],
                        "severity": r["sev"],
                        "category": r.get("category", ""),
                        "description": r["desc"],
                        "line": line,
                        "column": col + 1,
                        "snippet": snippet,
                        "remediation": r.get("fix", ""),
                    }
                    file_hits.append(hit)
                    rule_counts[r["id"]] += 1
                    file_rule_counts[r["id"]] += 1
                    severity_breakdown[r["sev"]] = severity_breakdown.get(r["sev"], 0) + 1

                    if r["sev"] == "critical":
                        file_severity_seen["critical"] = True
                    if r["sev"] == "high":
                        file_severity_seen["high"] = True

            if file_hits:
                violations_by_file[rel] = file_hits
                if file_severity_seen["critical"]:
                    files_with_critical += 1
                if file_severity_seen["high"]:
                    files_with_high += 1
                print(
                    f"DEBUG security: File {rel} - {len(file_hits)} violations: "
                    f"{dict(file_rule_counts)}"
                )
            else:
                print(f"DEBUG security: File {rel} - No violations found")

        total_violations = sum(rule_counts.values())
        print(
            f"DEBUG security: Analysis complete - {files_analyzed} files analyzed, "
            f"{total_violations} total violations"
        )

        # Compute severity-weighted risk score
        risk_points = 0
        critical_present = False

        for rel, hits in violations_by_file.items():
            for h in hits:
                sev = h["severity"]
                w = severity_weight.get(sev, 1)
                risk_points += w
                if sev == "critical":
                    critical_present = True

        print(
            f"DEBUG security: Risk points: {risk_points}, "
            f"Critical present: {critical_present}"
        )
        print(f"DEBUG security: Severity breakdown: {severity_breakdown}")

        # Score model:
        # - Start at 100
        # - Subtract scaled risk points (normalized by codebase size)
        # - Cap subtraction at 90
        # - If any critical finding, cap score at 50
        if files_analyzed > 0:
            normalization = 1.0 / max(1.0, math.sqrt(float(files_analyzed)))
        else:
            normalization = 1.0

        normalized_risk = risk_points * normalization
        score = 100.0 - min(90.0, normalized_risk * 2.0)
        if critical_present:
            score = min(score, 50.0)
        score = max(0.0, score)

        grade = self._score_to_grade(score)
        print(f"DEBUG security: Final score: {score:.1f}")

        files_with_violations = len(violations_by_file)
        clean_files = files_analyzed - files_with_violations

        # Top violation types
        top_types = sorted(rule_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
        top_violation_types = [{"rule": rid, "count": cnt} for rid, cnt in top_types]

        # Issues summary
        issues: List[str] = []

        if files_analyzed == 0:
            issues.append("No C/C++ files to analyze")
        elif total_violations == 0:
            issues.append("No security violations detected - excellent security posture!")
        else:
            issues.append(f"Total security violations: {total_violations}")
            issues.append(f"Files with violations: {files_with_violations}")
            issues.append(f"Clean files (no findings): {clean_files}")

            if critical_present:
                issues.append(
                    f"CRITICAL: {severity_breakdown.get('critical', 0)} critical security issue(s) found "
                    f"across {files_with_critical} file(s) - immediate attention required!"
                )
            if severity_breakdown.get("high", 0) > 0:
                issues.append(
                    f"HIGH: {severity_breakdown.get('high', 0)} high-severity issue(s) in "
                    f"{files_with_high} file(s)"
                )
            if severity_breakdown.get("medium", 0) > 0:
                issues.append(
                    f"MEDIUM: {severity_breakdown.get('medium', 0)} medium-severity issue(s) detected"
                )

            # Add descriptions for top rules
            for rid, cnt in top_types[:5]:
                desc = next((r["desc"] for r in rules if r["id"] == rid), rid)
                issues.append(f"{rid}: {desc} — {cnt} occurrence(s)")

        metrics = {
            "files_analyzed": files_analyzed,
            "total_violations": total_violations,
            "risk_points": risk_points,
            "critical_rule_present": critical_present,
            "top_violation_types": top_violation_types,
            "violations_by_file": violations_by_file,
            "severity_breakdown": severity_breakdown,
            "rule_counts": dict(rule_counts),
            "files_with_violations": files_with_violations,
            "clean_files": clean_files,
            "files_with_critical": files_with_critical,
            "files_with_high": files_with_high,
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