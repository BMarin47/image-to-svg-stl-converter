from __future__ import annotations

import math
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


MISSING_IMPORTS: list[str] = []

try:
    import cv2
except ImportError:
    cv2 = None
    MISSING_IMPORTS.append("opencv-python")

try:
    import numpy as np
except ImportError:
    np = None
    MISSING_IMPORTS.append("numpy")

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None
    MISSING_IMPORTS.append("Pillow")


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"
STARTUP_ERROR_LOG = BASE_DIR / "startup_error.txt"
OUTPUT_MANIFEST = OUTPUT_DIR / "archivos_generados.txt"

DEFAULT_OPENSCAD_PATH = r"C:\Program Files\OpenSCAD\openscad.exe"
DEFAULT_MODEL_WIDTH_MM = 100.0
DEFAULT_COLOR_COUNT = 8
DEFAULT_ALPHA_THRESHOLD = 8
DEFAULT_MIN_AREA_PX = 16
DEFAULT_MORPH_SIZE_PX = 2
KMEANS_MAX_SAMPLE_PIXELS = 100_000
BASE_MODE_NONE = "none"
BASE_MODE_CONTOUR = "contour"
BASE_MODE_RECTANGLE = "rectangle"

Point = Tuple[int, int]
FilledShape = Tuple[List[Point], List[List[Point]]]


@dataclass
class DetectedColor:
    label_id: int
    rgb: tuple[int, int, int]
    hex_value: str
    pixel_count: int
    border_fraction: float
    export_default: bool
    note: str


@dataclass
class ColorRow:
    color: DetectedColor
    export_var: tk.BooleanVar
    z_var: tk.StringVar
    thickness_var: tk.StringVar


@dataclass
class ExportRequest:
    color: DetectedColor
    z_offset: float
    thickness: float


@dataclass(frozen=True)
class ProductPreset:
    name: str
    model_width_mm: float
    base_thickness_mm: float
    relief_thickness_mm: float
    relief_z_offset_mm: float
    export_background: bool
    base_mode: str
    keyring_hole: bool = False
    hole_diameter_mm: float = 5.0
    hole_margin_mm: float = 4.0


PRODUCT_PRESETS = (cm