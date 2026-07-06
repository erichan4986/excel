"""Centralized path constants for cross-platform compatibility."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
CLEANED_DIR = DATA_DIR / "cleaned"
OUTPUT_DIR = DATA_DIR / "outputs"
SAMPLE_DIR = DATA_DIR / "samples"

STATIC_DIR = PROJECT_ROOT / "static"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
