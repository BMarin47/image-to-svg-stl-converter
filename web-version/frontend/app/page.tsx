"use client";

import { useMemo, useState } from "react";
import { ColorRow, ColorTable } from "../components/ColorTable";

type Preset = {
  name: string;
  model_width_mm: number;
  base_thickness_mm: number;
  relief_thickness_mm: number;
  relief_z_offset_mm: number;
  export_background: boolean;
  base_mode: string;
};

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const PRESETS: Preset[] = [
  {
    name: "Logo simple",
    model_width_mm: 100,
    base_thickness_mm: 0,
    relief_thickness_mm: 1,
    relief_z_offset_mm: 0,
    export_background: false,
    base_mode: "none"
  },
  {
    name: "Llavero",
    model_width_mm: 60,
    base_thickness_mm: 2.2,
    relief_thickness_mm: 1,
    relief_z_offset_mm: 2.2,
    export_background: false,
    base_mode: "keyring"
  },
  {
    name: "Imán",
    model_width_mm: 65,
    base_thickness_mm: 1.2,
    relief_thickness_mm: 0.8,
    relief_z_offset_mm: 1.2,
    export_background: false,
    base_mode: "contour"
  },
  {
    name: "Placa",
    model_width_mm: 100,
    base_thickness_mm: 2,
    relief_thickness_mm: 0.8,
    relief_z_offset_mm: 2,
    export_background: false,
    base_mode: "rectangle"
  },
  {
    name: "Logo en relieve",
    model_width_mm: 90,
    base_thickness_mm: 1.6,
    relief_thickness_mm: 1,
    relief_z_offset_mm: 1.6,
    export_background: false,
    base_mode: "contour"
  }
];

