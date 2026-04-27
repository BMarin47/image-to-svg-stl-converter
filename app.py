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
VECTOR_UPSCALE_MAX = 4
VECTOR_UPSCALE_MAX_PIXELS = 8_000_000
BASE_MODE_NONE = "none"
BASE_MODE_CONTOUR = "contour"
BASE_MODE_RECTANGLE = "rectangle"
BASE_MODE_KEYRING = "keyring"

Point = Tuple[float, float]
FilledShape = Tuple[List[Point], List[List[Point]]]


@dataclass
class DetectedColor:
    label_id: int
    rgb: tuple[int, int, int]
    hex_value: str
    pixel_count: int
    visible_fraction: float
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


PRODUCT_PRESETS = (
    ProductPreset(
        name="Logo simple",
        model_width_mm=100.0,
        base_thickness_mm=0.0,
        relief_thickness_mm=1.0,
        relief_z_offset_mm=0.0,
        export_background=False,
        base_mode=BASE_MODE_NONE,
    ),
    ProductPreset(
        name="Llavero",
        model_width_mm=60.0,
        base_thickness_mm=2.2,
        relief_thickness_mm=1.0,
        relief_z_offset_mm=2.2,
        export_background=False,
        base_mode=BASE_MODE_KEYRING,
        keyring_hole=True,
        hole_diameter_mm=5.0,
        hole_margin_mm=4.0,
    ),
    ProductPreset(
        name="Imán",
        model_width_mm=65.0,
        base_thickness_mm=1.2,
        relief_thickness_mm=0.8,
        relief_z_offset_mm=1.2,
        export_background=False,
        base_mode=BASE_MODE_CONTOUR,
    ),
    ProductPreset(
        name="Placa",
        model_width_mm=100.0,
        base_thickness_mm=2.0,
        relief_thickness_mm=0.8,
        relief_z_offset_mm=2.0,
        export_background=False,
        base_mode=BASE_MODE_RECTANGLE,
    ),
    ProductPreset(
        name="Logo en relieve",
        model_width_mm=90.0,
        base_thickness_mm=1.6,
        relief_thickness_mm=1.0,
        relief_z_offset_mm=1.6,
        export_background=False,
        base_mode=BASE_MODE_CONTOUR,
    ),
)
PRODUCT_PRESETS_BY_NAME = {preset.name: preset for preset in PRODUCT_PRESETS}
PRODUCT_PRESET_NAMES = [preset.name for preset in PRODUCT_PRESETS]


def ensure_dependencies() -> None:
    if MISSING_IMPORTS:
        packages = ", ".join(MISSING_IMPORTS)
        raise RuntimeError(
            "Faltan dependencias de Python: "
            f"{packages}\n\nInstala las dependencias y vuelve a ejecutar la app."
        )


def ensure_directories() -> None:
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def safe_hex_for_filename(hex_value: str) -> str:
    return hex_value.strip().lstrip("#").upper()


def parse_float(value: str, field_name: str, minimum: float | None = None) -> float:
    cleaned = value.strip().replace(",", ".")
    try:
        number = float(cleaned)
    except ValueError as exc:
        raise ValueError(f"{field_name} debe ser un numero valido.") from exc

    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name} debe ser mayor o igual a {minimum}.")
    return number


def parse_int(value: str, field_name: str, minimum: int | None = None) -> int:
    cleaned = value.strip()
    try:
        number = int(cleaned)
    except ValueError as exc:
        raise ValueError(f"{field_name} debe ser un entero valido.") from exc

    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name} debe ser mayor o igual a {minimum}.")
    return number


