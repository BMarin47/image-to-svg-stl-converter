entoimport tkinter as tk
from tkinter import filedialog, messagebox
import cv2
import svgwrite
import os

def convert_to_svg(image_path):
    # 1. Load the image using OpenCV
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError("Could not load image.")

    # 2. Convert the image to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 3. Convert to black and white (binary)
    # Using Otsu's thresholding to automatically find the best threshold
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 4. Find the main contours (outlines) of the black and white image
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Prepare SVG output path based on the original image name
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    output_path = f"{base_name}_vectorized.svg"

    # 5. Create SVG drawing
    height, width = img.shape[:2]
    dwg = svgwrite.Drawing(output_path, size=(width, height), profile='tiny')

    # 6. Add contours as polygons to the SVG
    for contour in contours:
        # Convert contour points to SVG format
        points = []
        for point in contour:
            x, y = point[0]
            points.append((int(x), int(y)))
        
        # Only draw polygons with at least 3 points
        if len(points) > 2:
            dwg.add(dwg.polygon(points, fill='black'))

    # Save the SVG file
    dwg.save()
    return output_path

def select_image():
    # Open a file dialog to select an image
    file_path = filedialog.askopenfilename(
        title="Select an Image",
        filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.bmp")]
    )
    
    if file_path:
        try:
            output_file = convert_to_svg(file_path)
            messagebox.showinfo("Success", f"Converted successfully!\nSaved as: {output_file}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to convert image:\n{str(e)}")

# --- Setup the User Interface ---
root = tk.Tk()
root.title("Image to SVG Converter")
root.geometry("350x150")

# Label with instructions
label = tk.Label(root, text="Select a logo image to convert to SVG", pady=15, font=("Arial", 10))
label.pack()

# Button to trigger the process
button = tk.Button(root, text="Load Image & Convert", command=select_image, padx=10, pady=10)
button.pack()

# Start the application loop
root.mainloop()
