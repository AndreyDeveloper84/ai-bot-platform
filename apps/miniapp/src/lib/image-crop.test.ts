/**
 * Кроп 1:1 — чистая геометрия (DRF-1814, часть B, макет 6.2).
 *
 * K1 — исходный квадрат при зуме 1 берётся целиком;
 * K2 — горизонтальное фото: квадрат — по меньшей стороне, по центру;
 * K3 — зум 2 берёт половину стороны из центра (окно уменьшается, не растёт);
 * K4 — поворот на 90° меняет местами ширину и высоту источника;
 * K5 — смещение не выпускает окно за край — прижимается.
 */
import { describe, expect, it } from "vitest";

import { cropWindow, normalizeRotation } from "./image-crop";

describe("cropWindow", () => {
  it("K1: квадрат при зуме 1 — весь", () => {
    expect(cropWindow({ width: 400, height: 400, zoom: 1, rotation: 0, offsetX: 0, offsetY: 0 })).toEqual({
      x: 0,
      y: 0,
      size: 400,
    });
  });

  it("K2: горизонтальное фото — по меньшей стороне, по центру", () => {
    expect(cropWindow({ width: 1200, height: 800, zoom: 1, rotation: 0, offsetX: 0, offsetY: 0 })).toEqual({
      x: 200,
      y: 0,
      size: 800,
    });
  });

  it("K3: зум 2 — половина стороны из центра", () => {
    expect(cropWindow({ width: 800, height: 800, zoom: 2, rotation: 0, offsetX: 0, offsetY: 0 })).toEqual({
      x: 200,
      y: 200,
      size: 400,
    });
  });

  it("K4: поворот 90° меняет местами стороны источника", () => {
    const rotated = cropWindow({ width: 1200, height: 800, zoom: 1, rotation: 90, offsetX: 0, offsetY: 0 });
    expect(rotated).toEqual({ x: 0, y: 200, size: 800 });
  });

  it("K5: смещение прижимается к краю, окно не выходит за фото", () => {
    expect(cropWindow({ width: 800, height: 800, zoom: 2, rotation: 0, offsetX: -999, offsetY: 999 })).toEqual({
      x: 0,
      y: 400,
      size: 400,
    });
  });
});

describe("normalizeRotation", () => {
  it("держит 0/90/180/270", () => {
    expect(normalizeRotation(360)).toBe(0);
    expect(normalizeRotation(450)).toBe(90);
    expect(normalizeRotation(-90)).toBe(270);
  });
});