def format_number(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text else "0"


def format_coord(value: float) -> str:
    if abs(value - round(value)) < 0.001:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def chunked(items: np.ndarray, size: int) -> Iterable[np.ndarray]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class ImageToSvgStlConverter(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Image to SVG/STL Converter - Bambu multicolor")
        self.geometry("980x720")
        self.minsize(860, 620)

        ensure_directories()

        self.image_path: Path | None = None
        self.image_rgba: np.ndarray | None = None
        self.valid_mask: np.ndarray | None = None
        self.labels_map: np.ndarray | None = None
        self.detected_colors: list[DetectedColor] = []
        self.color_rows: list[ColorRow] = []
        self.preview_photo: ImageTk.PhotoImage | None = None

        default_preset = PRODUCT_PRESETS[0]
        self.openscad_var = tk.StringVar(value=DEFAULT_OPENSCAD_PATH)
        self.max_colors_var = tk.StringVar(value=str(DEFAULT_COLOR_COUNT))
        self.preset_var = tk.StringVar(value=default_preset.name)
        self.model_width_var = tk.StringVar(value=format_number(default_preset.model_width_mm))
        self.base_thickness_var = tk.StringVar(
            value=format_number(default_preset.base_thickness_mm)
        )
        self.relief_thickness_var = tk.StringVar(
            value=format_number(default_preset.relief_thickness_mm)
        )
        self.relief_z_offset_var = tk.StringVar(
            value=format_number(default_preset.relief_z_offset_mm)
        )
        self.export_background_var = tk.BooleanVar(value=default_preset.export_background)
        self.clear_output_var = tk.BooleanVar(value=True)
        self.alpha_threshold_var = tk.StringVar(value=str(DEFAULT_ALPHA_THRESHOLD))
        self.min_area_var = tk.StringVar(value=str(DEFAULT_MIN_AREA_PX))
        self.morph_size_var = tk.StringVar(value=str(DEFAULT_MORPH_SIZE_PX))
        self.status_var = tk.StringVar(value="Carga una imagen PNG/JPG para comenzar.")

        self._build_ui()

        if MISSING_IMPORTS:
            self.after(250, self._show_missing_dependencies)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x")

        ttk.Button(top, text="Cargar imagen", command=self.select_image).pack(side="left")
        ttk.Button(top, text="Detectar colores", command=self.detect_colors_clicked).pack(
            side="left", padx=(8, 0)
        )
        self.process_button = ttk.Button(
            top,
            text="Procesar Colores",
            command=self.process_colors_clicked,
            state="disabled",
        )
        self.process_button.pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Abrir carpeta output", command=self.open_output_folder).pack(
            side="left", padx=(8, 0)
        )

        self.image_label_var = tk.StringVar(value="Sin imagen seleccionada")
        ttk.Label(top, textvariable=self.image_label_var).pack(side="left", padx=12)

        settings = ttk.LabelFrame(root, text="Configuracion")
        settings.pack(fill="x", pady=(12, 8))
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        ttk.Label(settings, text="OpenSCAD").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(settings, textvariable=self.openscad_var).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=8, pady=6
        )
        ttk.Button(settings, text="Buscar", command=self.select_openscad).grid(
            row=0, column=4, sticky="e", padx=8, pady=6
        )

        ttk.Label(settings, text="Colores max.").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        tk.Spinbox(settings, from_=1, to=32, textvariable=self.max_colors_var, width=8).grid(
            row=1, column=1, sticky="w", padx=8, pady=6
        )

        ttk.Label(settings, text="Ancho modelo mm").grid(row=1, column=2, sticky="w", padx=8, pady=6)
        ttk.Entry(settings, textvariable=self.model_width_var, width=12).grid(
            row=1, column=3, sticky="w", padx=8, pady=6
        )

        ttk.Label(settings, text="Alpha minimo").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(settings, textvariable=self.alpha_threshold_var, width=12).grid(
            row=2, column=1, sticky="w", padx=8, pady=6
        )

        ttk.Label(settings, text="Area minima px2").grid(row=2, column=2, sticky="w", padx=8, pady=6)
        ttk.Entry(settings, textvariable=self.min_area_var, width=12).grid(
            row=2, column=3, sticky="w", padx=8, pady=6
        )

        ttk.Label(settings, text="Limpieza px").grid(row=2, column=4, sticky="w", padx=8, pady=6)
        ttk.Entry(settings, textvariable=self.morph_size_var, width=8).grid(
            row=2, column=5, sticky="w", padx=8, pady=6
        )

        preset_box = ttk.LabelFrame(root, text="Preset de producto")
        preset_box.pack(fill="x", pady=(0, 8))
        for column in range(8):
            preset_box.columnconfigure(column, weight=0)
        preset_box.columnconfigure(1, weight=1)

        ttk.Label(preset_box, text="Preset").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.preset_combo = ttk.Combobox(
            preset_box,
            textvariable=self.preset_var,
            values=PRODUCT_PRESET_NAMES,
            state="readonly",
            width=20,
        )
        self.preset_combo.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        self.preset_combo.bind("<<ComboboxSelected>>", self._preset_selected)
        ttk.Button(preset_box, text="Aplicar preset", command=self.apply_selected_preset).grid(
            row=0, column=2, sticky="w", padx=8, pady=6
        )
        ttk.Checkbutton(
            preset_box,
            text="Exportar fondo detectado",
            variable=self.export_background_var,
            command=self._refresh_color_export_defaults,
        ).grid(row=0, column=3, columnspan=2, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(
            preset_box,
            text="Limpiar output antes de generar",
            variable=self.clear_output_var,
        ).grid(row=0, column=5, columnspan=3, sticky="w", padx=8, pady=6)

        ttk.Label(preset_box, text="Grosor base mm").grid(
            row=1, column=0, sticky="w", padx=8, pady=6
        )
        ttk.Entry(preset_box, textvariable=self.base_thickness_var, width=10).grid(
            row=1, column=1, sticky="w", padx=8, pady=6
        )
        ttk.Label(preset_box, text="Grosor relieve mm").grid(
            row=1, column=2, sticky="w", padx=8, pady=6
        )
        ttk.Entry(preset_box, textvariable=self.relief_thickness_var, width=10).grid(
            row=1, column=3, sticky="w", padx=8, pady=6
        )
        ttk.Label(preset_box, text="Z offset relieve mm").grid(
            row=1, column=4, sticky="w", padx=8, pady=6
        )
        ttk.Entry(preset_box, textvariable=self.relief_z_offset_var, width=10).grid(
            row=1, column=5, sticky="w", padx=8, pady=6
        )
        ttk.Button(
            preset_box,
            text="Aplicar a colores",
            command=self.apply_relief_values_to_rows,
        ).grid(row=1, column=6, sticky="w", padx=8, pady=6)

        body = ttk.PanedWindow(root, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, padding=(0, 0, 8, 0))
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=3)

        preview_box = ttk.LabelFrame(left, text="Vista previa")
        preview_box.pack(fill="both", expand=False)
        self.preview_label = ttk.Label(preview_box, text="Sin imagen", anchor="center")
        self.preview_label.pack(fill="both", expand=True, padx=8, pady=8)

        help_box = ttk.LabelFrame(left, text="Salida")
        help_box.pack(fill="both", expand=True, pady=(8, 0))
        output_text = (
            f"STL: {OUTPUT_DIR}\n\n"
            f"Temporales: {TEMP_DIR}\n\n"
            "Importa todos los STL juntos en Bambu Studio y elige cargarlos "
            "como un solo objeto con varias partes."
        )
        ttk.Label(help_box, text=output_text, wraplength=260, justify="left").pack(
            fill="both", expand=True, padx=8, pady=8
        )

        colors_box = ttk.LabelFrame(right, text="Colores detectados")
        colors_box.pack(fill="both", expand=True)

        header = ttk.Frame(colors_box)
        header.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(header, text="", width=4).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Hex", width=12).grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Z Offset mm", width=14).grid(row=0, column=2, sticky="w")
        ttk.Label(header, text="Grosor mm", width=12).grid(row=0, column=3, sticky="w")
        ttk.Label(header, text="Exportar", width=10).grid(row=0, column=4, sticky="w")
        ttk.Label(header, text="Info").grid(row=0, column=5, sticky="w")

        self.colors_canvas = tk.Canvas(colors_box, highlightthickness=0)
        self.colors_scrollbar = ttk.Scrollbar(
            colors_box, orient="vertical", command=self.colors_canvas.yview
        )
        self.colors_canvas.configure(yscrollcommand=self.colors_scrollbar.set)
        self.colors_scrollbar.pack(side="right", fill="y")
        self.colors_canvas.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        self.colors_frame = ttk.Frame(self.colors_canvas)
        self.colors_window = self.colors_canvas.create_window(
            (0, 0), window=self.colors_frame, anchor="nw"
        )
        self.colors_frame.bind(
            "<Configure>",
            lambda _event: self.colors_canvas.configure(scrollregion=self.colors_canvas.bbox("all")),
        )
        self.colors_canvas.bind(
            "<Configure>",
            lambda event: self.colors_canvas.itemconfigure(self.colors_window, width=event.width),
        )

        ttk.Label(root, textvariable=self.status_var, anchor="w").pack(fill="x", pady=(8, 0))

    def _show_missing_dependencies(self) -> None:
        messagebox.showerror(
            "Dependencias faltantes",
            "Faltan dependencias de Python:\n"
            + "\n".join(f"- {name}" for name in MISSING_IMPORTS)
            + "\n\nInstala las dependencias desde requirements.txt o con pip.",
        )

    def set_status(self, message: str) -> None:
        self.status_var.set(message)
        self.update_idletasks()

    def _current_preset(self) -> ProductPreset:
        return PRODUCT_PRESETS_BY_NAME.get(self.preset_var.get(), PRODUCT_PRESETS[0])

    def _preset_selected(self, _event: tk.Event | None = None) -> None:
        self.apply_selected_preset()

    def apply_selected_preset(self) -> None:
        preset = self._current_preset()
        self.model_width_var.set(format_number(preset.model_width_mm))
        self.base_thickness_var.set(format_number(preset.base_thickness_mm))
        self.relief_thickness_var.set(format_number(preset.relief_thickness_mm))
        self.relief_z_offset_var.set(format_number(preset.relief_z_offset_mm))
        self.export_background_var.set(preset.export_background)
        self.apply_relief_values_to_rows()
        self._refresh_color_export_defaults()
        self.set_status(f"Preset aplicado: {preset.name}.")

    def apply_relief_values_to_rows(self) -> None:
        for row in self.color_rows:
            row.z_var.set(self.relief_z_offset_var.get())
            row.thickness_var.set(self.relief_thickness_var.get())

    def _refresh_color_export_defaults(self) -> None:
        export_background = self.export_background_var.get()
        for row in self.color_rows:
            row.export_var.set(self._default_export_for_color(row.color, export_background))

    def _default_export_for_color(self, color: DetectedColor, export_background: bool) -> bool:
        if export_background:
            return True
        if not color.export_default:
            return False
        if self._is_probable_antialias_color(color):
            return False
        return color.export_default

    def open_output_folder(self) -> None:
        try:
            ensure_directories()
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(OUTPUT_DIR)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(OUTPUT_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(OUTPUT_DIR)])
        except Exception as exc:
            messagebox.showerror("Error", self._format_exception(exc))

    def select_image(self) -> None:
        try:
            ensure_dependencies()
            path = filedialog.askopenfilename(
                title="Seleccionar imagen",
                initialdir=str(INPUT_DIR),
                filetypes=[
                    ("Imagenes", "*.png *.jpg *.jpeg *.bmp *.webp"),
                    ("PNG", "*.png"),
                    ("JPG", "*.jpg *.jpeg"),
                    ("Todos", "*.*"),
                ],
            )
            if not path:
                return

            self.image_path = Path(path)
            self.image_label_var.set(str(self.image_path))
            self._load_image()
            self.detect_colors_clicked()
        except Exception as exc:
            messagebox.showerror("Error", self._format_exception(exc))

    def select_openscad(self) -> None:
        path = filedialog.askopenfilename(
            title="Seleccionar openscad.exe",
            filetypes=[("OpenSCAD", "openscad.exe"), ("Ejecutables", "*.exe"), ("Todos", "*.*")],
        )
        if path:
            self.openscad_var.set(path)

    def _load_image(self) -> None:
        if self.image_path is None:
            raise RuntimeError("Selecciona una imagen primero.")

        image = Image.open(self.image_path).convert("RGBA")
        self.image_rgba = np.array(image)

        preview = image.copy()
        preview.thumbnail((320, 260), Image.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.preview_label.configure(image=self.preview_photo, text="")

        width, height = image.size
        self.set_status(f"Imagen cargada: {width} x {height} px")

    def detect_colors_clicked(self) -> None:
        try:
            ensure_dependencies()
            if self.image_path is None:
                messagebox.showwarning("Sin imagen", "Selecciona una imagen PNG/JPG primero.")
                return
            if self.image_rgba is None:
                self._load_image()

            max_colors = parse_int(self.max_colors_var.get(), "Colores max.", minimum=1)
            alpha_threshold = parse_int(self.alpha_threshold_var.get(), "Alpha minimo", minimum=0)
            alpha_threshold = min(alpha_threshold, 255)

            self.detected_colors, self.labels_map, self.valid_mask = self._detect_colors(
                self.image_rgba, max_colors, alpha_threshold
            )
            self._render_color_rows()

            if self.detected_colors:
                self.process_button.configure(state="normal")
                self.set_status(f"Detectados {len(self.detected_colors)} colores principales.")
            else:
                self.process_button.configure(state="disabled")
                self.set_status("No se detectaron colores exportables.")
        except Exception as exc:
            messagebox.showerror("Error", self._format_exception(exc))

    def _detect_colors(
        self, image_rgba: np.ndarray, max_colors: int, alpha_threshold: int
    ) -> tuple[list[DetectedColor], np.ndarray, np.ndarray]:
        height, width = image_rgba.shape[:2]
        rgb = image_rgba[:, :, :3].astype(np.uint8)
        alpha = image_rgba[:, :, 3]
        valid_mask = alpha > alpha_threshold

        if not np.any(valid_mask):
            raise RuntimeError("La imagen no tiene pixeles visibles con el alpha configurado.")

        valid_rgb = rgb[valid_mask]
        unique_rgb, inverse = np.unique(valid_rgb.reshape(-1, 3), axis=0, return_inverse=True)

        if len(unique_rgb) <= max_colors:
            labels_valid = inverse.astype(np.int32)
            centers_rgb = [tuple(int(v) for v in row) for row in unique_rgb]
        else:
            labels_valid, centers_rgb = self._kmeans_labels(rgb, valid_mask, max_colors)

        labels_map = np.full((height, width), -1, dtype=np.int32)
        labels_map[valid_mask] = labels_valid

        detected: list[DetectedColor] = []
        total_visible = int(np.count_nonzero(valid_mask))

        for label_id, center_rgb in enumerate(centers_rgb):
            pixel_count = int(np.count_nonzero(labels_valid == label_id))
            if pixel_count == 0:
                continue

            color_mask = labels_map == label_id
            border_counts = (
                np.count_nonzero(color_mask[0, :]),
                np.count_nonzero(color_mask[-1, :]),
                np.count_nonzero(color_mask[:, 0]),
                np.count_nonzero(color_mask[:, -1]),
            )
            border_pixels = sum(border_counts)
            border_sides = sum(1 for count in border_counts if count > 0)
            visible_fraction = pixel_count / max(total_visible, 1)
            border_fraction = border_pixels / max(pixel_count, 1)
            is_white = self._is_near_white(center_rgb)
            is_probable_white_background = is_white and border_sides >= 3
            is_probable_antialias = (
                self._is_neutral_gray(center_rgb)
                and not self._is_near_white(center_rgb)
                and not self._is_near_black(center_rgb)
                and visible_fraction < 0.01
            )

            note_parts = [f"{pixel_count} px"]
            if is_probable_white_background:
                note_parts.append("fondo blanco probable")
            elif is_white:
                note_parts.append("blanco")
            if is_probable_antialias:
                note_parts.append("borde suavizado probable")
            if visible_fraction < 0.002:
                note_parts.append("muy pequeno")

            detected.append(
                DetectedColor(
                    label_id=label_id,
                    rgb=center_rgb,
                    hex_value=rgb_to_hex(center_rgb),
                    pixel_count=pixel_count,
                    visible_fraction=visible_fraction,
                    border_fraction=border_fraction,
                    export_default=not is_probable_white_background,
                    note=", ".join(note_parts),
                )
            )

        detected.sort(key=lambda item: item.pixel_count, reverse=True)
        return detected, labels_map, valid_mask

    def _kmeans_labels(
        self, rgb: np.ndarray, valid_mask: np.ndarray, max_colors: int
    ) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
        valid_lab = lab[valid_mask].astype(np.float32)
        valid_rgb = rgb[valid_mask]
        total_pixels = len(valid_lab)
        k = min(max_colors, total_pixels)

        if total_pixels > KMEANS_MAX_SAMPLE_PIXELS:
            rng = np.random.default_rng(12345)
            sample_idx = rng.choice(total_pixels, size=KMEANS_MAX_SAMPLE_PIXELS, replace=False)
            sample = valid_lab[sample_idx]
        else:
            sample = valid_lab

        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 80, 0.35)
        _compactness, _sample_labels, centers_lab = cv2.kmeans(
            sample,
            k,
            None,
            criteria,
            4,
            cv2.KMEANS_PP_CENTERS,
        )

        labels_valid = self._assign_clusters(valid_lab, centers_lab)
        centers_rgb: list[tuple[int, int, int]] = []

        for label_id in range(k):
            cluster_rgb = valid_rgb[labels_valid == label_id]
            if len(cluster_rgb) == 0:
                centers_rgb.append((0, 0, 0))
                continue
            median = np.median(cluster_rgb, axis=0)
            centers_rgb.append(tuple(int(v) for v in np.clip(np.round(median), 0, 255)))

        return labels_valid, centers_rgb

    def _assign_clusters(self, pixels_lab: np.ndarray, centers_lab: np.ndarray) -> np.ndarray:
        labels = np.empty(len(pixels_lab), dtype=np.int32)
        start = 0
        for chunk in chunked(pixels_lab, 200_000):
            diff = chunk[:, None, :] - centers_lab[None, :, :]
            distances = np.sum(diff * diff, axis=2)
            end = start + len(chunk)
            labels[start:end] = np.argmin(distances, axis=1)
            start = end
        return labels

    def _is_near_white(self, rgb: tuple[int, int, int]) -> bool:
        return rgb[0] >= 245 and rgb[1] >= 245 and rgb[2] >= 245

    def _is_near_black(self, rgb: tuple[int, int, int]) -> bool:
        return rgb[0] <= 24 and rgb[1] <= 24 and rgb[2] <= 24

    def _is_neutral_gray(self, rgb: tuple[int, int, int]) -> bool:
        return max(rgb) - min(rgb) <= 12

    def _is_probable_antialias_color(self, color: DetectedColor) -> bool:
        if color.visible_fraction >= 0.01:
            return False
        if self._is_near_white(color.rgb) or self._is_near_black(color.rgb):
            return False
        return self._is_neutral_gray(color.rgb)

    def _render_color_rows(self) -> None:
        for child in self.colors_frame.winfo_children():
            child.destroy()

        self.color_rows.clear()

        for row_index, color in enumerate(self.detected_colors):
            export_var = tk.BooleanVar(
                value=self._default_export_for_color(
                    color,
                    self.export_background_var.get(),
                )
            )
            z_var = tk.StringVar(value=self.relief_z_offset_var.get())
            thickness_var = tk.StringVar(value=self.relief_thickness_var.get())
            row = ColorRow(color, export_var, z_var, thickness_var)
            self.color_rows.append(row)

            swatch = tk.Label(
                self.colors_frame,
                width=3,
                height=1,
                background=color.hex_value,
                relief="solid",
                borderwidth=1,
            )
            swatch.grid(row=row_index, column=0, sticky="w", padx=(0, 8), pady=4)

            ttk.Label(self.colors_frame, text=color.hex_value, width=12).grid(
                row=row_index, column=1, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Entry(self.colors_frame, textvariable=z_var, width=10).grid(
                row=row_index, column=2, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Entry(self.colors_frame, textvariable=thickness_var, width=10).grid(
                row=row_index, column=3, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Checkbutton(self.colors_frame, variable=export_var).grid(
                row=row_index, column=4, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Label(self.colors_frame, text=color.note).grid(
                row=row_index, column=5, sticky="w", pady=4
            )

    def process_colors_clicked(self) -> None:
        try:
            ensure_dependencies()
            openscad_path = Path(self.openscad_var.get().strip().strip('"'))
            if not openscad_path.exists():
                raise FileNotFoundError(
                    "No se encontro OpenSCAD en:\n"
                    f"{openscad_path}\n\nConfigura la ruta correcta a openscad.exe."
                )

            if self.image_rgba is None or self.labels_map is None:
                raise RuntimeError("Primero carga una imagen y detecta los colores.")

            model_width_mm = parse_float(
                self.model_width_var.get(), "Ancho modelo mm", minimum=0.001
            )
            base_thickness_mm = parse_float(
                self.base_thickness_var.get(), "Grosor base mm", minimum=0.0
            )
            min_area_px = parse_int(self.min_area_var.get(), "Area minima px2", minimum=0)
            morph_size_px = parse_int(self.morph_size_var.get(), "Limpieza px", minimum=0)
            preset = self._current_preset()
            clear_output = self.clear_output_var.get()

            requests: list[ExportRequest] = []
            for row in self.color_rows:
                if not row.export_var.get():
                    continue
                z_offset = parse_float(
                    row.z_var.get(), f"Z Offset {row.color.hex_value}", minimum=0.0
                )
                thickness = parse_float(
                    row.thickness_var.get(), f"Grosor {row.color.hex_value}", minimum=0.001
                )
                if preset.base_mode != BASE_MODE_NONE and base_thickness_mm > 0:
                    z_offset = max(z_offset, base_thickness_mm)
                requests.append(ExportRequest(row.color, z_offset, thickness))

            base_requested = preset.base_mode != BASE_MODE_NONE and base_thickness_mm > 0
            if not requests and not base_requested:
                messagebox.showwarning(
                    "Sin colores",
                    "Marca al menos un color para exportar o usa un preset con base.",
                )
                return

            image_rgba = self.image_rgba.copy()
            labels_map = self.labels_map.copy()
            detected_colors = list(self.detected_colors)

            self.process_button.configure(state="disabled")
            self.set_status("Procesando colores con OpenSCAD...")

            worker = threading.Thread(
                target=self._process_worker,
                args=(
                    requests,
                    image_rgba,
                    labels_map,
                    openscad_path,
                    model_width_mm,
                    base_thickness_mm,
                    preset,
                    detected_colors,
                    min_area_px,
                    morph_size_px,
                    clear_output,
                ),
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            messagebox.showerror("Error", self._format_exception(exc))

    def _process_worker(
        self,
        requests: list[ExportRequest],
        image_rgba: np.ndarray,
        labels_map: np.ndarray,
        openscad_path: Path,
        model_width_mm: float,
        base_thickness_mm: float,
        preset: ProductPreset,
        detected_colors: list[DetectedColor],
        min_area_px: int,
        morph_size_px: int,
        clear_output: bool,
    ) -> None:
        try:
            generated = self._export_selected_colors(
                requests,
                image_rgba,
                labels_map,
                openscad_path,
                model_width_mm,
                base_thickness_mm,
                preset,
                detected_colors,
                min_area_px,
                morph_size_px,
                clear_output,
            )
            self.after(0, lambda: self._process_success(generated))
        except Exception as exc:
            error = self._format_exception(exc)
            self.after(0, lambda: self._process_error(error))

    def _process_success(self, generated: list[Path]) -> None:
        self.process_button.configure(state="normal")
        self.set_status(f"Listo. Generados {len(generated)} archivos en {OUTPUT_DIR}.")
        names = "\n".join(path.name for path in generated)
        messagebox.showinfo(
            "Proceso finalizado",
            f"Archivos generados: {len(generated)}\n\n"
            f"Ruta output:\n{OUTPUT_DIR}\n\n"
            f"Archivos nuevos:\n{names}\n\n"
            "Para ver el llavero como una sola pieza con relieve positivo, "
            "abre producto_..._completo.stl.",
        )

    def _process_error(self, error: str) -> None:
        self.process_button.configure(state="normal")
        self.set_status("Error durante el procesamiento.")
        messagebox.showerror("Error", error)

    def _export_selected_colors(
        self,
        requests: list[ExportRequest],
        image_rgba: np.ndarray,
        labels_map: np.ndarray,
        openscad_path: Path,
        model_width_mm: float,
        base_thickness_mm: float,
        preset: ProductPreset,
        detected_colors: list[DetectedColor],
        min_area_px: int,
        morph_size_px: int,
        clear_output: bool,
    ) -> list[Path]:
        height_px, width_px = image_rgba.shape[:2]
        if width_px < 1 or height_px < 1:
            raise RuntimeError("La imagen no tiene dimensiones validas.")

        pixel_to_mm = model_width_mm / float(width_px)
        generated: list[Path] = []
        base_path: Path | None = None
        color_part_paths: list[Path] = []
        if clear_output:
            self._clear_previous_generated_files()

        if preset.base_mode != BASE_MODE_NONE and base_thickness_mm > 0:
            base_path = self._export_product_base(
                labels_map,
                openscad_path,
                preset,
                detected_colors,
                base_thickness_mm,
                pixel_to_mm,
                width_px,
                height_px,
                min_area_px,
                morph_size_px,
            )
            generated.append(base_path)

        antialias_assignments = self._build_antialias_assignments(requests, detected_colors)
        color_shape_groups: list[tuple[ExportRequest, list[FilledShape]]] = []

        for export_index, request in enumerate(requests, start=1):
            hex_name = safe_hex_for_filename(request.color.hex_value)
            base_name = f"color_{export_index:02d}_{hex_name}"

            mask_path = TEMP_DIR / f"{base_name}_mask.png"
            svg_path = TEMP_DIR / f"{base_name}.svg"
            scad_path = TEMP_DIR / f"{base_name}.scad"
            stl_path = OUTPUT_DIR / f"{base_name}.stl"

            mask = self._build_clean_mask(
                labels_map,
                request.color.label_id,
                min_area_px,
                morph_size_px,
                extra_label_ids=antialias_assignments.get(request.color.label_id, []),
            )
            if np.count_nonzero(mask) == 0:
                raise RuntimeError(
                    f"No quedaron pixeles exportables para {request.color.hex_value}. "
                    "Baja Area minima px2 o Limpieza px."
                )

            Image.fromarray(mask).save(mask_path)

            shapes = self._extract_filled_shapes(mask, min_area_px)
            if not shapes:
                raise RuntimeError(
                    f"No se pudieron generar areas cerradas para {request.color.hex_value}."
                )
            color_shape_groups.append((request, shapes))

            self._write_svg_from_shapes(
                shapes,
                request.color.hex_value,
                svg_path,
                width_px,
                height_px,
            )
            self._write_scad(
                scad_path,
                svg_path,
                shapes,
                request.z_offset,
                request.thickness,
                pixel_to_mm,
            )
            self._run_openscad(openscad_path, scad_path, stl_path)
            generated.append(stl_path)
            color_part_paths.append(stl_path)

        if base_path is not None and color_part_paths:
            combined_path = self._export_imported_product(
                base_path,
                color_part_paths,
                openscad_path,
                preset,
                base_thickness_mm,
            )
            generated.append(combined_path)

        self._write_output_manifest(generated)
        return generated

    def _build_antialias_assignments(
        self,
        requests: list[ExportRequest],
        detected_colors: list[DetectedColor],
    ) -> dict[int, list[int]]:
        selected_colors = [request.color for request in requests]
        selected_ids = {color.label_id for color in selected_colors}
        assignments: dict[int, list[int]] = {color.label_id: [] for color in selected_colors}

        if not selected_colors:
            return assignments

        for color in detected_colors:
            if color.label_id in selected_ids:
                continue
            if not self._is_probable_antialias_color(color):
                continue

            closest = min(
                selected_colors,
                key=lambda selected: self._rgb_distance_squared(color.rgb, selected.rgb),
            )
            assignments.setdefault(closest.label_id, []).append(color.label_id)

        return assignments

    def _rgb_distance_squared(
        self,
        first: tuple[int, int, int],
        second: tuple[int, int, int],
    ) -> int:
        return sum((first[index] - second[index]) ** 2 for index in range(3))

    def _export_imported_product(
        self,
        base_path: Path,
        color_part_paths: list[Path],
        openscad_path: Path,
        preset: ProductPreset,
        base_thickness_mm: float,
    ) -> Path:
        preset_slug = self._safe_name_for_filename(preset.name)
        scad_path = TEMP_DIR / f"producto_{preset_slug}_completo.scad"
        stl_path = OUTPUT_DIR / f"producto_{preset_slug}_completo.stl"

        self._write_import_union_scad(scad_path, base_path, color_part_paths, base_thickness_mm)
        self._run_openscad(openscad_path, scad_path, stl_path)
        return stl_path

    def _write_import_union_scad(
        self,
        scad_path: Path,
        base_path: Path,
        color_part_paths: list[Path],
        base_thickness_mm: float,
    ) -> None:
        base_import_path = base_path.resolve().as_posix().replace('"', '\\"')
        imports = [f'    import("{base_import_path}", convexity = 10);\n']
        relief_visual_scale = 2.6

        for path in color_part_paths:
            import_path = path.resolve().as_posix().replace('"', '\\"')
            imports.append(
                f"    translate([0, 0, {base_thickness_mm:.6f}])\n"
                f"        scale([1, 1, {relief_visual_scale:.6f}])\n"
                f"            translate([0, 0, {-base_thickness_mm:.6f}])\n"
                f'                import("{import_path}", convexity = 10);\n'
            )

        scad = (
            "// Generated by image-to-svg-stl-converter.\n"
            "// Combined STL made from already generated positive STL parts.\n"
            "// The logo is imported as-is, without mask inversion.\n\n"
            "render(convexity = 10)\n"
            "union() {\n"
            + "".join(imports)
            + "}\n"
        )
        scad_path.write_text(scad, encoding="utf-8")

    def _export_combined_product(
        self,
        color_shape_groups: list[tuple[ExportRequest, list[FilledShape]]],
        labels_map: np.ndarray,
        openscad_path: Path,
        preset: ProductPreset,
        detected_colors: list[DetectedColor],
        base_thickness_mm: float,
        pixel_to_mm: float,
        width_px: int,
        height_px: int,
        min_area_px: int,
        morph_size_px: int,
    ) -> Path:
        preset_slug = self._safe_name_for_filename(preset.name)
        scad_path = TEMP_DIR / f"producto_{preset_slug}_completo.scad"
        stl_path = OUTPUT_DIR / f"producto_{preset_slug}_completo.stl"

        base_reference_mask = self._build_base_reference_mask(labels_map, detected_colors)
        base_shapes, _mask, cutouts = self._build_product_base_shapes(
            base_reference_mask,
            preset,
            pixel_to_mm,
            width_px,
            height_px,
            min_area_px,
            morph_size_px,
        )
        self._write_combined_scad(
            scad_path,
            base_shapes,
            cutouts,
            color_shape_groups,
            base_thickness_mm,
            pixel_to_mm,
        )
        self._run_openscad(openscad_path, scad_path, stl_path)
        return stl_path

    def _write_combined_scad(
        self,
        scad_path: Path,
        base_shapes: list[FilledShape],
        base_cutouts: list[list[Point]],
        color_shape_groups: list[tuple[ExportRequest, list[FilledShape]]],
        base_thickness_mm: float,
        pixel_to_mm: float,
    ) -> None:
        base_body = self._scad_2d_body(base_shapes, base_cutouts, "        ")
        color_modules: list[str] = []
        color_instances: list[str] = []
        cutout_instances: list[str] = []

        for index, (request, shapes) in enumerate(color_shape_groups, start=1):
            module_name = f"color_shape_{index}"
            body = self._scad_2d_body(shapes, [], "        ")
            total_height = max(base_thickness_mm + request.thickness, request.z_offset + request.thickness)
            color_modules.append(
                f"module {module_name}() {{\n"
                f"{body}"
                "}\n"
            )
            cutout_instances.append(f"            {module_name}();\n")
            color_instances.append(
                f"    linear_extrude(height = {total_height:.6f}, convexity = 10)\n"
                "        scale([pixel_to_mm, pixel_to_mm, 1])\n"
                f"            {module_name}();\n"
            )

        scad = (
            "// Generated by image-to-svg-stl-converter.\n"
            "// Single-piece preview/export with base and raised logo together.\n\n"
            f"pixel_to_mm = {pixel_to_mm:.10f};\n\n"
            "module base_shape() {\n"
            f"{base_body}"
            "}\n\n"
            + "\n".join(color_modules)
            + "\n"
            "render(convexity = 10)\n"
            "union() {\n"
            f"    linear_extrude(height = {base_thickness_mm:.6f}, convexity = 10)\n"
            "        scale([pixel_to_mm, pixel_to_mm, 1])\n"
            "            difference() {\n"
            "                base_shape();\n"
            "                union() {\n"
            + "".join(cutout_instances)
            + "                }\n"
            "            }\n"
            + "".join(color_instances)
            + "}\n"
        )
        scad_path.write_text(scad, encoding="utf-8")

    def _export_product_base(
        self,
        labels_map: np.ndarray,
        openscad_path: Path,
        preset: ProductPreset,
        detected_colors: list[DetectedColor],
        base_thickness_mm: float,
        pixel_to_mm: float,
        width_px: int,
        height_px: int,
        min_area_px: int,
        morph_size_px: int,
    ) -> Path:
        base_color = self._select_base_color(detected_colors)
        base_hex = base_color.hex_value if base_color is not None else "#FFFFFF"
        preset_slug = self._safe_name_for_filename(preset.name)
        hex_name = safe_hex_for_filename(base_hex)
        base_name = f"base_{preset_slug}_{hex_name}"

        mask_path = TEMP_DIR / f"{base_name}_mask.png"
        svg_path = TEMP_DIR / f"{base_name}.svg"
        scad_path = TEMP_DIR / f"{base_name}.scad"
        stl_path = OUTPUT_DIR / f"{base_name}.stl"

        base_reference_mask = self._build_base_reference_mask(labels_map, detected_colors)
        shapes, mask, cutouts = self._build_product_base_shapes(
            base_reference_mask,
            preset,
            pixel_to_mm,
            width_px,
            height_px,
            min_area_px,
            morph_size_px,
        )
        if not shapes:
            raise RuntimeError(f"No se pudo generar la base para el preset {preset.name}.")

        Image.fromarray(mask).save(mask_path)

        self._write_svg_from_shapes(
            shapes,
            base_hex,
            svg_path,
            width_px,
            height_px,
            extra_cutouts=cutouts,
        )
        self._write_scad(
            scad_path,
            svg_path,
            shapes,
            0.0,
            base_thickness_mm,
            pixel_to_mm,
            cutout_shapes=cutouts,
        )
        self._run_openscad(openscad_path, scad_path, stl_path)
        return stl_path

    def _select_base_color(self, detected_colors: list[DetectedColor]) -> DetectedColor | None:
        if not detected_colors:
            return None

        background_candidates = [color for color in detected_colors if not color.export_default]
        if background_candidates:
            return max(background_candidates, key=lambda color: color.pixel_count)

        return max(detected_colors, key=lambda color: color.pixel_count)

    def _safe_name_for_filename(self, name: str) -> str:
        replacements = {
            "á": "a",
            "é": "e",
            "í": "i",
            "ó": "o",
            "ú": "u",
            "Á": "A",
            "É": "E",
            "Í": "I",
            "Ó": "O",
            "Ú": "U",
            "ñ": "n",
            "Ñ": "N",
        }
        cleaned = "".join(replacements.get(char, char) for char in name)
        cleaned = "".join(char if char.isalnum() else "_" for char in cleaned)
        return "_".join(part for part in cleaned.lower().split("_") if part)

    def _build_base_reference_mask(
        self,
        labels_map: np.ndarray,
        detected_colors: list[DetectedColor],
    ) -> np.ndarray:
        foreground_labels = [color.label_id for color in detected_colors if color.export_default]

        if foreground_labels:
            mask = np.where(np.isin(labels_map, foreground_labels), 255, 0).astype(np.uint8)
            if np.count_nonzero(mask) > 0:
                return mask

        mask = np.where(labels_map >= 0, 255, 0).astype(np.uint8)
        if np.count_nonzero(mask) == 0:
            raise RuntimeError("La imagen no tiene pixeles visibles para construir la base.")
        return mask

    def _build_product_base_shapes(
        self,
        reference_mask: np.ndarray,
        preset: ProductPreset,
        pixel_to_mm: float,
        width_px: int,
        height_px: int,
        min_area_px: int,
        morph_size_px: int,
    ) -> tuple[list[FilledShape], np.ndarray, list[list[Point]]]:
        if np.count_nonzero(reference_mask) == 0:
            raise RuntimeError("No hay pixeles de logo para construir la base.")

        if preset.base_mode == BASE_MODE_RECTANGLE:
            shape = self._rectangle_base_shape(reference_mask, width_px, height_px)
            mask = np.zeros((height_px, width_px), dtype=np.uint8)
            polygon = np.array(shape[0], dtype=np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(mask, [polygon], 255)
            return [shape], mask, []

        if preset.base_mode == BASE_MODE_KEYRING:
            return self._build_keyring_base_shapes(
                reference_mask,
                preset,
                pixel_to_mm,
                width_px,
                height_px,
                min_area_px,
            )

        padding_px = self._base_padding_px(width_px, height_px)
        mask = self._solid_contour_mask(reference_mask, width_px, height_px, padding_px)
        if morph_size_px > 1:
            kernel_size = max(2, morph_size_px)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

        if min_area_px > 0:
            mask = self._remove_small_components(mask, min_area_px)

        shapes = self._extract_outer_filled_shapes(mask, min_area_px)
        return shapes, mask, []

    def _build_keyring_base_shapes(
        self,
        reference_mask: np.ndarray,
        preset: ProductPreset,
        pixel_to_mm: float,
        width_px: int,
        height_px: int,
        min_area_px: int,
    ) -> tuple[list[FilledShape], np.ndarray, list[list[Point]]]:
        padding_px = self._base_padding_px(width_px, height_px)
        mask = self._solid_contour_mask(reference_mask, width_px, height_px, padding_px)
        shapes = self._extract_outer_filled_shapes(mask, min_area_px)
        if not shapes:
            raise RuntimeError("No se pudo crear la base del llavero desde el logo.")

        left, top, right, _bottom = self._visible_bounds(mask)
        hole_radius_px = max(2.0, (preset.hole_diameter_mm * 0.5) / pixel_to_mm)
        margin_px = max(2.0, preset.hole_margin_mm / pixel_to_mm)
        tab_radius_px = hole_radius_px + margin_px
        center_x = (left + right) * 0.5
        center_y = top - (tab_radius_px * 0.35)

        tab_points = self._circle_points(center_x, center_y, tab_radius_px)
        hole_points = self._circle_points(center_x, center_y, hole_radius_px)
        shapes.append((tab_points, []))

        cv2.circle(
            mask,
            (int(round(center_x)), int(round(center_y))),
            int(round(tab_radius_px)),
            255,
            thickness=-1,
        )
        cv2.circle(
            mask,
            (int(round(center_x)), int(round(center_y))),
            int(round(hole_radius_px)),
            0,
            thickness=-1,
        )

        return shapes, mask, [hole_points]

    def _base_padding_px(self, width_px: int, height_px: int) -> int:
        return max(2, int(round(min(width_px, height_px) * 0.035)))

    def _solid_contour_mask(
        self,
        reference_mask: np.ndarray,
        width_px: int,
        height_px: int,
        padding_px: int,
    ) -> np.ndarray:
        y_values, x_values = np.nonzero(reference_mask)
        if len(x_values) == 0 or len(y_values) == 0:
            raise RuntimeError("No hay pixeles visibles para calcular el contorno general.")

        points = np.column_stack((x_values, y_values)).astype(np.int32).reshape((-1, 1, 2))
        hull = cv2.convexHull(points)
        mask = np.zeros((height_px, width_px), dtype=np.uint8)
        cv2.fillPoly(mask, [hull], 255)

        if padding_px > 0:
            kernel_size = padding_px * 2 + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
            mask = cv2.dilate(mask, kernel, iterations=1)

        return mask

    def _rectangle_base_shape(
        self,
        visible_mask: np.ndarray,
        width_px: int,
        height_px: int,
    ) -> FilledShape:
        left, top, right, bottom = self._visible_bounds(visible_mask)
        padding_px = max(2, int(round(min(width_px, height_px) * 0.03)))
        left = max(0, left - padding_px)
        top = max(0, top - padding_px)
        right = min(width_px, right + padding_px)
        bottom = min(height_px, bottom + padding_px)

        points = [(left, top), (right, top), (right, bottom), (left, bottom)]
        return points, []

    def _visible_bounds(self, mask: np.ndarray) -> tuple[int, int, int, int]:
        y_values, x_values = np.nonzero(mask)
        if len(x_values) == 0 or len(y_values) == 0:
            raise RuntimeError("No hay pixeles visibles para calcular el contorno general.")

        left = int(x_values.min())
        top = int(y_values.min())
        right = int(x_values.max()) + 1
        bottom = int(y_values.max()) + 1
        return left, top, right, bottom

    def _extract_outer_filled_shapes(self, mask: np.ndarray, min_area_px: int) -> list[FilledShape]:
        vector_mask, coordinate_scale = self._prepare_mask_for_vector_contours(mask)
        contours, _hierarchy = cv2.findContours(
            vector_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_NONE,
        )
        min_contour_area = max(1.0, float(min_area_px))
        shapes: list[FilledShape] = []

        for contour in contours:
            area = abs(cv2.contourArea(contour)) / (coordinate_scale * coordinate_scale)
            if area < min_contour_area:
                continue

            outer_points = self._contour_to_points(contour, coordinate_scale)
            if len(outer_points) >= 3:
                shapes.append((outer_points, []))

        shapes.sort(
            key=lambda shape: abs(
                cv2.contourArea(np.array(shape[0], dtype=np.int32).reshape((-1, 1, 2)))
            ),
            reverse=True,
        )
        return shapes

    def _keyring_hole_points(
        self,
        labels_map: np.ndarray,
        preset: ProductPreset,
        pixel_to_mm: float,
        width_px: int,
        height_px: int,
    ) -> list[Point]:
        visible_mask = np.where(labels_map >= 0, 255, 0).astype(np.uint8)
        left, top, right, bottom = self._visible_bounds(visible_mask)

        radius_px = (preset.hole_diameter_mm * 0.5) / pixel_to_mm
        max_radius_px = max(2.0, (min(width_px, height_px) - 4.0) * 0.5)
        radius_px = max(2.0, min(radius_px, max_radius_px))
        margin_px = max(2.0, preset.hole_margin_mm / pixel_to_mm)

        center_x = (left + right) * 0.5
        center_y = top + margin_px + radius_px
        if center_y + radius_px > bottom - margin_px:
            center_y = (top + bottom) * 0.5

        center_x = min(max(center_x, radius_px + 1.0), width_px - radius_px - 1.0)
        center_y = min(max(center_y, radius_px + 1.0), height_px - radius_px - 1.0)
        return self._circle_points(center_x, center_y, radius_px)

    def _circle_points(self, center_x: float, center_y: float, radius: float) -> list[Point]:
        points: list[Point] = []
        segments = 72
        for index in range(segments):
            angle = (2.0 * math.pi * index) / segments
            x_value = int(round(center_x + math.cos(angle) * radius))
            y_value = int(round(center_y + math.sin(angle) * radius))
            point = (x_value, y_value)
            if not points or point != points[-1]:
                points.append(point)

        if len(points) > 1 and points[0] == points[-1]:
            points.pop()
        return points

    def _clear_previous_generated_files(self) -> None:
        patterns = (
            (OUTPUT_DIR, "color_*.stl"),
            (OUTPUT_DIR, "base_*.stl"),
            (TEMP_DIR, "color_*_mask.png"),
            (TEMP_DIR, "color_*.svg"),
            (TEMP_DIR, "color_*.scad"),
            (TEMP_DIR, "base_*_mask.png"),
            (TEMP_DIR, "base_*.svg"),
            (TEMP_DIR, "base_*.scad"),
            (OUTPUT_DIR, "producto_*_completo.stl"),
            (TEMP_DIR, "producto_*_completo.scad"),
            (OUTPUT_DIR, "producto_*_bambu.3mf"),
        )
        for directory, pattern in patterns:
            for path in directory.glob(pattern):
                if path.is_file():
                    path.unlink()

        if OUTPUT_MANIFEST.exists():
            OUTPUT_MANIFEST.unlink()

    def _write_output_manifest(self, generated: list[Path]) -> None:
        lines = [
            "Archivos generados por image-to-svg-stl-converter.",
            "Para ver el producto armado, abrir producto_..._completo.stl si existe.",
            "",
        ]
        lines.extend(str(path.resolve()) for path in generated)
        OUTPUT_MANIFEST.write_text("\n".join(lines), encoding="utf-8")

    def _build_clean_mask(
        self,
        labels_map: np.ndarray,
        label_id: int,
        min_area_px: int,
        morph_size_px: int,
        extra_label_ids: list[int] | None = None,
    ) -> np.ndarray:
        label_ids = [label_id]
        if extra_label_ids:
            label_ids.extend(extra_label_ids)
        mask = np.where(np.isin(labels_map, label_ids), 255, 0).astype(np.uint8)

        if morph_size_px > 1:
            kernel_size = max(2, morph_size_px)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        else:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

        if min_area_px > 0:
            mask = self._remove_small_components(mask, min_area_px)

        return mask

    def _remove_small_components(self, mask: np.ndarray, min_area_px: int) -> np.ndarray:
        count, component_map, stats, _centroids = cv2.connectedComponentsWithStats(
            mask, connectivity=8
        )
        clean = np.zeros_like(mask)

        for component_id in range(1, count):
            area = int(stats[component_id, cv2.CC_STAT_AREA])
            if area >= min_area_px:
                clean[component_map == component_id] = 255

        return clean

    def _extract_filled_shapes(self, mask: np.ndarray, min_area_px: int) -> list[FilledShape]:
        vector_mask, coordinate_scale = self._prepare_mask_for_vector_contours(mask)
        contours, hierarchy = cv2.findContours(
            vector_mask,
            cv2.RETR_CCOMP,
            cv2.CHAIN_APPROX_NONE,
        )
        if hierarchy is None:
            return []

        hierarchy_rows = hierarchy[0]
        min_contour_area = max(1.0, float(min_area_px))
        shapes: list[FilledShape] = []

        for contour_index, contour in enumerate(contours):
            parent_index = int(hierarchy_rows[contour_index][3])
            if parent_index != -1:
                continue

            area = abs(cv2.contourArea(contour)) / (coordinate_scale * coordinate_scale)
            if area < min_contour_area:
                continue

            outer_points = self._contour_to_points(contour, coordinate_scale)
            if len(outer_points) < 3:
                continue

            holes: list[list[Point]] = []
            child_index = int(hierarchy_rows[contour_index][2])
            while child_index != -1:
                child = contours[child_index]
                child_area = abs(cv2.contourArea(child)) / (
                    coordinate_scale * coordinate_scale
                )
                if child_area >= 1.0:
                    hole_points = self._contour_to_points(child, coordinate_scale)
                    if len(hole_points) >= 3:
                        holes.append(hole_points)
                child_index = int(hierarchy_rows[child_index][0])

            shapes.append((outer_points, holes))

        return shapes

    def _prepare_mask_for_vector_contours(self, mask: np.ndarray) -> tuple[np.ndarray, float]:
        height, width = mask.shape[:2]
        pixel_count = max(1, height * width)
        max_scale = int(math.sqrt(VECTOR_UPSCALE_MAX_PIXELS / pixel_count))
        scale = max(1, min(VECTOR_UPSCALE_MAX, max_scale))

        if scale <= 1:
            return mask, 1.0

        upscaled = cv2.resize(
            mask,
            (width * scale, height * scale),
            interpolation=cv2.INTER_CUBIC,
        )
        upscaled = cv2.GaussianBlur(
            upscaled,
            (0, 0),
            sigmaX=max(0.45, scale * 0.28),
            sigmaY=max(0.45, scale * 0.28),
        )
        _threshold, smooth = cv2.threshold(upscaled, 127, 255, cv2.THRESH_BINARY)
        return smooth.astype(np.uint8), float(scale)

    def _contour_to_points(self, contour: np.ndarray, coordinate_scale: float = 1.0) -> list[Point]:
        epsilon = max(
            0.45 * coordinate_scale,
            min(1.6 * coordinate_scale, 0.0008 * cv2.arcLength(contour, True)),
        )
        approx = cv2.approxPolyDP(contour, epsilon, True)
        points: list[Point] = []
        previous: Point | None = None

        for x_value, y_value in approx.reshape(-1, 2):
            point = (float(x_value) / coordinate_scale, float(y_value) / coordinate_scale)
            if point != previous:
                points.append(point)
                previous = point

        if len(points) > 1 and points[0] == points[-1]:
            points.pop()

        return points

    def _write_svg_from_shapes(
        self,
        shapes: list[FilledShape],
        color_hex: str,
        svg_path: Path,
        canvas_width_px: int,
        canvas_height_px: int,
        extra_cutouts: list[list[Point]] | None = None,
    ) -> None:
        path_parts: list[str] = []
        for outer_points, holes in shapes:
            path_parts.append(self._points_to_svg_path(outer_points))
            for hole_points in holes:
                path_parts.append(self._points_to_svg_path(hole_points))
        for cutout_points in extra_cutouts or []:
            path_parts.append(self._points_to_svg_path(cutout_points))

        if not path_parts:
            raise RuntimeError(f"No hay poligonos rellenos para escribir en {svg_path}.")

        path_data = " ".join(path_parts)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{canvas_width_px}" height="{canvas_height_px}" '
            f'viewBox="0 0 {canvas_width_px} {canvas_height_px}">\n'
            f'  <path d="{path_data}" fill="{color_hex}" fill-rule="evenodd"/>\n'
            "</svg>\n"
        )
        svg_path.write_text(svg, encoding="utf-8")

    def _points_to_svg_path(self, points: list[Point]) -> str:
        first_x, first_y = points[0]
        commands = [f"M {format_coord(first_x)} {format_coord(first_y)}"]
        for x_value, y_value in points[1:]:
            commands.append(f"L {format_coord(x_value)} {format_coord(y_value)}")
        commands.append("Z")
        return " ".join(commands)

    def _points_to_scad_list(self, points: list[Point]) -> str:
        return (
            "["
            + ", ".join(
                f"[{format_coord(x_value)}, {format_coord(y_value)}]"
                for x_value, y_value in points
            )
            + "]"
        )

    def _shape_to_scad_2d(self, outer_points: list[Point], holes: list[list[Point]], indent: str) -> str:
        outer_polygon = (
            f"{indent}polygon(points = {self._points_to_scad_list(outer_points)}, "
            "convexity = 10);"
        )

        if not holes:
            return outer_polygon + "\n"

        lines = [f"{indent}difference() {{", outer_polygon]
        for hole_points in holes:
            lines.append(
                f"{indent}    polygon(points = {self._points_to_scad_list(hole_points)}, "
                "convexity = 10);"
            )
        lines.append(f"{indent}}}")
        return "\n".join(lines) + "\n"

    def _scad_2d_body(
        self,
        shapes: list[FilledShape],
        cutout_shapes: list[list[Point]],
        indent: str,
    ) -> str:
        if cutout_shapes:
            shape_body = "".join(
                self._shape_to_scad_2d(outer_points, holes, indent + "        ")
                for outer_points, holes in shapes
            )
            cutout_body = "".join(
                f"{indent}        polygon(points = {self._points_to_scad_list(points)}, "
                "convexity = 10);\n"
                for points in cutout_shapes
            )
            return (
                f"{indent}difference() {{\n"
                f"{indent}    union() {{\n"
                f"{shape_body}"
                f"{indent}    }}\n"
                f"{indent}    union() {{\n"
                f"{cutout_body}"
                f"{indent}    }}\n"
                f"{indent}}}\n"
            )

        shape_body = "".join(
            self._shape_to_scad_2d(outer_points, holes, indent + "    ")
            for outer_points, holes in shapes
        )
        return f"{indent}union() {{\n" f"{shape_body}" f"{indent}}}\n"

    def _write_scad(
        self,
        scad_path: Path,
        svg_path: Path,
        shapes: list[FilledShape],
        z_offset: float,
        thickness: float,
        pixel_to_mm: float,
        cutout_shapes: list[list[Point]] | None = None,
    ) -> None:
        svg_for_comment = svg_path.resolve().as_posix().replace('"', '\\"')
        shape_indent = "            " if cutout_shapes else "        "
        shape_body = "".join(
            self._shape_to_scad_2d(outer_points, holes, shape_indent)
            for outer_points, holes in shapes
        )
        if cutout_shapes:
            cutout_body = "".join(
                f"            polygon(points = {self._points_to_scad_list(points)}, "
                "convexity = 10);\n"
                for points in cutout_shapes
            )
            module_body = (
                "    difference() {\n"
                "        union() {\n"
                f"{shape_body}"
                "        }\n"
                "        union() {\n"
                f"{cutout_body}"
                "        }\n"
                "    }\n"
            )
        else:
            module_body = "    union() {\n" f"{shape_body}" "    }\n"
        scad = (
            "// Generated by image-to-svg-stl-converter.\n"
            "// Each STL is one material/color part.\n"
            "// The SVG uses the original full image canvas in pixel coordinates.\n"
            "// The STL is generated from filled OpenSCAD polygons, not outlines.\n"
            "// All parts share the same 0,0 origin and the same scale.\n"
            f'// Review SVG: "{svg_for_comment}"\n\n'
            f"pixel_to_mm = {pixel_to_mm:.10f};\n\n"
            "module filled_color_shape() {\n"
            f"{module_body}"
            "}\n\n"
            f"translate([0, 0, {z_offset:.6f}])\n"
            f"linear_extrude(height = {thickness:.6f}, convexity = 10)\n"
            "    scale([pixel_to_mm, pixel_to_mm, 1])\n"
            "        filled_color_shape();\n"
        )
        scad_path.write_text(scad, encoding="utf-8")

    def _run_openscad(self, openscad_path: Path, scad_path: Path, stl_path: Path) -> None:
        command = [str(openscad_path), "-o", str(stl_path), str(scad_path)]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "OpenSCAD fallo al generar el STL.\n\n"
                f"SCAD:\n{scad_path}\n\n"
                f"STDOUT:\n{result.stdout.strip()}\n\n"
                f"STDERR:\n{result.stderr.strip()}"
            )

        if not stl_path.exists():
            raise RuntimeError(
                "OpenSCAD termino sin error, pero no se encontro el STL esperado:\n"
                f"{stl_path}"
            )

    def _format_exception(self, exc: Exception | str) -> str:
        if isinstance(exc, str):
            return exc
        detail = traceback.format_exc()
        if detail.strip() == "NoneType: None":
            return str(exc)
        return f"{exc}\n\nDetalle:\n{detail}"


def show_startup_error(error_text: str) -> None:
    try:
        STARTUP_ERROR_LOG.write_text(error_text, encoding="utf-8")
    except Exception:
        pass

    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"No se pudo abrir la aplicacion.\n\n{error_text}\n\n"
            f"Detalle guardado en:\n{STARTUP_ERROR_LOG}",
            "Error al iniciar",
            0x10,
        )
    except Exception:
        sys.stderr.write(error_text + "\n")


def main() -> None:
    try:
        app = ImageToSvgStlConverter()
        app.mainloop()
    except Exception:
        show_startup_error(traceback.format_exc())


if __name__ == "__main__":
    main()
