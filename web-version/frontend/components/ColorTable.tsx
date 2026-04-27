"use client";

export type ColorRow = {
  label_id: number;
  hex_value: string;
  rgb: [number, number, number];
  pixel_count: number;
  export_default: boolean;
  note: string;
  export: boolean;
  z_offset: number;
  thickness: number;
};

type Props = {
  colors: ColorRow[];
  onChange: (colors: ColorRow[]) => void;
};

export function ColorTable({ colors, onChange }: Props) {
  function patch(index: number, value: Partial<ColorRow>) {
    onChange(colors.map((row, rowIndex) => (rowIndex === index ? { ...row, ...value } : row)));
  }

  if (colors.length === 0) {
    return <div className="placeholder">Detecta colores para ver la tabla.</div>;
  }

  return (
    <div className="tableWrap">
      <table>
        <thead>
          <tr>
            <th>Color</th>
            <th>Hex</th>
            <th>Exportar</th>
            <th>Z offset mm</th>
            <th>Grosor mm</th>
            <th>Info</th>
          </tr>
        </thead>
        <tbody>
          {colors.map((color, index) => (
            <tr key={color.label_id}>
              <td>
                <div className="swatch" style={{ background: color.hex_value }} />
              </td>
              <td>{color.hex_value}</td>
              <td>
                <input
                  type="checkbox"
                  checked={color.export}
                  onChange={(event) => patch(index, { export: event.target.checked })}
                />
              </td>
              <td>
                <input
                  className="numeric"
                  type="number"
                  min="0"
                  step="0.1"
                  value={color.z_offset}
                  onChange={(event) => patch(index, { z_offset: Number(event.target.value) })}
                />
              </td>
              <td>
                <input
                  className="numeric"
                  type="number"
                  min="0.01"
                  step="0.1"
                  value={color.thickness}
                  onChange={(event) => patch(index, { thickness: Number(event.target.value) })}
                />
              </td>
              <td>{color.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
