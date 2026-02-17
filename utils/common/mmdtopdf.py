# mmdtopdf.py
"""
Standalone Mermaid diagram converter.

Provides:
- Mermaid .mmd -> PNG conversion via Mermaid CLI (mmdc)
- PNG -> PDF conversion via img2pdf (or Pillow fallback)
- SVG output support via mmdc
- Batch conversion of multiple .mmd files
- Configurable mmdc path, background color, theme, dimensions

Dependencies: subprocess (stdlib)
Optional:     img2pdf (pip install img2pdf)
              Pillow (pip install Pillow) — fallback for img2pdf

Usage:
    from utils.common.mmdtopdf import MermaidConverter

    converter = MermaidConverter()
    converter.mmd_to_png("diagram.mmd", "diagram.png")
    converter.mmd_to_svg("diagram.mmd", "diagram.svg")
    png, pdf = converter.convert("diagram.mmd", "diagram.pdf")
    results = converter.convert_batch(["a.mmd", "b.mmd"], output_dir="./out")
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MermaidConfig:
    """Configuration for Mermaid CLI conversion."""
    mmdc_path: str = "mmdc"
    background_color: str = "white"
    theme: str = "default"
    width: Optional[int] = None
    height: Optional[int] = None
    scale: Optional[float] = None
    timeout_seconds: int = 60
    puppeteer_config: Optional[str] = None
    default_output_dir: str = "./out"
    keep_intermediate_png: bool = True

    @classmethod
    def from_env(cls, env_config=None) -> "MermaidConfig":
        import os
        if env_config is None:
            try:
                from utils.parsers.env_parser import EnvConfig
                env_config = EnvConfig()
            except ImportError:
                env_config = None

        def _get(key, default=""):
            if env_config and hasattr(env_config, "get"):
                val = env_config.get(key)
                if val is not None and val != "":
                    return val
            return os.getenv(key, default)

        return cls(
            mmdc_path=str(_get("MMDC_PATH", "mmdc")),
            background_color=str(_get("MERMAID_BG_COLOR", "white")),
            theme=str(_get("MERMAID_THEME", "default")),
            default_output_dir=str(_get("OUT_DIR", "./out")),
        )


class MermaidError(Exception):
    """Base error for Mermaid operations."""

class MermaidCLIError(MermaidError):
    """Mermaid CLI (mmdc) execution failed."""

class MermaidNotFoundError(MermaidError):
    """Mermaid CLI (mmdc) not found on PATH."""

class ConversionError(MermaidError):
    """File conversion failed."""


class MermaidConverter:
    """Converts Mermaid diagrams (.mmd) to PNG, SVG, and PDF formats."""

    def __init__(self, config=None, env_config=None, mmdc_path=None):
        self.config = config or MermaidConfig.from_env(env_config)
        if mmdc_path:
            self.config.mmdc_path = mmdc_path
        self.logger = logger

    @classmethod
    def from_env(cls, env_config=None):
        return cls(config=MermaidConfig.from_env(env_config))

    def is_mmdc_available(self) -> bool:
        return shutil.which(self.config.mmdc_path) is not None

    def _ensure_mmdc(self):
        if not self.is_mmdc_available():
            raise MermaidNotFoundError(
                f"Mermaid CLI not found at '{self.config.mmdc_path}'. "
                "Install with: npm install -g @mermaid-js/mermaid-cli"
            )

    @staticmethod
    def _ensure_dir(file_path):
        parent = Path(file_path).parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)

    def mmd_to_png(self, mmd_file, png_file) -> Path:
        mmd_path, png_path = Path(mmd_file), Path(png_file)
        if not mmd_path.exists():
            raise FileNotFoundError(f"Mermaid file not found: {mmd_path}")
        self._ensure_mmdc()
        self._ensure_dir(png_path)
        cmd = self._build_mmdc_command(mmd_path, png_path)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=self.config.timeout_seconds, check=False)
            if result.returncode != 0:
                raise MermaidCLIError(f"mmdc failed (exit {result.returncode}): {result.stderr.strip()}")
            if not png_path.exists():
                raise MermaidCLIError(f"mmdc completed but output not created: {png_path}")
            self.logger.info("PNG generated: %s", png_path)
            return png_path
        except subprocess.TimeoutExpired:
            raise MermaidCLIError(f"mmdc timed out after {self.config.timeout_seconds}s")

    def mmd_to_svg(self, mmd_file, svg_file) -> Path:
        mmd_path, svg_path = Path(mmd_file), Path(svg_file)
        if not mmd_path.exists():
            raise FileNotFoundError(f"Mermaid file not found: {mmd_path}")
        self._ensure_mmdc()
        self._ensure_dir(svg_path)
        cmd = self._build_mmdc_command(mmd_path, svg_path)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=self.config.timeout_seconds, check=False)
            if result.returncode != 0:
                raise MermaidCLIError(f"mmdc SVG failed (exit {result.returncode}): {result.stderr.strip()}")
            self.logger.info("SVG generated: %s", svg_path)
            return svg_path
        except subprocess.TimeoutExpired:
            raise MermaidCLIError(f"mmdc timed out after {self.config.timeout_seconds}s")

    def png_to_pdf(self, png_file, pdf_file) -> Path:
        png_path, pdf_path = Path(png_file), Path(pdf_file)
        if not png_path.exists():
            raise FileNotFoundError(f"PNG file not found: {png_path}")
        self._ensure_dir(pdf_path)
        try:
            import img2pdf
            with open(pdf_path, "wb") as f:
                f.write(img2pdf.convert(str(png_path)))
            self.logger.info("PDF generated (img2pdf): %s", pdf_path)
            return pdf_path
        except ImportError:
            pass
        except Exception as e:
            self.logger.warning("img2pdf failed: %s, trying Pillow", e)
        try:
            from PIL import Image
            image = Image.open(png_path)
            if image.mode == "RGBA":
                bg = Image.new("RGB", image.size, (255, 255, 255))
                bg.paste(image, mask=image.split()[3])
                image = bg
            elif image.mode != "RGB":
                image = image.convert("RGB")
            image.save(str(pdf_path), "PDF", resolution=150.0)
            self.logger.info("PDF generated (Pillow): %s", pdf_path)
            return pdf_path
        except ImportError:
            raise ConversionError("Neither 'img2pdf' nor 'Pillow' installed.")
        except Exception as e:
            raise ConversionError(f"PNG to PDF failed: {e}") from e

    def convert(self, mmd_file, pdf_file, png_file=None) -> Tuple[Path, Path]:
        pdf_path = Path(pdf_file)
        png_path = Path(png_file) if png_file else pdf_path.with_suffix(".png")
        generated_png = self.mmd_to_png(mmd_file, png_path)
        generated_pdf = self.png_to_pdf(generated_png, pdf_path)
        if not self.config.keep_intermediate_png and generated_png.exists():
            try:
                generated_png.unlink()
            except OSError:
                pass
        return generated_png, generated_pdf

    def convert_batch(self, mmd_files, output_dir=None, output_format="pdf") -> List[Dict[str, Any]]:
        out_dir = Path(output_dir or self.config.default_output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for mmd_file in mmd_files:
            mmd_path = Path(mmd_file)
            result = {"input": str(mmd_path), "output": "", "success": False, "error": None}
            try:
                if output_format == "png":
                    output = out_dir / f"{mmd_path.stem}.png"
                    self.mmd_to_png(mmd_path, output)
                elif output_format == "svg":
                    output = out_dir / f"{mmd_path.stem}.svg"
                    self.mmd_to_svg(mmd_path, output)
                else:
                    _, output = self.convert(mmd_path, out_dir / f"{mmd_path.stem}.pdf")
                result["output"] = str(output)
                result["success"] = True
            except Exception as e:
                result["error"] = str(e)
            results.append(result)
        return results

    def _build_mmdc_command(self, input_path, output_path) -> List[str]:
        cmd = [self.config.mmdc_path, "-i", str(input_path), "-o", str(output_path),
               "--backgroundColor", self.config.background_color, "--theme", self.config.theme]
        if self.config.width:
            cmd.extend(["--width", str(self.config.width)])
        if self.config.height:
            cmd.extend(["--height", str(self.config.height)])
        if self.config.scale:
            cmd.extend(["--scale", str(self.config.scale)])
        if self.config.puppeteer_config:
            cmd.extend(["--puppeteerConfigFile", self.config.puppeteer_config])
        return cmd

    def __repr__(self):
        avail = "available" if self.is_mmdc_available() else "not found"
        return f"MermaidConverter(mmdc='{self.config.mmdc_path}' [{avail}], theme='{self.config.theme}')"
