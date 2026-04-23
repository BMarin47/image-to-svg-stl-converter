import tkinter as tk
from tkinter import filedialog, messagebox
import cv2
import numpy as np
import os
import subprocess

OPENSCAD_PATH = r"C:\Program Files\OpenSCAD\openscad.exe"
EXTRUDE_HEIGHT = 5

def load_mask(input_path):
    img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)

    if img is None:
        raise Exception("No se pudo leer la imagen.")

    # Si tiene transparencia, usa el canal alpha
    if len(img.shape) == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        mask = np.where(alpha > 20, 255, 0).astype(np.uint8)
    else:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

        # Detecta automáticamente lo oscuro como figura
        _, mask = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )

    # Limpieza suave
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    return mask

def find_contours_with_holes(mask):
    contours, hierarchy = cv2.findContours(
        mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )

    if hierarchy is None:
        return [], None

    return contours, hierarchy[0]

def simplify_contour(cnt):
    epsilon = 0.0015 * cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, epsilon, True)
    return approx

def contour_to_points(cnt):
    points = []
    for point in cnt:
        x, y = point[0]
        points.append(f"[{int(x)}, {-int(y)}]")
    return ", ".join(points)

def png_to_svg(contours, hierarchy, output_svg):
    with open(output_svg, "w", encoding="utf-8") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg">\n')

        for i, cnt in enumerate(contours):
            area = cv2.contourArea(cnt)
            if area < 30:
                continue

            cnt = simplify_contour(cnt)

            path = "M "
            for point in cnt:
                x, y = point[0]
                path += f"{int(x)},{int(y)} "
            path += "Z"

            f.write(f'<path d="{path}" fill="black"/>\n')

        f.write("</svg>")

def write_polygon(f, cnt, indent=""):
    cnt = simplify_contour(cnt)

    if len(cnt) < 3:
        return

    points = contour_to_points(cnt)
    f.write(f"{indent}polygon(points=[{points}]);\n")

def contours_to_scad(contours, hierarchy, scad_path):
    with open(scad_path, "w", encoding="utf-8") as f:
        f.write(f"linear_extrude(height = {EXTRUDE_HEIGHT}) {{\n")
        f.write("    union() {\n")

        for i, cnt in enumerate(contours):
            area = cv2.contourArea(cnt)

            if area < 30:
                continue

            parent = hierarchy[i][3]

            # Solo procesar contornos principales
            if parent != -1:
                continue

            child = hierarchy[i][2]

            if child == -1:
                write_polygon(f, cnt, "        ")
            else:
                f.write("        difference() {\n")
                write_polygon(f, cnt, "            ")

                while child != -1:
                    if cv2.contourArea(contours[child]) > 30:
                        write_polygon(f, contours[child], "            ")
                    child = hierarchy[child][0]

                f.write("        }\n")

        f.write("    }\n")
        f.write("}\n")

def scad_to_stl(scad_path, stl_path):
    result = subprocess.run(
        [
            OPENSCAD_PATH,
            "-o",
            os.path.abspath(stl_path),
            os.path.abspath(scad_path)
        ],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        raise Exception(result.stderr if result.stderr else "OpenSCAD falló.")

def process_image():
    file_path = filedialog.askopenfilename(filetypes=[("PNG files", "*.png")])

    if not file_path:
        return

    os.makedirs("output", exist_ok=True)
    os.makedirs("temp", exist_ok=True)

    base_name = os.path.splitext(os.path.basename(file_path))[0]

    svg_path = f"output/{base_name}.svg"
    scad_path = f"temp/{base_name}.scad"
    stl_path = f"output/{base_name}.stl"

    try:
        mask = load_mask(file_path)
        contours, hierarchy = find_contours_with_holes(mask)

        if not contours:
            raise Exception("No se detectaron formas válidas.")

        png_to_svg(contours, hierarchy, svg_path)
        contours_to_scad(contours, hierarchy, scad_path)
        scad_to_stl(scad_path, stl_path)

        messagebox.showinfo(
            "Success",
            f"Archivos generados correctamente:\n\n{svg_path}\n{stl_path}"
        )

    except Exception as e:
        messagebox.showerror("Error", str(e))
        print("ERROR:", e)

root = tk.Tk()
root.title("PNG to SVG to STL Converter")

label = tk.Label(root, text="Seleccioná un PNG para convertirlo a SVG y STL")
label.pack(padx=20, pady=10)

btn = tk.Button(root, text="Load Image & Convert", command=process_image)
btn.pack(padx=20, pady=20)

root.mainloop()