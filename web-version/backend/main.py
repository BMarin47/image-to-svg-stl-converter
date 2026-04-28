from __future__ import annotations

import os
import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from converter import (
    DEFAULT_ALPHA_THRESHOLD,
    DEFAULT_COLOR_COUNT,
    DEFAULT_MERGE_TOLERANCE,
    DEFAULT_MIN_AREA_PX,
    DEFAULT_MODEL_WIDTH_MM,
    DEFAULT_MORPH_SIZE_PX,
    DEFAULT_OPENSCAD_PATH,
    PRODUCT_PRESETS,
    PRODUCT_PRESETS_BY_NAME,
    ExportRequest,
    GenerateSettings,
    WebConverter,
)


class DetectRequest(BaseModel):
    max_colors: int = Field(default=DEFAULT_COLOR_COUNT, ge=2, le=5)
    alpha_threshold: int = Field(default=DEFAULT_ALPHA_THRESHOLD, ge=0, le=255)
    merge_tolerance: float = Field(default=DEFAULT_MERGE_TOLERANCE, ge=0, le=80)
    ignore_white_background: bool = True


class GenerateColorRequest(BaseModel):
    label_id: int
    hex_value: str
    export: bool = True
    z_offset: float = Field(default=0.0, ge=0)
    thickness: float = Field(default=1.0, gt=0)


class GenerateRequest(BaseModel):
    preset_name: str = "Logo simple"
    model_width_mm: float = Field(default=DEFAULT_MODEL_WIDTH_MM, gt=0)
    base_thickness_mm: float = Field(default=0.0, ge=0)
    min_area_px: int = Field(default=DEFAULT_MIN_AREA_PX, ge=0)
    morph_size_px: int = Field(default=DEFAULT_MORPH_SIZE_PX, ge=0)
    mirror_x: bool = False
    openscad_path: str | None = None
    export_3mf: bool = True
    colors: list[GenerateColorRequest]


app = FastAPI(title="image-to-svg-stl-converter web")
converter = WebConverter()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    openscad_path = Path(os.getenv("OPENSCAD_PATH", DEFAULT_OPENSCAD_PATH))
    return {
        "ok": True,
        "openscad_path": str(openscad_path),
        "openscad_exists": openscad_path.exists(),
    }


@app.get("/api/presets")
def presets() -> dict:
    return {"presets": [asdict(preset) for preset in PRODUCT_PRESETS]}


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)) -> dict:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Sube una imagen PNG/JPG/WEBP válida.")

    job_id = uuid.uuid4().hex
    try:
        metadata = converter.create_job(job_id, await file.read(), file.filename or "image.png")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "job_id": job_id,
        "filename": metadata["filename"],
        "width": metadata["width"],
        "height": metadata["height"],
        "preview_url": f"/api/jobs/{job_id}/preview",
    }


@app.get("/api/jobs/{job_id}/preview")
def preview(job_id: str) -> FileResponse:
    try:
        metadata = converter.get_job_metadata(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(metadata["preview_path"], media_type="image/png")


@app.post("/api/jobs/{job_id}/detect")
def detect(job_id: str, request: DetectRequest) -> dict:
    try:
        return converter.detect_colors(
            job_id,
            max_colors=request.max_colors,
            alpha_threshold=request.alpha_threshold,
            merge_tolerance=request.merge_tolerance,
            ignore_white_background=request.ignore_white_background,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/generate")
def generate(job_id: str, request: GenerateRequest) -> dict:
    preset = PRODUCT_PRESETS_BY_NAME.get(request.preset_name)
    if preset is None:
        raise HTTPException(status_code=400, detail="Preset inválido.")

    selected_colors = [
        ExportRequest(
            label_id=color.label_id,
            hex_value=color.hex_value,
            z_offset=color.z_offset,
            thickness=color.thickness,
        )
        for color in request.colors
        if color.export
    ]

    if not selected_colors and preset.base_mode == "none":
        raise HTTPException(status_code=400, detail="Selecciona al menos un color para exportar.")

    openscad_path = request.openscad_path or os.getenv("OPENSCAD_PATH") or DEFAULT_OPENSCAD_PATH
    settings = GenerateSettings(
        preset_name=request.preset_name,
        model_width_mm=request.model_width_mm,
        base_thickness_mm=request.base_thickness_mm,
        min_area_px=request.min_area_px,
        morph_size_px=request.morph_size_px,
        mirror_x=request.mirror_x,
        openscad_path=openscad_path,
        colors=selected_colors,
        export_3mf=request.export_3mf,
    )

    try:
        result = converter.generate_zip(job_id, settings)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "job_id": job_id,
        "files": result["files"],
        "download_url": f"/api/jobs/{job_id}/download/{result['zip_name']}",
    }


@app.get("/api/jobs/{job_id}/download/{zip_name}")
def download(job_id: str, zip_name: str) -> FileResponse:
    output_dir = converter.job_dir(job_id) / "output"
    zip_path = output_dir / zip_name
    if not zip_path.exists() or zip_path.suffix.lower() != ".zip":
        raise HTTPException(status_code=404, detail="ZIP no encontrado.")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=zip_name,
    )
