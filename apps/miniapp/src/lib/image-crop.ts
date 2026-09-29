/**
 * Кроп 1:1 для фото профиля (DRF-1814, часть B, макет 6.2).
 *
 * Геометрия отделена от canvas намеренно: `cropWindow` — чистая функция,
 * которую можно проверить числами; `renderSquareCrop` — единственное место,
 * где есть canvas, и jsdom его не рисует. Каталог принимает только квадрат
 * (±2 %, `internal_specialist_profile_api.SQUARE_TOLERANCE`), поэтому кроп —
 * не украшение, а условие приёма файла.
 *
 * Модель: окно — квадрат со стороной `min(w, h) / zoom` в координатах уже
 * повёрнутого изображения, по центру плюс смещение пользователя, прижатое
 * к краям. Поворот только кратный 90°: свободный угол дал бы пустые углы,
 * которые пришлось бы чем-то заливать.
 */

export interface CropInput {
  /** Размеры ИСХОДНОГО изображения (до поворота). */
  width: number;
  height: number;
  /** ≥ 1; 1 — максимальный квадрат, 2 — половина стороны. */
  zoom: number;
  /** 0 | 90 | 180 | 270 — по часовой. */
  rotation: number;
  /** Смещение центра окна в пикселях повёрнутого изображения. */
  offsetX: number;
  offsetY: number;
}

export interface CropWindow {
  /** Левый верхний угол в координатах ПОВЁРНУТОГО изображения. */
  x: number;
  y: number;
  size: number;
}

export const ROTATION_STEP = 90;
export const ZOOM_MIN = 1;
export const ZOOM_MAX = 3;

export function normalizeRotation(deg: number): number {
  const r = Math.round(deg / ROTATION_STEP) * ROTATION_STEP;
  return ((r % 360) + 360) % 360;
}

function rotatedSize(width: number, height: number, rotation: number): { w: number; h: number } {
  return normalizeRotation(rotation) % 180 === 0 ? { w: width, h: height } : { w: height, h: width };
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(Math.max(v, lo), hi);

export function cropWindow(input: CropInput): CropWindow {
  const { w, h } = rotatedSize(input.width, input.height, input.rotation);
  const zoom = clamp(Number.isFinite(input.zoom) ? input.zoom : 1, ZOOM_MIN, ZOOM_MAX);
  const size = Math.round(Math.min(w, h) / zoom);
  const cx = w / 2 + (input.offsetX || 0);
  const cy = h / 2 + (input.offsetY || 0);
  const x = Math.round(clamp(cx - size / 2, 0, Math.max(0, w - size)));
  const y = Math.round(clamp(cy - size / 2, 0, Math.max(0, h - size)));
  return { x, y, size };
}

/** Загрузить `File` в `HTMLImageElement` (object URL освобождается после загрузки). */
export function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("image_decode_failed"));
    };
    img.src = url;
  });
}

export interface RenderOptions {
  zoom: number;
  rotation: number;
  offsetX: number;
  offsetY: number;
  /** Сторона результата в пикселях; каталог квадрат принимает любой. */
  outputSize?: number;
  mimeType?: string;
  quality?: number;
}

/**
 * Нарисовать квадрат из изображения по окну кропа и вернуть JPEG-blob.
 *
 * Поворот применяется на canvas (`rotate`), потом окно вырезается уже из
 * повёрнутого полотна — та же система координат, что у `cropWindow`.
 */
export async function renderSquareCrop(image: HTMLImageElement, opts: RenderOptions): Promise<Blob> {
  const rotation = normalizeRotation(opts.rotation);
  const win = cropWindow({
    width: image.naturalWidth || image.width,
    height: image.naturalHeight || image.height,
    zoom: opts.zoom,
    rotation,
    offsetX: opts.offsetX,
    offsetY: opts.offsetY,
  });
  const out = opts.outputSize ?? Math.min(win.size, 1024);
  const { w: rw, h: rh } = rotatedSize(
    image.naturalWidth || image.width,
    image.naturalHeight || image.height,
    rotation,
  );

  // 1. Повёрнутое полотно целиком.
  const rotated = document.createElement("canvas");
  rotated.width = rw;
  rotated.height = rh;
  const rctx = rotated.getContext("2d");
  if (!rctx) throw new Error("canvas_unavailable");
  rctx.translate(rw / 2, rh / 2);
  rctx.rotate((rotation * Math.PI) / 180);
  rctx.drawImage(
    image,
    -(image.naturalWidth || image.width) / 2,
    -(image.naturalHeight || image.height) / 2,
  );

  // 2. Квадрат из него.
  const square = document.createElement("canvas");
  square.width = out;
  square.height = out;
  const sctx = square.getContext("2d");
  if (!sctx) throw new Error("canvas_unavailable");
  sctx.drawImage(rotated, win.x, win.y, win.size, win.size, 0, 0, out, out);

  return new Promise((resolve, reject) => {
    square.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("canvas_export_failed"))),
      opts.mimeType ?? "image/jpeg",
      opts.quality ?? 0.9,
    );
  });
}
