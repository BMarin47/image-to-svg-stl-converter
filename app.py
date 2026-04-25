from __future__ import annotations

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

        self.openscad_var = tk.StringVar(value=DEFAULT_OPENSCAD_PATH)
        self.max_colors_var = tk.StringVar(value=str(DEFAULT_COLOR_COUNT))
        self.model_width_var = tk.StringVar(value=str(DEFAULT_MODEL_WIDTH_MM))
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
            border_pixels = (
                np.count_nonzero(color_mask[0, :])
                + np.count_nonzero(color_mask[-1, :])
                + np.count_nonzero(color_mask[:, 0])
                + np.count_nonzero(color_mask[:, -1])
            )
            border_fraction = border_pixels / max(pixel_count, 1)
            is_white = self._is_near_white(center_rgb)
            is_probable_white_background = is_white and border_fraction > 0.02

            note_parts = [f"{pixel_count} px"]
            if is_probable_white_background:
                note_parts.append("fondo blanco probable")
            elif is_white:
                note_parts.append("blanco")
            if pixel_count / total_visible < 0.002:
                note_parts.append("muy pequeno")

            detected.append(
                DetectedColor(
                    label_id=label_id,
                    rgb=center_rgb,
                    hex_value=rgb_to_hex(center_rgb),
                    pixel_count=pixel_count,
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

    def _render_color_rows(self) -> None:
        for child in self.colors_frame.winfo_children():
            child.destroy()

        self.color_rows.clear()

        for row_index, color in enumerate(self.detected_colors):
            export_var = tk.BooleanVar(value=color.export_default)
            z_var = tk.StringVar(value="0.0")
            thickness_var = tk.StringVar(value="1.0")
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
            min_area_px = parse_int(self.min_area_var.get(), "Area minima px2", minimum=0)
            morph_size_px = parse_int(self.morph_size_var.get(), "Limpieza px", minimum=0)

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
                requests.append(ExportRequest(row.color, z_offset, thickness))

            if not requests:
                messagebox.showwarning("Sin colores", "Marca al menos un color para exportar.")
                return

            image_rgba = self.image_rgba.copy()
            labels_map = self.labels_map.copy()

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
                    min_area_px,
                    morph_size_px,
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
        min_area_px: int,
        morph_size_px: int,
    ) -> None:
        try:
            generated = self._export_selected_colors(
                requests,
                image_rgba,
                labels_map,
                openscad_path,
                model_width_mm,
                min_area_px,
                morph_size_px,
            )
            self.after(0, lambda: self._process_success(generated))
        except Exception as exc:
            error = self._format_exception(exc)
            self.after(0, lambda: self._process_error(error))

    def _process_success(self, generated: list[Path]) -> None:
        self.process_button.configure(state="normal")
        self.set_status(f"Listo. Generados {len(generated)} STL en {OUTPUT_DIR}.")
        names = "\n".join(path.name for path in generated)
        messagebox.showinfo(
            "Proceso finalizado",
            f"STL generados: {len(generated)}\n\n"
            f"Ruta output:\n{OUTPUT_DIR}\n\n"
            f"Archivos nuevos:\n{names}\n\n"
            "Importa estos archivos juntos en Bambu Studio y acepta cargarlos "
            "como un solo objeto con varias partes.",
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
        min_area_px: int,
        morph_size_px: int,
    ) -> list[Path]:
        height_px, width_px = image_rgba.shape[:2]
        if width_px < 1 or height_px < 1:
            raise RuntimeError("La imagen no tiene dimensiones validas.")

        pixel_to_mm = model_width_mm / float(width_px)
        generated: list[Path] = []
        self._clear_previous_generated_files()

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

        self._write_output_manifest(generated)
        return generated

    def _clear_previous_generated_files(self) -> None:
        patterns = (
            (OUTPUT_DIR, "color_*.stl"),
            (TEMP_DIR, "color_*_mask.png"),
            (TEMP_DIR, "color_*.svg"),
            (TEMP_DIR, "color_*.scad"),
        )
        for directory, pattern in patterns:
            for path in directory.glob(pattern):
                if path.is_file():
                    path.unlink()

        if OUTPUT_MANIFEST.exists():
            OUTPUT_MANIFEST.unlink()

    def _write_output_manifest(self, generated: list[Path]) -> None:
        lines = [
            "Importar estos STL juntos en Bambu Studio.",
            "Elegir cargar como un solo objeto con varias partes.",
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
    ) -> np.ndarray:
        mask = np.where(labels_map == label_id, 255, 0).astype(np.uint8)

        if morph_size_px > 1:
            kernel_size = max(2, morph_size_px)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
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
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            return []

        hierarchy_rows = hierarchy[0]
        min_contour_area = max(1.0, float(min_area_px))
        shapes: list[FilledShape] = []

        for contour_index, contour in enumerate(contours):
            parent_index = int(hierarchy_rows[contour_index][3])
            if parent_index != -1:
                continue

            area = abs(cv2.contourArea(contour))
            if area < min_contour_area:
                continue

            outer_points = self._contour_to_points(contour)
            if len(outer_points) < 3:
                continue

            holes: list[list[Point]] = []
            child_index = int(hierarchy_rows[contour_index][2])
            while child_index != -1:
                child = contours[child_index]
                if abs(cv2.contourArea(child)) >= 1.0:
                    hole_points = self._contour_to_points(child)
                    if len(hole_points) >= 3:
                        holes.append(hole_points)
                child_index = int(hierarchy_rows[child_index][0])

            shapes.append((outer_points, holes))

        return shapes

    def _contour_to_points(self, contour: np.ndarray) -> list[Point]:
        epsilon = max(0.15, 0.0008 * cv2.arcLength(contour, True))
        approx = cv2.approxPolyDP(contour, epsilon, True)
        points: list[Point] = []
        previous: Point | None = None

        for x_value, y_value in approx.reshape(-1, 2):
            point = (int(x_value), int(y_value))
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
    ) -> None:
        path_parts: list[str] = []
        for outer_points, holes in shapes:
            path_parts.append(self._points_to_svg_path(outer_points))
            for hole_points in holes:
                path_parts.append(self._points_to_svg_path(hole_points))

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
        commands = [f"M {first_x} {first_y}"]
        for x_value, y_value in points[1:]:
            commands.append(f"L {x_value} {y_value}")
        commands.append("Z")
        return " ".join(commands)

    def _points_to_scad_list(self, points: list[Point]) -> str:
        return "[" + ", ".join(f"[{x_value}, {y_value}]" for x_value, y_value in points) + "]"

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

    def _write_scad(
        self,
        scad_path: Path,
        svg_path: Path,
        shapes: list[FilledShape],
        z_offset: float,
        thickness: float,
        pixel_to_mm: float,
    ) -> None:
        svg_for_comment = svg_path.resolve().as_posix().replace('"', '\\"')
        shape_body = "".join(
            self._shape_to_scad_2d(outer_points, holes, "        ")
            for outer_points, holes in shapes
        )
        scad = (
            "// Generated by image-to-svg-stl-converter.\n"
            "// Each STL is one material/color part.\n"
            "// The SVG uses the original full image canvas in pixel coordinates.\n"
            "// The STL is generated from filled OpenSCAD polygons, not outlines.\n"
            "// All parts share the same 0,0 origin and the same scale.\n"
            f'// Review SVG: "{svg_for_comment}"\n\n'
            f"pixel_to_mm = {pixel_to_mm:.10f};\n\n"
            "module filled_color_shape() {\n"
            "    union() {\n"
            f"{shape_body}"
            "    }\n"
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
