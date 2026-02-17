"""
Excel Report Adapter for generating consolidated analysis reports.

Combines results from all other adapters and exports to an Excel workbook
with separate tabs for overview and per-adapter findings.
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.adapters.base_adapter import BaseStaticAdapter


class ExcelReportAdapter(BaseStaticAdapter):
    """
    Generates Excel reports from adapter results.

    Creates a multi-tab workbook:
    - static_overview: Summary of all adapters' scores and findings
    - static_<adapter>: Detailed findings per adapter
    """

    def __init__(self, output_dir: str = "./out", debug: bool = False):
        """
        Initialize ExcelReportAdapter.

        Args:
            output_dir: Directory where Excel file will be saved.
            debug: Enable debug logging.
        """
        super().__init__("excel_report", debug)
        self.output_dir = output_dir
        # Ensure output directory exists
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

    def analyze(
        self,
        file_cache: Optional[List[Dict[str, Any]]] = None,
        ccls_navigator: Optional[Any] = None,
        dependency_graph: Optional[Dict[str, Any]] = None,
        adapter_results: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Generate Excel report from adapter results.

        Args:
            file_cache: Unused (for interface compatibility).
            ccls_navigator: Unused (for interface compatibility).
            dependency_graph: Unused (for interface compatibility).
            adapter_results: Dict mapping adapter names to their result dicts.

        Returns:
            Standard result dict with file path and stats.
        """
        if not adapter_results:
            return self._empty_result("No adapter results to report")

        try:
            # Import ExcelWriter
            from utils.common.excel_writer import ExcelWriter

            # Create output file path
            file_path = os.path.join(self.output_dir, "detailed_code_review.xlsx")

            # Create workbook
            writer = ExcelWriter(file_path)

            # Add overview tab
            self._add_overview_tab(writer, adapter_results)

            # Add detail tabs for each adapter
            tabs_generated = self._add_adapter_tabs(writer, adapter_results)

            # Save workbook
            writer.save()

            # Compute total findings
            total_findings = sum(
                len(result.get("details", []))
                for result in adapter_results.values()
            )

            self.logger.info(f"Excel report saved to {file_path}")

            return {
                "score": 100.0,
                "grade": "A",
                "metrics": {
                    "file_path": file_path,
                    "tabs_generated": tabs_generated,
                    "total_findings": total_findings,
                },
                "issues": [],
                "details": [],
                "tool_available": True,
            }

        except ImportError as e:
            return self._handle_tool_unavailable(
                "ExcelWriter",
                f"ExcelWriter import failed: {e}",
            )
        except Exception as e:
            return self._handle_tool_unavailable(
                "excel_generation",
                f"Failed to generate Excel report: {e}",
            )

    def _add_overview_tab(
        self,
        writer: Any,
        adapter_results: Dict[str, Dict[str, Any]],
    ) -> None:
        """
        Add static_overview tab with summary data.

        Args:
            writer: ExcelWriter instance.
            adapter_results: Results from all adapters.
        """
        try:
            # Build summary data
            summary_data = {
                "Report Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Adapters Run": len(adapter_results),
            }

            # Add per-adapter summary
            for adapter_name, result in adapter_results.items():
                score = result.get("score", 0.0)
                grade = result.get("grade", "F")
                tool_available = result.get("tool_available", False)

                # Score and grade
                summary_data[f"{adapter_name} Score"] = f"{score} ({grade})"

                # Issue count
                issues_count = len(result.get("issues", []))
                summary_data[f"{adapter_name} Issues"] = issues_count

                # Tool availability
                summary_data[f"{adapter_name} Tool Available"] = (
                    "yes" if tool_available else "no"
                )

            # Convert to rows for ExcelWriter
            headers = list(summary_data.keys())
            data_rows = [list(summary_data.values())]

            writer.add_data_sheet(
                headers=headers,
                data=data_rows,
                sheet_name="static_overview",
            )

            self.logger.debug("Added static_overview tab")

        except Exception as e:
            self.logger.warning(f"Failed to add overview tab: {e}")

    def _add_adapter_tabs(
        self,
        writer: Any,
        adapter_results: Dict[str, Dict[str, Any]],
    ) -> int:
        """
        Add detail tabs for each adapter.

        Args:
            writer: ExcelWriter instance.
            adapter_results: Results from all adapters.

        Returns:
            Number of tabs generated.
        """
        tabs_generated = 0

        for adapter_name, result in adapter_results.items():
            details = result.get("details", [])
            if not details:
                continue

            try:
                # Compute sheet name with Excel limit (31 chars)
                base_sheet_name = f"static_{adapter_name}"
                sheet_name = self._truncate_sheet_name(base_sheet_name)

                # Extract headers and build data rows
                headers = [
                    "File",
                    "Function",
                    "Line",
                    "Description",
                    "Severity",
                    "Category",
                    "CWE",
                ]

                data_rows = []
                for detail in details:
                    row = [
                        detail.get("file", ""),
                        detail.get("function", ""),
                        detail.get("line", ""),
                        detail.get("description", ""),
                        detail.get("severity", ""),
                        detail.get("category", ""),
                        detail.get("cwe", ""),
                    ]
                    data_rows.append(row)

                # Add table sheet
                writer.add_table_sheet(
                    headers=headers,
                    data=data_rows,
                    sheet_name=sheet_name,
                    status_column="Severity",
                )

                tabs_generated += 1
                self.logger.debug(f"Added tab: {sheet_name} ({len(data_rows)} findings)")

            except Exception as e:
                self.logger.warning(
                    f"Failed to add tab for adapter {adapter_name}: {e}"
                )

        return tabs_generated

    def _truncate_sheet_name(self, name: str, max_length: int = 31) -> str:
        """
        Truncate sheet name to Excel limit.

        Excel sheet names have a 31-character limit.

        Args:
            name: Original sheet name.
            max_length: Maximum length (Excel limit is 31).

        Returns:
            Truncated name.
        """
        if len(name) <= max_length:
            return name
        return name[:max_length]
