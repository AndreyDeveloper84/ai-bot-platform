import type { ImgHTMLAttributes, ReactNode } from "react";

import { useMasterPhoto } from "../hooks/useMasterPhoto";

/**
 * DRF-2539 — картинка мастера (фото или работа портфолио) через прокси бота.
 *
 * `src` — значение с провода (`photo_url` / `image_url`), не адрес картинки:
 * байты берутся `fetch` с подписью (`lib/master-photo.ts`). Пока грузится,
 * фото нет или прокси отказал — рисуется `fallback` (инициалы). Компонент,
 * а не хук на месте: читатели рисуют фото внутри `map`.
 */
export function MasterPhoto({
  src,
  alt,
  fallback = null,
  ...img
}: Omit<ImgHTMLAttributes<HTMLImageElement>, "src" | "alt"> & {
  src: string | null | undefined;
  alt: string;
  fallback?: ReactNode;
}) {
  const blob = useMasterPhoto(src);
  return blob ? <img {...img} src={blob} alt={alt} /> : <>{fallback}</>;
}
