/**
 * Есть ли сеть — одним ответом на всё приложение.
 *
 * До этого хука состояние сети читали два экрана, каждый своей копией
 * (`CustomerRecordsScreen`, `CustomerWellnessDashboardScreen`), а вся
 * воронка записи не читала его вовсе: человек без сети доходил до
 * «Записаться», жал и получал ошибку сети вместо записи.
 *
 * `navigator.onLine` честен ровно в одну сторону: `false` означает «сети
 * точно нет», `true` — «интерфейс поднят», а не «интернет работает».
 * Поэтому хук используется, чтобы ЗАПРЕЩАТЬ действие при `false`, и
 * никогда — чтобы обещать успех при `true`.
 */
import { useEffect, useState } from "react";

export function isOnline(): boolean {
  if (typeof navigator === "undefined") return true;
  return navigator.onLine !== false;
}

export function useOnline(): boolean {
  const [online, setOnline] = useState<boolean>(isOnline);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onOnline = () => setOnline(true);
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);
  return online;
}
