"use client";

import qrcode from "qrcode-generator";
import { useMemo } from "react";

/**
 * A QR code drawn in the page. Made here, not by an outside service: the
 * family-number link carries a one-time code.
 *
 * Always black on white, in dark mode too: phone cameras read dark modules
 * on a light ground, and some cannot read the inverse.
 */
export function QrCode({
  value,
  label,
  size = 168,
}: {
  value: string;
  label: string;
  size?: number;
}) {
  const { path, extent } = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(value);
    qr.make();
    const count = qr.getModuleCount();
    const quiet = 4;
    let d = "";
    for (let row = 0; row < count; row++) {
      for (let col = 0; col < count; col++) {
        if (qr.isDark(row, col)) d += `M${col + quiet} ${row + quiet}h1v1h-1z`;
      }
    }
    return { path: d, extent: count + quiet * 2 };
  }, [value]);

  return (
    <svg
      role="img"
      aria-label={label}
      width={size}
      height={size}
      viewBox={`0 0 ${extent} ${extent}`}
      shapeRendering="crispEdges"
      className="shrink-0 rounded-md"
    >
      <rect width={extent} height={extent} fill="#ffffff" />
      <path d={path} fill="#000000" />
    </svg>
  );
}
