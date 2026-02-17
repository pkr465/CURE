"""
CURE — Codebase Update & Refactor Engine
NDJSON Writer: converts flattened JSON arrays into line-delimited NDJSON.
"""

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class NDJSONWriter:
    """
    Converts a flattened JSON file into a proper NDJSON file.

    Supports two input formats:
      1) A single JSON array: ``[ {...}, {...}, ... ]``
      2) One JSON object per line (JSON-per-line / pseudo-NDJSON)

    Output:
      True NDJSON — one JSON object per line, each line valid JSON.
    """

    def __init__(
        self,
        input_json_path: Union[str, Path],
        output_ndjson_path: Union[str, Path],
        record_filter: Optional[Callable[[Dict[str, Any]], bool]] = None,
        record_transform: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        ensure_ascii: bool = False,
    ):
        self.input_json_path = Path(input_json_path)
        self.output_ndjson_path = Path(output_ndjson_path)
        self.record_filter = record_filter
        self.record_transform = record_transform
        self.ensure_ascii = ensure_ascii

    def _load_as_array(self) -> List[Dict[str, Any]]:
        """
        Try to load the file as a single JSON array.
        """
        logger.info(f"Attempting to load flattened JSON as array: {self.input_json_path}")
        with self.input_json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError(
                f"Expected a list at the root of {self.input_json_path}, "
                f"got {type(data).__name__} instead."
            )

        entries: List[Dict[str, Any]] = []
        for idx, item in enumerate(data, 1):
            if not isinstance(item, dict):
                logger.warning(
                    f"Skipping non-dict entry at index {idx} in {self.input_json_path}: "
                    f"{type(item).__name__}"
                )
                continue
            entries.append(item)

        logger.info(f"Loaded {len(entries)} dict entries from flattened JSON (array mode).")
        return entries

    def _load_as_json_per_line(self) -> List[Dict[str, Any]]:
        """
        Fallback: treat each line as an independent JSON object.
        """
        logger.info(
            f"Falling back to JSON-per-line mode for flattened JSON: {self.input_json_path}"
        )
        entries: List[Dict[str, Any]] = []
        with self.input_json_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception as e:
                    logger.warning(
                        f"Skipping unparsable line {line_num} in {self.input_json_path}: {e}"
                    )
                    continue
                if not isinstance(obj, dict):
                    logger.warning(
                        f"Skipping non-dict JSON at line {line_num} "
                        f"in {self.input_json_path}: {type(obj).__name__}"
                    )
                    continue
                entries.append(obj)

        logger.info(f"Loaded {len(entries)} dict entries from flattened JSON (line mode).")
        return entries

    def load_flat_json(self) -> List[Dict[str, Any]]:
        """
        Load the flattened JSON file as a list of dicts.

        Strategy:
          1) Try to parse as a single JSON array.
          2) If that fails with JSONDecodeError (e.g., 'Extra data'), fall back
             to treating the file as JSON-per-line.

        Returns:
            A list of dict entries.

        Raises:
            FileNotFoundError: if the file does not exist.
            ValueError: if no valid dict entries could be parsed.
        """
        if not self.input_json_path.exists():
            raise FileNotFoundError(f"Input flattened JSON file not found: {self.input_json_path}")

        try:
            return self._load_as_array()
        except Exception as e:
            logger.warning(
                f"Failed to load {self.input_json_path} as a JSON array ({e}); "
                f"attempting JSON-per-line mode..."
            )
            # Now try JSON-per-line
            entries = self._load_as_json_per_line()
            if not entries:
                raise ValueError(
                    f"Failed to parse any valid dict entries from {self.input_json_path} "
                    f"using JSON-per-line mode."
                )
            return entries

    def write_ndjson(self, entries: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Write a list of dict entries to an NDJSON file.

        Returns:
            A summary dict with counts:
              - total_entries
              - written_entries
              - skipped_entries
        """
        self.output_ndjson_path.parent.mkdir(parents=True, exist_ok=True)

        total_entries = 0
        written_entries = 0
        skipped_entries = 0

        logger.info(f"Writing NDJSON to: {self.output_ndjson_path}")

        with self.output_ndjson_path.open("w", encoding="utf-8") as f:
            for entry in entries:
                total_entries += 1

                if self.record_filter and not self.record_filter(entry):
                    skipped_entries += 1
                    continue

                if self.record_transform:
                    try:
                        entry = self.record_transform(entry)
                        if not isinstance(entry, dict):
                            logger.warning(
                                "record_transform did not return a dict; skipping this entry."
                            )
                            skipped_entries += 1
                            continue
                    except Exception as e:
                        logger.warning(
                            f"record_transform raised an exception; skipping this entry: {e}"
                        )
                        skipped_entries += 1
                        continue

                try:
                    json_line = json.dumps(entry, ensure_ascii=self.ensure_ascii)
                    f.write(json_line + "\n")
                    written_entries += 1
                except Exception as e:
                    logger.warning(f"Failed to serialize entry to NDJSON; skipping. Error: {e}")
                    skipped_entries += 1

        logger.info(
            f"NDJSON write complete: total={total_entries}, "
            f"written={written_entries}, skipped={skipped_entries}"
        )

        return {
            "total_entries": total_entries,
            "written_entries": written_entries,
            "skipped_entries": skipped_entries,
        }

    def run(self) -> Dict[str, int]:
        """
        Full pipeline:
          1) Load flattened JSON (array or JSON-per-line).
          2) Write NDJSON.

        Returns:
            A summary dict from write_ndjson (counts).
        """
        entries = self.load_flat_json()
        summary = self.write_ndjson(entries)
        return summary