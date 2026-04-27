import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Image to SVG/STL Converter Web",
  description: "Convierte imágenes en STL por color para Bambu Studio"
};

export default function RootLayout({
  children
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
