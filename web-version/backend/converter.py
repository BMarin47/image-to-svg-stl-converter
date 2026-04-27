from __future__ import annotations

import json
import math
import shutil
import subprocess
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Literal

import cv2
import numpy as np
from PIL import Image


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"

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

Point = tuple[float, float]
FilledShape = tuple[list[Point], list[list[Point]]]


@dataclass
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
class ExportRequest:
    label_id: int
    hex_value: str
    z_offset: float
    thickness: float


@dataclass
class GenerateSettings:
    preset_name: str
    model_width_mm: float
    base_thickness_mm: float
    min_area_px: int
    morph_size_px: int
    mirror_x: bool
    openscad_path: str | None
    colors: list[ExportRequest]


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


def ensure_directories() -> None:
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def safe_hex_for_filename(hex_value: str) -> str:
    return hex_value.strip().lstrip("#").upper()


def format_coord(value: float) -> str:
    if abs(value - round(value)) < 0.001:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def chunked(items: np.ndarray, size: int) -> Iterable[np.ndarray]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class WebConverter:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or BASE_DIR
        self.input_dir = self.base_dir / "input"
        self.output_dir = self.base_dir / "output"
        self.temp_dir = self.base_dir / "temp"
        self.jobs_dir = self.temp_dir / "jobs"
        self.input_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        self.temp_dir.mkdir(exist_ok=True)
        self.jobs_dir.mkdir(exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def create_job(self, job_id: str, image_bytes: bytes, filename: str) -> dict:
        job_dir = self.job_dir(job_id)
        if job_dir.exists():
            shutil.rmtree(job_dir)
        (job_dir / "input").mkdir(parents=True)
        (job_dir / "output").mkdir()
        (job_dir / "temp").mkdir()

        suffix = Path(filename).suffix.lower() or ".png"
        image_path = job_dir / "input" / f"source{suffix}"
        image_path.write_bytes(image_bytes)

        image = Image.open(image_path).convert("RGBA")
        canonical_path = job_dir / "input" / "source.png"
        image.save(canonical_path)
        preview_path = job_dir / "preview.png"
        image.save(preview_path)

        metadata = {
            "job_id": job_id,
            "filename": filename,
            "image_path": str(canonical_path),
            "preview_path": str(preview_path),
            "width": image.width,
            "height": image.height,
        }
        (job_dir / "job.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return metadata

    def get_job_metadata(self, job_id: str) -> dict:
        path = self.job_dir(job_id) / "job.json"
        if not path.exists():
            raise FileNotFoundError("No existe el trabajo solicitado.")
        return json.loads(path.read_text(encoding="utf-8"))

    def detect_colors(
        self,
        job_id: str,
        max_colors: int = DEFAULT_COLOR_COUNT,
        alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
    ) -> dict:
        metadata = self.get_job_metadata(job_id)
        image_rgba = np.array(Image.open(metadata["image_path"]).convert("RGBA"))
        colors, labels_map, valid_mask = self._detect_colors(
            image_rgba,
            max(1, int(max_colors)),
            max(0, min(255, int(alpha_threshold))),
        )

        job_dir = self.job_dir(job_id)
        np.save(job_dir / "labels.npy", labels_map)
        np.save(job_dir / "valid_mask.npy", valid_mask)
        colors_json = [asdict(color) for color in colors]
        (job_dir / "colors.json").write_text(json.dumps(colors_json, indent=2), encoding="utf-8")
        (job_dir / "detect_settings.json").write_text(
            json.dumps(
                {"max_colors": max_colors, "alpha_threshold": alpha_threshold},
                indent=2,
            ),
            encoding="utf-8",
        )

        return {
            "job_id": job_id,
            "width": metadata["width"],
            "height": metadata["height"],
            "colors": colors_json,
            "presets": [asdict(preset) for preset in PRODUCT_PRESETS],
        }

    def generate_zip(self, job_id: str, settings: GenerateSettings) -> dict:
        metadata = self.get_job_metadata(job_id)
        job_dir = self.job_dir(job_id)
        labels_path = job_dir / "labels.npy"
        colors_path = job_dir / "colors.json"
        if not labels_path.exists() or not colors_path.exists():
            raise RuntimeError("Primero detecta colores.")

        image_rgba = np.array(Image.open(metadata["image_path"]).convert("RGBA"))
        labels_map = np.load(labels_path)
        if settings.mirror_x:
            image_rgba = np.ascontiguousarray(np.fliplr(image_rgba))
            labels_map = np.ascontiguousarray(np.fliplr(labels_map))

        detected_colors = [
            DetectedColor(
                label_id=int(item["label_id"]),
                rgb=tuple(item["rgb"]),
                hex_value=item["hex_value"],
                pixel_count=int(item["pixel_count"]),
                visible_fraction=float(item["visible_fraction"]),
                border_fraction=float(item["border_fraction"]),
                export_default=bool(item["export_default"]),
                note=item["note"],
            )
            for item in json.loads(colors_path.read_text(encoding="utf-8"))
        ]

        output_dir = job_dir / "output"
        temp_dir = job_dir / "temp"
        shutil.rmtree(output_dir, ignore_errors=True)
        shutil.rmtree(temp_dir, ignore_errors=True)
        output_dir.mkdir()
        temp_dir.mkdir()

        openscad_path = Path(settings.openscad_path or DEFAULT_OPENSCAD_PATH)
        if not openscad_path.exists():
            raise FileNotFoundError(
                f"No se encontró OpenSCAD en {openscad_path}. "
                "Configura OPENSCAD_PATH o envía openscad_path desde la interfaz."
            )

        height_px, width_px = image_rgba.shape[:2]
        if width_px < 1 or height_px < 1:
            raise RuntimeError("La imagen no tiene dimensiones válidas.")

        preset = PRODUCT_PRESETS_BY_NAME.get(settings.preset_name, PRODUCT_PRESETS[0])
        if preset.base_mode == BASE_MODE_KEYRING and not settings.colors:
            settings.colors = [
                ExportRequest(
                    label_id=color.label_id,
                    hex_value=color.hex_value,
                    z_offset=max(preset.relief_z_offset_mm, settings.base_thickness_mm),
                    thickness=preset.relief_thickness_mm,
                )
                for color in detected_colors
                if color.export_default
            ]
            if not settings.colors:
                raise RuntimeError(
                    "El preset Llavero necesita al menos un color real del logo para generar "
                    "el relieve. Detecta colores o marca un color para exportar."
                )
        pixel_to_mm = settings.model_width_mm / float(width_px)
        generated: list[Path] = []
        base_path: Path | None = None
        color_paths: list[Path] = []

        if preset.base_mode != BASE_MODE_NONE and settings.base_thickness_mm > 0:
            base_path = self._export_product_base(
                labels_map,
                detected_colors,
                openscad_path,
                output_dir,
                temp_dir,
                preset,
                settings.base_thickness_mm,
                pixel_to_mm,
                width_px,
                height_px,
                settings.min_area_px,
                settings.morph_size_px,
            )
            generated.append(base_path)

        antialias_assignments = self._build_antialias_assignments(
            settings.colors,
            detected_colors,
        )

        for export_index, request in enumerate(settings.colors, start=1):
            hex_name = safe_hex_for_filename(request.hex_value)
            base_name = f"color_{export_index:02d}_{hex_name}"
            mask_path = temp_dir / f"{base_name}_mask.png"
            svg_path = temp_dir / f"{base_name}.svg"
            scad_path = temp_dir / f"{base_name}.scad"
            stl_path = output_dir / f"{base_name}.stl"

            z_offset = request.z_offset
            if preset.base_mode != BASE_MODE_NONE and settings.base_thickness_mm > 0:
                z_offset = max(z_offset, settings.base_thickness_mm)

            mask = self._build_clean_mask(
                labels_map,
                request.label_id,
                settings.min_area_px,
                settings.morph_size_px,
                extra_label_ids=antialias_assignments.get(request.label_id, []),
            )
            if np.count_nonzero(mask) == 0:
                continue

            Image.fromarray(mask).save(mask_path)
            shapes = self._extract_filled_shapes(mask, settings.min_area_px)
            if not shapes:
                continue

            self._write_svg_from_shapes(shapes, request.hex_value, svg_path, width_px, height_px)
            self._write_scad(scad_path, shapes, z_offset, request.thickness, pixel_to_mm)
            self._run_openscad(openscad_path, scad_path, stl_path)
            color_paths.append(stl_path)
            generated.append(stl_path)

        if preset.base_mode == BASE_MODE_KEYRING and base_path is not None and color_paths:
            complete_path = output_dir / "producto_llavero_completo.stl"
            complete_scad_path = temp_dir / "producto_llavero_completo.scad"
            self._write_import_union_scad(complete_scad_path, [base_path, *color_paths])
            self._run_openscad(openscad_path, complete_scad_path, complete_path)
            generated.append(complete_path)

        if not generated:
            raise RuntimeError("No se generó ningún STL. Marca al menos un color exportable.")

        preview_path = output_dir / "preview.png"
        Image.fromarray(image_rgba).save(preview_path)
        instructions_path = output_dir / "instrucciones.txt"
        instructions_path.write_text(
            self._instructions_text(generated, settings, preset),
            encoding="utf-8",
        )

        zip_path = output_dir / f"{job_id}_stl.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in generated:
                archive.write(path, arcname=path.name)
            archive.write(preview_path, arcname="preview.png")
            archive.write(instructions_path, arcname="instrucciones.txt")

        return {
            "job_id": job_id,
            "zip_name": zip_path.name,
            "zip_path": str(zip_path),
            "files": [path.name for path in generated],
        }

    def _detect_colors(
        self,
        image_rgba: np.ndarray,
        max_colors: int,
        alpha_threshold: int,
    ) -> tuple[list[DetectedColor], np.ndarray, np.ndarray]:
        height, width = image_rgba.shape[:2]
        rgb = image_rgba[:, :, :3].astype(np.uint8)
        alpha = image_rgba[:, :, 3]
        valid_mask = alpha > alpha_threshold
        if not np.any(valid_mask):
            raise RuntimeError("La imagen no tiene píxeles visibles con el alpha configurado.")

        valid_rgb = rgb[valid_mask]
        unique_rgb, inverse = np.unique(valid_rgb.reshape(-1, 3), axis=0, return_inverse=True)
        if len(unique_rgb) <= max_colors:
            labels_valid = inverse.astype(np.int32)
            centers_rgb = [tuple(int(v) for v in row) for row in unique_rgb]
        else:
            labels_valid, centers_rgb = self._kmeans_labels(rgb, valid_mask, max_colors)

        labels_map = np.full((height, width), -1, dtype=np.int32)
        labels_map[valid_mask] = labels_valid
        total_visible = int(np.count_nonzero(valid_mask))
        detected: list[DetectedColor] = []

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
                note_parts.append("borde suavizado probable, se fusiona con el color cercano")
            if visible_fraction < 0.002:
                note_parts.append("muy pequeño")

            export_default = not is_probable_white_background and not is_probable_antialias

            detected.append(
                DetectedColor(
                    label_id=label_id,
                    rgb=center_rgb,
                    hex_value=rgb_to_hex(center_rgb),
                    pixel_count=pixel_count,
                    visible_fraction=visible_fraction,
                    border_fraction=border_fraction,
                    export_default=export_default,
                    note=", ".join(note_parts),
                )
            )

        detected.sort(key=lambda item: item.pixel_count, reverse=True)
        return detected, labels_map, valid_mask

    def _kmeans_labels(
        self,
        rgb: np.ndarray,
        valid_mask: np.ndarray,
        max_colors: int,
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

    def _build_antialias_assignments(
        self,
        requests: list[ExportRequest],
        detected_colors: list[DetectedColor],
    ) -> dict[int, list[int]]:
        selected_by_id = {request.label_id: request for request in requests}
        assignments: dict[int, list[int]] = {request.label_id: [] for request in requests}
        if not requests:
            return assignments

        for color in detected_colors:
            if color.label_id in selected_by_id:
                continue
            if not self._is_probable_antialias_color(color):
                continue

            closest = min(
                requests,
                key=lambda request: self._hex_distance_squared(color.hex_value, request.hex_value),
            )
            assignments.setdefault(closest.label_id, []).append(color.label_id)
        return assignments

    def _hex_distance_squared(self, first_hex: str, second_hex: str) -> int:
        first = self._hex_to_rgb(first_hex)
        second = self._hex_to_rgb(second_hex)
        return sum((first[index] - second[index]) ** 2 for index in range(3))

    def _hex_to_rgb(self, hex_value: str) -> tuple[int, int, int]:
        value = hex_value.strip().lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

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

        kernel_size = max(2, morph_size_px)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        if min_area_px > 0:
            mask = self._remove_small_components(mask, min_area_px)
        return mask

    def _remove_small_components(self, mask: np.ndarray, min_area_px: int) -> np.ndarray:
        count, component_map, stats, _centroids = cv2.connectedComponentsWithStats(
            mask,
            connectivity=8,
        )
        clean = np.zeros_like(mask)
        for component_id in range(1, count):
            area = int(stats[component_id, cv2.CC_STAT_AREA])
            if area >= min_area_px:
                clean[component_map == component_id] = 255
        return clean

    def _export_product_base(
        self,
        labels_map: np.ndarray,
        detected_colors: list[DetectedColor],
        openscad_path: Path,
        output_dir: Path,
        temp_dir: Path,
        preset: ProductPreset,
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
        base_name = f"base_{preset_slug}_{safe_hex_for_filename(base_hex)}"
        svg_path = temp_dir / f"{base_name}.svg"
        scad_path = temp_dir / f"{base_name}.scad"
        stl_path = output_dir / f"{base_name}.stl"

        reference_mask = self._build_base_reference_mask(labels_map, detected_colors)
        shapes, _mask, cutouts = self._build_product_base_shapes(
            reference_mask,
            preset,
            pixel_to_mm,
            width_px,
            height_px,
            min_area_px,
            morph_size_px,
        )
        if not shapes:
            raise RuntimeError(f"No se pudo generar la base para el preset {preset.name}.")

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
            raise RuntimeError("La imagen no tiene píxeles visibles para construir la base.")
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
        if preset.base_mode == BASE_MODE_RECTANGLE:
            shape = self._rectangle_base_shape(reference_mask, width_px, height_px)
            mask = np.zeros((height_px, width_px), dtype=np.uint8)
            cv2.fillPoly(mask, [np.array(shape[0], dtype=np.int32).reshape((-1, 1, 2))], 255)
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
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE,
                (morph_size_px, morph_size_px),
            )
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
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
        return shapes, mask, [hole_points]

    def _rectangle_base_shape(
        self,
        visible_mask: np.ndarray,
        width_px: int,
        height_px: int,
    ) -> FilledShape:
        left, top, right, bottom = self._visible_bounds(visible_mask)
        padding_px = self._base_padding_px(width_px, height_px)
        left = max(0, left - padding_px)
        top = max(0, top - padding_px)
        right = min(width_px, right + padding_px)
        bottom = min(height_px, bottom + padding_px)
        return [(left, top), (right, top), (right, bottom), (left, bottom)], []

    def _visible_bounds(self, mask: np.ndarray) -> tuple[int, int, int, int]:
        y_values, x_values = np.nonzero(mask)
        if len(x_values) == 0 or len(y_values) == 0:
            raise RuntimeError("No hay píxeles visibles para calcular el contorno general.")
        return int(x_values.min()), int(y_values.min()), int(x_values.max()) + 1, int(y_values.max()) + 1

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
            raise RuntimeError("No hay píxeles visibles para calcular el contorno general.")

        points = np.column_stack((x_values, y_values)).astype(np.int32).reshape((-1, 1, 2))
        hull = cv2.convexHull(points)
        mask = np.zeros((height_px, width_px), dtype=np.uint8)
        cv2.fillPoly(mask, [hull], 255)
        if padding_px > 0:
            kernel_size = padding_px * 2 + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
            mask = cv2.dilate(mask, kernel, iterations=1)
        return mask

    def _circle_points(self, center_x: float, center_y: float, radius: float) -> list[Point]:
        points: list[Point] = []
        for index in range(72):
            angle = (2.0 * math.pi * index) / 72
            point = (
                float(center_x + math.cos(angle) * radius),
                float(center_y + math.sin(angle) * radius),
            )
            if not points or point != points[-1]:
                points.append(point)
        return points

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
        return shapes

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
            if int(hierarchy_rows[contour_index][3]) != -1:
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
                child_area = abs(cv2.contourArea(child)) / (coordinate_scale * coordinate_scale)
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

    def _contour_to_points(
        self,
        contour: np.ndarray,
        coordinate_scale: float = 1.0,
    ) -> list[Point]:
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
            raise RuntimeError(f"No hay polígonos rellenos para escribir en {svg_path}.")

        svg_path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{canvas_width_px}" height="{canvas_height_px}" '
            f'viewBox="0 0 {canvas_width_px} {canvas_height_px}">\n'
            f'  <path d="{" ".join(path_parts)}" fill="{color_hex}" fill-rule="evenodd"/>\n'
            "</svg>\n",
            encoding="utf-8",
        )

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

    def _shape_to_scad_2d(
        self,
        outer_points: list[Point],
        holes: list[list[Point]],
        indent: str,
    ) -> str:
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
        shapes: list[FilledShape],
        z_offset: float,
        thickness: float,
        pixel_to_mm: float,
        cutout_shapes: list[list[Point]] | None = None,
    ) -> None:
        module_body = self._scad_2d_body(shapes, cutout_shapes or [], "    ")
        scad = (
            "// Generated by web image-to-svg-stl-converter.\n"
            "// The STL is generated from filled polygons, not outlines.\n"
            "// All color parts share the same 0,0 origin and the same scale.\n\n"
            f"pixel_to_mm = {pixel_to_mm:.10f};\n\n"
            "module filled_shape() {\n"
            f"{module_body}"
            "}\n\n"
            f"translate([0, 0, {z_offset:.6f}])\n"
            f"linear_extrude(height = {thickness:.6f}, convexity = 10)\n"
            "    scale([pixel_to_mm, pixel_to_mm, 1])\n"
            "        filled_shape();\n"
        )
        scad_path.write_text(scad, encoding="utf-8")

    def _write_import_union_scad(self, scad_path: Path, stl_paths: list[Path]) -> None:
        imports = "\n".join(
            f'    import("{self._scad_path(path)}", convexity = 10);'
            for path in stl_paths
        )
        scad = (
            "// Complete keyring generated by importing the aligned STL parts.\n"
            "// The separate STL parts are still exported for multicolor printing.\n\n"
            "union() {\n"
            f"{imports}\n"
            "}\n"
        )
        scad_path.write_text(scad, encoding="utf-8")

    def _scad_path(self, path: Path) -> str:
        return str(path.resolve()).replace("\\", "/").replace('"', '\\"')

    def _run_openscad(self, openscad_path: Path, scad_path: Path, stl_path: Path) -> None:
        result = subprocess.run(
            [str(openscad_path), "-o", str(stl_path), str(scad_path)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "OpenSCAD falló al generar el STL.\n\n"
                f"SCAD: {scad_path}\n\n"
                f"STDOUT:\n{result.stdout.strip()}\n\n"
                f"STDERR:\n{result.stderr.strip()}"
            )
        if not stl_path.exists():
            raise RuntimeError(f"OpenSCAD terminó sin generar {stl_path}.")

    def _safe_name_for_filename(self, name: str) -> str:
        replacements = {
            "á": "a",
            "é": "e",
            "í": "i",
            "ó": "o",
            "ú": "u",
            "ñ": "n",
        }
        cleaned = "".join(replacements.get(char.lower(), char.lower()) for char in name)
        cleaned = "".join(char if char.isalnum() else "_" for char in cleaned)
        return "_".join(part for part in cleaned.split("_") if part)

    def _instructions_text(
        self,
        generated: list[Path],
        settings: GenerateSettings,
        preset: ProductPreset,
    ) -> str:
        names = "\n".join(f"- {path.name}" for path in generated)
        keyring_note = ""
        bambu_note = (
            "- En Bambu Studio importa todos los STL juntos.\n"
            "- Acepta cargarlos como un solo objeto con multiples partes.\n"
        )
        if preset.base_mode == BASE_MODE_KEYRING:
            keyring_note = (
                "- Para ver o imprimir el llavero completo en una sola pieza, usa "
                "producto_llavero_completo.stl.\n"
                "- Para impresion multicolor, importa base_llavero_*.stl y los color_*.stl "
                "juntos como un solo objeto con multiples partes.\n"
            )
            bambu_note = ""
        return (
            "Archivos generados por image-to-svg-stl-converter web.\n\n"
            f"Preset: {preset.name}\n"
            f"Ancho del modelo: {settings.model_width_mm} mm\n"
            f"Mirror X: {'si' if settings.mirror_x else 'no'}\n\n"
            "STL generados:\n"
            f"{names}\n\n"
            "Importante:\n"
            "- STL no guarda colores.\n"
            "- Cada STL representa un color/material.\n"
            f"{keyring_note}"
            f"{bambu_note}"
            "- Todos comparten el mismo canvas, origen y escala.\n"
        )
