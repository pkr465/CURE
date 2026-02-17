"""
CURE — Codebase Update & Refactor Engine
HITL Constraint Parser

Parses ``*_constraints.md`` files into :class:`ConstraintRule` objects.
These markdown files follow the same format as
``agents/REMEDIATION_CONSTRAINTS.md``:

    | Rule ID | Description | Standard Remediation | LLM Action / Constraint |
    | :--- | :--- | :--- | :--- |
    | QCT001 | memcpy used | Replace with memscpy | IGNORE standard. Add bounds check. |
"""

import logging
import re
from pathlib import Path
from typing import Any, List, Optional

from .config import HITLConfig
from .schemas import ConstraintRule

logger = logging.getLogger(__name__)


class ConstraintParser:
    """Parse ``*_constraints.md`` files into structured rules.

    Discovers markdown tables with a ``| Rule ID`` header and extracts
    each data row as a :class:`ConstraintRule`.
    """

    def __init__(
        self,
        store: Any,  # FeedbackStore
        config: Optional[HITLConfig] = None,
    ) -> None:
        self.store = store
        self.config = config or HITLConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_constraint_file(self, file_path: str) -> List[ConstraintRule]:
        """Parse a single ``*_constraints.md`` file.

        Returns:
            List of :class:`ConstraintRule` objects extracted from the file.
        """
        fp = Path(file_path).resolve()
        if not fp.exists():
            logger.error("Constraint file not found: %s", fp)
            return []

        content = fp.read_text(encoding="utf-8")
        rules = self._extract_rules_from_markdown(content, source_file=str(fp))

        # Persist to store
        if self.config.auto_persist_feedback:
            for rule in rules:
                self.store.save_constraint_rule(rule)

        logger.info("Parsed %d constraint rules from %s", len(rules), fp.name)
        return rules

    def parse_all_constraint_files(self, base_dir: str) -> List[ConstraintRule]:
        """Discover and parse all constraint files under *base_dir*.

        Uses ``config.constraint_file_pattern`` (default ``**/*_constraints.md``).
        """
        base = Path(base_dir).resolve()
        if not base.is_dir():
            logger.warning("Constraints directory not found: %s", base)
            return []

        all_rules: List[ConstraintRule] = []
        for md_file in sorted(base.glob(self.config.constraint_file_pattern)):
            try:
                rules = self.parse_constraint_file(str(md_file))
                all_rules.extend(rules)
            except Exception as exc:
                logger.error("Error parsing %s: %s", md_file, exc)

        logger.info(
            "Total constraint rules from %s: %d", base, len(all_rules)
        )
        return all_rules

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _extract_rules_from_markdown(
        self, content: str, source_file: Optional[str] = None
    ) -> List[ConstraintRule]:
        """Extract markdown table rows into :class:`ConstraintRule` objects."""
        rules: List[ConstraintRule] = []
        lines = content.split("\n")

        # Find table header(s) — there may be multiple tables
        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Look for the table header marker
            if self.config.constraint_table_marker in line:
                # Parse header to determine column mapping
                header_cells = self._parse_row(line)
                col_map = self._build_column_map(header_cells)

                # Skip separator row (e.g., | :--- | :--- | :--- |)
                i += 1
                if i < len(lines) and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i]):
                    i += 1

                # Parse data rows
                while i < len(lines):
                    data_line = lines[i].strip()
                    if not data_line.startswith("|"):
                        break  # End of table

                    cells = self._parse_row(data_line)
                    if not cells or not cells[0]:
                        i += 1
                        continue

                    rule = self._cells_to_rule(cells, col_map, source_file)
                    if rule:
                        rules.append(rule)
                    i += 1
                continue

            i += 1

        return rules

    @staticmethod
    def _parse_row(line: str) -> List[str]:
        """Split a markdown table row into stripped cell values."""
        # Remove leading/trailing pipes and split
        parts = line.split("|")
        # First and last elements are usually empty from the leading/trailing |
        cells = [p.strip() for p in parts[1:-1]] if len(parts) > 2 else []
        return cells

    @staticmethod
    def _build_column_map(header_cells: List[str]) -> dict:
        """Map normalized header names to column indices."""
        col_map: dict = {}
        for idx, cell in enumerate(header_cells):
            lower = cell.lower().strip("*").strip()
            if "rule" in lower and "id" in lower:
                col_map["rule_id"] = idx
            elif "description" in lower:
                col_map["description"] = idx
            elif "standard" in lower and "remediation" in lower:
                col_map["standard_remediation"] = idx
            elif "llm" in lower or "action" in lower or "constraint" in lower:
                col_map["llm_action"] = idx
            elif "reason" in lower:
                col_map["reasoning"] = idx
        return col_map

    @staticmethod
    def _cells_to_rule(
        cells: List[str],
        col_map: dict,
        source_file: Optional[str],
    ) -> Optional[ConstraintRule]:
        """Convert parsed cells to a :class:`ConstraintRule`."""

        def _get(key: str, default: str = "") -> str:
            idx = col_map.get(key)
            if idx is not None and idx < len(cells):
                val = cells[idx].strip("*").strip()
                return val if val else default
            return default

        rule_id = _get("rule_id")
        if not rule_id:
            return None

        return ConstraintRule(
            rule_id=rule_id,
            description=_get("description"),
            standard_remediation=_get("standard_remediation"),
            llm_action=_get("llm_action"),
            reasoning=_get("reasoning") or None,
            source_file=source_file,
        )
