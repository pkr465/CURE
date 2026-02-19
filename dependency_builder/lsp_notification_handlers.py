import logging
from typing import Dict, Any, Callable

from dependency_builder.utils import clean_uri

def semantic_highlight_handler(logger: logging.Logger) -> Callable[[Dict[str, Any]], None]:
    """
    Handles $ccls/publishSemanticHighlight notifications.
    Used to understand how the server is parsing symbols in real-time.
    """
    def handler(params: Dict[str, Any]):
        try:
            uri = params.get("uri", "")
            symbols = params.get("symbols", [])
            file_path = clean_uri(uri)

            # Use debug to prevent log spam during massive indexing
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"[SemanticHighlight] File: {file_path} | Symbols found: {len(symbols)}")

            if not symbols:
                return

            for sym in symbols:
                if not isinstance(sym, dict):
                    continue
                kind = sym.get("kind")
                role = sym.get("role")
                for r in sym.get("ranges", []):
                    if not isinstance(r, dict):
                        continue
                    start = r.get("start", {})
                    end = r.get("end", {})
        except Exception as e:
            logger.debug(f"[SemanticHighlight] Error processing notification: {e}")
    return handler


def skipped_ranges_handler(logger: logging.Logger) -> Callable[[Dict[str, Any]], None]:
    """
    Handles $ccls/publishSkippedRanges notifications.
    Useful for identifying #if 0 blocks or inactive preprocessor branches.
    """
    def handler(params: Dict[str, Any]):
        try:
            uri = params.get("uri", "")
            skipped_ranges = params.get("skippedRanges", [])
            file_path = clean_uri(uri)

            if not skipped_ranges:
                return

            logger.info(f"[SkippedRanges] File: {file_path} | Skipped Blocks: {len(skipped_ranges)}")

            for r in skipped_ranges:
                if not isinstance(r, dict):
                    continue
                start = r.get("start", {})
                end = r.get("end", {})
                logger.info(f"  - Block skipped: Lines {start.get('line')} to {end.get('line')}")
        except Exception as e:
            logger.debug(f"[SkippedRanges] Error processing notification: {e}")
    return handler


def progress_handler(logger: logging.Logger) -> Callable[[Dict[str, Any]], None]:
    """
    Handles $/progress notifications.
    Tracks background indexing or compilation tasks.
    """
    def handler(params: Dict[str, Any]):
        try:
            token = params.get("token")
            value = params.get("value", {})
            if not isinstance(value, dict):
                return

            kind = value.get("kind")
            title = value.get("title", "")
            message = value.get("message", "")
            percentage = value.get("percentage")

            if kind == "begin":
                logger.info(f"[LSP Progress] [{token}] Started: {title} {message}")
            elif kind == "report":
                pct_display = f" ({percentage}%)" if percentage is not None else ""
                if message or percentage == 100:
                    logger.info(f"[LSP Progress] [{token}] {message}{pct_display}")
                else:
                    logger.debug(f"[LSP Progress] [{token}] Processing...{pct_display}")
            elif kind == "end":
                logger.info(f"[LSP Progress] [{token}] Completed: {message}")
        except Exception as e:
            logger.debug(f"[Progress] Error processing notification: {e}")
    return handler


def work_done_progress_create_handler(logger: logging.Logger) -> Callable[[Dict[str, Any]], None]:
    """
    Handles window/workDoneProgress/create requests.
    """
    def handler(params: Dict[str, Any]):
        try:
            token = params.get("token")
            logger.debug(f"[WorkDoneProgress] Token created: {token}")
        except Exception as e:
            logger.debug(f"[WorkDoneProgress] Error: {e}")
    return handler