/**
 * Кроп-редактор 1:1 для фото профиля (DRF-1814, часть B, макет 6.2).
 *
 * Камера/галерея выбираются снаружи (`<input type=file>` с `capture` и без);
 * сюда приходит уже выбранный `File`. Здесь: предпросмотр с квадратной
 * рамкой, зум (ползунок), поворот на 90°, «Заменить» (открыть выбор снова),
 * «Сохранить» → `onApply(blob)`.
 *
 * Геометрия и canvas — в `lib/image-crop`; компонент только собирает
 * параметры и не знает, как рисуется квадрат. Это позволяет проверять экран
 * без canvas (jsdom его не имеет): тест подменяет `renderSquareCrop` и
 * смотрит на аргументы.
 */

import { useEffect, useMemo, useState } from "react";

import {
  ROTATION_STEP,
  ZOOM_MAX,
  ZOOM_MIN,
  loadImage,
  normalizeRotation,
  renderSquareCrop,
} from "../lib/image-crop";

export const CROP_COPY = {
  title: "Обрежьте фото",
  hint: "Квадрат — так фото увидят клиенты. Двигайте ползунок, чтобы приблизить.",
  zoom: "Приближение",
  rotate: "Повернуть",
  replace: "Заменить",
  apply: "Сохранить",
  cancel: "Отмена",
  decodeError: "Не получилось открыть это фото. Попробуйте другое.",
  renderError: "Не получилось обрезать фото. Попробуйте ещё раз.",
};

interface Props {
  file: File;
  onApply: (square: Blob) => void;
  onReplace: () => void;
  onCancel: () => void;
  busy?: boolean;
}

export function PhotoCropSheet({ file, onApply, onReplace, onCancel, busy }: Props) {
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [err, setErr] = useState("");
  const [rendering, setRendering] = useState(false);

  useEffect(() => {
    let alive = true;
    setErr("");
    setImage(null);
    loadImage(file)
      .then((img) => {
        if (alive) setImage(img);
      })
      .catch(() => {
        if (alive) setErr(CROP_COPY.decodeError);
      });
    return () => {
      alive = false;
    };
  }, [file]);

  const previewUrl = useMemo(() => URL.createObjectURL(file), [file]);
  useEffect(() => () => URL.revokeObjectURL(previewUrl), [previewUrl]);

  const apply = async () => {
    if (!image || rendering) return;
    setRendering(true);
    setErr("");
    try {
      const blob = await renderSquareCrop(image, { zoom, rotation, offsetX: 0, offsetY: 0 });
      onApply(blob);
    } catch {
      setErr(CROP_COPY.renderError);
    } finally {
      setRendering(false);
    }
  };

  const disabled = busy || rendering || !image;

  return (
    <div className="master-profile__sheet" role="dialog" aria-modal="true" aria-label={CROP_COPY.title}>
      <div className="master-profile__sheet-card">
        <h3 className="master-profile__sheet-title">{CROP_COPY.title}</h3>
        <p className="master-profile__hint">{CROP_COPY.hint}</p>

        <div className="photo-crop__frame" aria-hidden="true">
          <img
            className="photo-crop__image"
            src={previewUrl}
            alt=""
            style={{ transform: `rotate(${rotation}deg) scale(${zoom})` }}
          />
        </div>

        <label className="photo-crop__zoom">
          <span>{CROP_COPY.zoom}</span>
          <input
            type="range"
            min={ZOOM_MIN}
            max={ZOOM_MAX}
            step={0.1}
            value={zoom}
            aria-label={CROP_COPY.zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
            disabled={disabled}
          />
        </label>

        {err ? (
          <p className="master-profile__error" role="alert">
            {err}
          </p>
        ) : null}

        <div className="master-profile__sheet-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setRotation((r) => normalizeRotation(r + ROTATION_STEP))}
            disabled={disabled}
          >
            {CROP_COPY.rotate}
          </button>
          <button type="button" className="btn-secondary" onClick={onReplace} disabled={busy || rendering}>
            {CROP_COPY.replace}
          </button>
          <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy || rendering}>
            {CROP_COPY.cancel}
          </button>
          <button type="button" className="btn-primary" onClick={() => void apply()} disabled={disabled}>
            {rendering || busy ? "Сохраняем…" : CROP_COPY.apply}
          </button>
        </div>
      </div>
    </div>
  );
}