export default function Home() {
  const [jobId, setJobId] = useState<string>("");
  const [previewUrl, setPreviewUrl] = useState<string>("");
  const [filename, setFilename] = useState<string>("");
  const [colors, setColors] = useState<ColorRow[]>([]);
  const [presetName, setPresetName] = useState("Logo simple");
  const [maxColors, setMaxColors] = useState(4);
  const [alphaThreshold, setAlphaThreshold] = useState(8);
  const [mergeTolerance, setMergeTolerance] = useState(18);
  const [ignoreWhiteBackground, setIgnoreWhiteBackground] = useState(true);
  const [modelWidth, setModelWidth] = useState(100);
  const [baseThickness, setBaseThickness] = useState(0);
  const [baseEnabled, setBaseEnabled] = useState(false);
  const [reliefZOffset, setReliefZOffset] = useState(0);
  const [reliefThickness, setReliefThickness] = useState(1);
  const [minArea, setMinArea] = useState(16);
  const [morphSize, setMorphSize] = useState(2);
  const [mirrorX, setMirrorX] = useState(false);
  const [export3mf, setExport3mf] = useState(true);
  const [openscadPath, setOpenscadPath] = useState("C:\\Program Files\\OpenSCAD\\openscad.exe");
  const [downloadUrl, setDownloadUrl] = useState("");
  const [generatedFiles, setGeneratedFiles] = useState<string[]>([]);
  const [status, setStatus] = useState("Sube una imagen para comenzar.");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const selectedPreset = useMemo(
    () => PRESETS.find((preset) => preset.name === presetName) || PRESETS[0],
    [presetName]
  );

  function applyPreset(name: string) {
    const preset = PRESETS.find((item) => item.name === name) || PRESETS[0];
    setPresetName(preset.name);
    setModelWidth(preset.model_width_mm);
    setBaseThickness(preset.base_thickness_mm);
    setBaseEnabled(preset.base_thickness_mm > 0);
    setReliefZOffset(preset.relief_z_offset_mm);
    setReliefThickness(preset.relief_thickness_mm);
    setColors((rows) =>
      rows.map((row) => ({
        ...row,
        export: preset.export_background ? true : row.export_default,
        z_offset: preset.relief_z_offset_mm,
        thickness: preset.relief_thickness_mm
      }))
    );
  }

  function applyHeightsToColors() {
    setColors((rows) =>
      rows.map((row) => ({
        ...row,
        z_offset: reliefZOffset,
        thickness: reliefThickness
      }))
    );
  }

  async function upload(file: File) {
    setBusy(true);
    setError("");
    setDownloadUrl("");
    setGeneratedFiles([]);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(`${API_BASE}/api/upload`, {
        method: "POST",
        body: form
      });
      const data = await readJson(response);
      setJobId(data.job_id);
      setFilename(data.filename);
      setPreviewUrl(`${API_BASE}${data.preview_url}`);
      setColors([]);
      setStatus(`Imagen cargada: ${data.width} x ${data.height} px`);
    } catch (caught) {
      setError(messageFrom(caught));
    } finally {
      setBusy(false);
    }
  }

  async function detectColors() {
    if (!jobId) return;
    setBusy(true);
    setError("");
    setDownloadUrl("");
    try {
      const response = await fetch(`${API_BASE}/api/jobs/${jobId}/detect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          max_colors: maxColors,
          alpha_threshold: alphaThreshold,
          merge_tolerance: mergeTolerance,
          ignore_white_background: ignoreWhiteBackground
        })
      });
      const data = await readJson(response);
      setColors(
        data.colors.map((color: any) => ({
          ...color,
          export: selectedPreset.export_background ? true : color.export_default,
          z_offset: reliefZOffset,
          thickness: reliefThickness
        }))
      );
      setStatus(`Detectados ${data.colors.length} colores.`);
    } catch (caught) {
      setError(messageFrom(caught));
    } finally {
      setBusy(false);
    }
  }

  async function generateStl() {
    if (!jobId) return;
    setBusy(true);
    setError("");
    setDownloadUrl("");
    setGeneratedFiles([]);
    try {
      const response = await fetch(`${API_BASE}/api/jobs/${jobId}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preset_name: presetName,
          model_width_mm: modelWidth,
          base_thickness_mm: baseEnabled ? baseThickness : 0,
          min_area_px: minArea,
          morph_size_px: morphSize,
          mirror_x: mirrorX,
          openscad_path: openscadPath,
          export_3mf: export3mf,
          colors
        })
      });
      const data = await readJson(response);
      setDownloadUrl(`${API_BASE}${data.download_url}`);
      setGeneratedFiles(data.files);
      setStatus(`Generados ${data.files.length} archivos. Descarga el ZIP.`);
    } catch (caught) {
      setError(messageFrom(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <div className="topbar">
        <div>
          <h1 className="title">Image to SVG/STL Converter Web</h1>
          <p className="subtitle">STL separados por color, alineados para Bambu Studio.</p>
        </div>
        {downloadUrl ? (
          <a className="download" href={downloadUrl}>
            Descargar ZIP
          </a>
        ) : null}
      </div>

      <div className="grid">
        <section className="panel stack">
          <h2>Imagen</h2>
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp,image/bmp"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) upload(file);
            }}
          />
          {previewUrl ? (
            <img className="preview" src={previewUrl} alt={`Vista previa ${filename}`} />
          ) : (
            <div className="placeholder">Sin imagen</div>
          )}

          <div className="fields">
            <div className="field">
              <label>Colores AMS</label>
              <select value={maxColors} onChange={(event) => setMaxColors(Number(event.target.value))}>
                {[2, 3, 4, 5].map((count) => (
                  <option key={count} value={count}>
                    {count} colores
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Tolerancia similares</label>
              <input
                type="number"
                min="0"
                max="80"
                step="1"
                value={mergeTolerance}
                onChange={(event) => setMergeTolerance(Number(event.target.value))}
              />
            </div>
            <div className="field">
              <label>Alpha mínimo</label>
              <input
                type="number"
                min="0"
                max="255"
                value={alphaThreshold}
                onChange={(event) => setAlphaThreshold(Number(event.target.value))}
              />
            </div>
            <label className="row checkField">
              <input
                type="checkbox"
                checked={ignoreWhiteBackground}
                onChange={(event) => setIgnoreWhiteBackground(event.target.checked)}
              />
              Ignorar fondo blanco externo
            </label>
          </div>

          <button disabled={!jobId || busy} onClick={detectColors}>
            Detectar colores
          </button>

          <div className="status">{status}</div>
          {error ? <div className="status error">{error}</div> : null}
        </section>

        <section className="panel stack">
          <h2>Configuración</h2>
          <div className="fields">
            <div className="field">
              <label>Preset de producto</label>
              <select value={presetName} onChange={(event) => applyPreset(event.target.value)}>
                {PRESETS.map((preset) => (
                  <option key={preset.name} value={preset.name}>
                    {preset.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Ancho modelo mm</label>
              <input
                type="number"
                min="0.1"
                step="0.1"
                value={modelWidth}
                onChange={(event) => setModelWidth(Number(event.target.value))}
              />
            </div>
            <div className="field">
              <label>Grosor base mm</label>
              <input
                type="number"
                min="0"
                step="0.1"
                value={baseThickness}
                onChange={(event) => setBaseThickness(Number(event.target.value))}
              />
            </div>
            <label className="row checkField">
              <input
                type="checkbox"
                checked={baseEnabled}
                onChange={(event) => setBaseEnabled(event.target.checked)}
              />
              Generar base separada
            </label>
            <div className="field">
              <label>Z colores mm</label>
              <input
                type="number"
                min="0"
                step="0.1"
                value={reliefZOffset}
                onChange={(event) => setReliefZOffset(Number(event.target.value))}
              />
            </div>
            <div className="field">
              <label>Altura colores mm</label>
              <input
                type="number"
                min="0.01"
                step="0.1"
                value={reliefThickness}
                onChange={(event) => setReliefThickness(Number(event.target.value))}
              />
            </div>
            <div className="field">
              <label>Área mínima px²</label>
              <input
                type="number"
                min="0"
                value={minArea}
                onChange={(event) => setMinArea(Number(event.target.value))}
              />
            </div>
            <div className="field">
              <label>Limpieza px</label>
              <input
                type="number"
                min="0"
                value={morphSize}
                onChange={(event) => setMorphSize(Number(event.target.value))}
              />
            </div>
            <div className="field">
              <label>OpenSCAD</label>
              <input value={openscadPath} onChange={(event) => setOpenscadPath(event.target.value)} />
            </div>
          </div>

          <label className="row">
            <input type="checkbox" checked={mirrorX} onChange={(event) => setMirrorX(event.target.checked)} />
            Mirror X
          </label>

          <label className="row">
            <input type="checkbox" checked={export3mf} onChange={(event) => setExport3mf(event.target.checked)} />
            Generar 3MF armado
          </label>

          <ColorTable colors={colors} onChange={setColors} />

          <div className="row">
            <button disabled={!jobId || colors.length === 0 || busy} onClick={generateStl}>
              Generar STL
            </button>
            <button className="secondary" type="button" onClick={applyHeightsToColors}>
              Aplicar alturas
            </button>
            <button className="secondary" type="button" onClick={() => applyPreset(presetName)}>
              Aplicar preset a colores
            </button>
          </div>

          {generatedFiles.length ? (
            <div className="hint">
              Archivos generados:
              <br />
              {generatedFiles.join(", ")}
            </div>
          ) : (
            <p className="hint">
              STL no guarda colores. Cada STL representa un material. En Bambu Studio importa todos
              juntos como un solo objeto con múltiples partes.
            </p>
          )}
        </section>
      </div>
    </main>
  );
}

async function readJson(response: Response) {
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Error inesperado.");
  }
  return data;
}

function messageFrom(caught: unknown) {
  if (caught instanceof Error) return caught.message;
  return "Error inesperado.";
}
