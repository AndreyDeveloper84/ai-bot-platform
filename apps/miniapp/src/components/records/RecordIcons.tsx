/**
 * Значки раздела клиента: «Записи» (макет «Записи.png», DRF-2952), дневник питания
 * (DRF-2954), Главная (DRF-2950).
 *
 * Линейные, цвет — `currentColor`: значок берёт цвет строки, в которой стоит,
 * и не несёт собственного. Все декоративные (`aria-hidden`) — смысл строки
 * всегда записан текстом рядом.
 */
import type { ReactNode } from "react";

export type RecordIconName =
  | "back"
  | "chevron"
  | "calendar"
  | "clock"
  | "pin"
  | "price"
  | "status"
  | "star"
  | "repeat"
  | "details"
  | "ayla"
  | "info"
  | "check"
  | "alert"
  | "offline"
  | "lock"
  | "gone"
  | "bowl"
  | "camera"
  | "text"
  | "image"
  | "plus";

const PATHS: Record<RecordIconName, ReactNode> = {
  back: <path d="M15 5l-7 7 7 7" />,
  chevron: <path d="M9 5l7 7-7 7" />,
  calendar: (
    <>
      <rect x="3.5" y="5" width="17" height="15.5" rx="3" />
      <path d="M3.5 10h17M8 3v4M16 3v4" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  pin: (
    <>
      <path d="M12 21s6.5-6 6.5-11a6.5 6.5 0 10-13 0c0 5 6.5 11 6.5 11z" />
      <circle cx="12" cy="10" r="2.3" />
    </>
  ),
  price: (
    <>
      <rect x="3" y="6" width="18" height="12.5" rx="3" />
      <path d="M3 10.5h18M7 15h3" />
    </>
  ),
  status: (
    <>
      <path d="M5 5.5h14a2 2 0 012 2v7a2 2 0 01-2 2h-7l-4.5 3.5V16.5H5a2 2 0 01-2-2v-7a2 2 0 012-2z" />
      <circle cx="12" cy="11" r="1.2" />
    </>
  ),
  star: <path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.8 6.8 19.6l1-5.8-4.3-4.1 5.9-.8z" />,
  repeat: (
    <>
      <path d="M4 12a8 8 0 0113.7-5.6L20 8.5" />
      <path d="M20 4v4.5h-4.5M20 12a8 8 0 01-13.7 5.6L4 15.5" />
      <path d="M4 20v-4.5h4.5" />
    </>
  ),
  details: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="3.5" />
      <path d="M8 9h8M8 12.5h8M8 16h5" />
    </>
  ),
  ayla: (
    <>
      <path d="M5 5.5h14a2 2 0 012 2v7a2 2 0 01-2 2h-7l-4.5 3.5V16.5H5a2 2 0 01-2-2v-7a2 2 0 012-2z" />
      <path d="M12 8.2l.9 1.9 1.9.9-1.9.9-.9 1.9-.9-1.9-1.9-.9 1.9-.9z" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5.5M12 7.6v.2" />
    </>
  ),
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  alert: (
    <>
      <path d="M12 4.2l8.8 15.3H3.2z" />
      <path d="M12 10v4.5M12 17.2v.2" />
    </>
  ),
  offline: (
    <>
      <path d="M3 9.5a14 14 0 0118 0M6 13a9.5 9.5 0 0112 0M9.2 16.4a4.5 4.5 0 015.6 0" />
      <path d="M12 19.6v.2M5 4l14 16" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="10" rx="2.5" />
      <path d="M8 10.5V8a4 4 0 018 0v2.5M12 14.5v2.5" />
    </>
  ),
  gone: (
    <>
      <rect x="3.5" y="5" width="17" height="15.5" rx="3" />
      <path d="M3.5 10h17M9.5 13.5l5 4M14.5 13.5l-5 4" />
    </>
  ),
  // Дневник питания (DRF-2954): запись без снимка, вход «фото», вход «текст».
  bowl: (
    <>
      <path d="M3.5 11h17a8.5 8.5 0 01-17 0z" />
      <path d="M9 19.5h6M14.5 7.5l4-4" />
    </>
  ),
  camera: (
    <>
      <path d="M4 8.5h3l1.6-2.5h6.8L17 8.5h3a1 1 0 011 1V18a1.5 1.5 0 01-1.5 1.5h-15A1.5 1.5 0 013 18V9.5a1 1 0 011-1z" />
      <circle cx="12" cy="13.5" r="3.5" />
    </>
  ),
  text: (
    <>
      <rect x="4.5" y="3.5" width="15" height="17" rx="2.5" />
      <path d="M8 8.5h8M8 12h8M8 15.5h5" />
    </>
  ),
  image: (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
      <circle cx="9" cy="9.5" r="1.6" />
      <path d="M4 17.5l5-5 3.5 3.5 2.5-2.5 5 5" />
    </>
  ),
  // DRF-2950: «+» у стакана воды на Главной (строка плана и кнопка шага).
  plus: <path d="M12 5v14M5 12h14" />,
};

export function RecordIcon({ name, size = 20 }: { name: RecordIconName; size?: number }) {
  return (
    <svg
      className="cr-icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
