# image-to-svg-stl-converter web

Version web del conversor. Mantiene la idea del `app.py` de escritorio:

- Carga una imagen.
- Detecta colores con OpenCV.
- Permite elegir colores, `z offset` y grosor.
- Genera un STL sólido por color con OpenSCAD.
- Mantiene mismo canvas, mismo origen y misma escala.
- Entrega un ZIP con los STL, `preview.png` e `instrucciones.txt`.

## Estructura

```text
web-version/
  backend/
    main.py
    converter.py
    requirements.txt
    input/
    output/
    temp/
  frontend/
    app/
    components/
    package.json
```

## 1. Instalar backend

```powershell
cd web-version\backend
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install --only-binary=:all: -r requirements.txt
```

Si `numpy` u `opencv` dicen que no hay wheel compatible, crea el entorno con Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
```

Si OpenSCAD no está en `C:\Program Files\OpenSCAD\openscad.exe`, configura la ruta:

```powershell
$env:OPENSCAD_PATH="C:\Ruta\A\openscad.exe"
```

## 2. Iniciar backend

```powershell
cd web-version\backend
.\.venv\Scripts\activate
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

También puedes iniciarlo sin activar el entorno:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

API:

- `GET http://127.0.0.1:8000/api/health`
- `POST http://127.0.0.1:8000/api/upload`
- `POST http://127.0.0.1:8000/api/jobs/{job_id}/detect`
- `POST http://127.0.0.1:8000/api/jobs/{job_id}/generate`

## 3. Instalar frontend

En otra terminal:

```powershell
cd web-version\frontend
npm install
```

## 4. Iniciar frontend

```powershell
cd web-version\frontend
npm run dev
```

Abre:

```text
http://localhost:3000
```

## 5. Probar

1. Sube una imagen PNG/JPG/WEBP.
2. Ajusta `Colores max.` si hace falta.
3. Pulsa `Detectar colores`.
4. Elige preset.
5. Marca los colores que quieras exportar.
6. Ajusta `Z offset` y `Grosor`.
7. Activa `Mirror X` solo si el modelo sale espejado.
8. Pulsa `Generar STL`.
9. Descarga el ZIP.

## Bambu Studio

STL no guarda colores. Cada STL representa un color/material.

En Bambu Studio:

1. Extrae el ZIP.
2. Importa todos los STL juntos.
3. Acepta cargarlos como un solo objeto con múltiples partes.
4. Asigna un filamento/color a cada parte.

Los STL generados comparten canvas, origen y escala para quedar alineados.
