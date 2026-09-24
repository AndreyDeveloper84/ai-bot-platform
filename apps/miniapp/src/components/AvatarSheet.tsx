/**
 * Аватар справа вверху → лист разделов (DRF-2115, §50 п.6).
 *
 * Общая компонента для салонной админки и мастерской поверхности
 * (DRF-2121): принимает готовый список пунктов (`avatarSheetItemsFor` /
 * мастерский список), сама решает только как открыть, закрыть и перейти.
 * Пункты с `group: "manage"` рисуются под заголовком «Управление салоном».
 *
 * Доступность — та же, что у листа «Ещё» соло-поверхности: `role="dialog"`,
 * Escape закрывает, фокус уходит в лист и возвращается на аватар, Tab
 * заперт внутри панели, подложка закрывает по тапу.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AVATAR_SHEET_COPY, initialsOf, type AvatarSheetItem } from "../lib/avatar-sheet";

export function AvatarSheet({
  name,
  photoUrl,
  dot = false,
  items,
}: {
  /** Имя человека — для инициалов на кнопке. */
  name: string;
  /** Фото, если есть, — вместо инициалов (мастер, DRF-2121). */
  photoUrl?: string | null;
  /** Точка «есть изменения» на аватаре (например, владелец ждёт правок профиля). */
  dot?: boolean;
  items: ReadonlyArray<AvatarSheetItem>;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;
    panel.querySelector<HTMLElement>("button")?.focus();
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Tab" || !panel) return;
      const focusable = panel.querySelectorAll<HTMLElement>("button");
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      // eslint-disable-next-line react-hooks/exhaustive-deps -- совет правила здесь ломает замысел: фокус возвращается тому, кто открыл шторку, и `triggerRef.current` нужен именно на момент очистки
      triggerRef.current?.focus();
    };
  }, [open]);

  if (items.length === 0) return null;

  const go = (to: string) => {
    setOpen(false);
    navigate(to);
  };

  const manage = items.filter((i) => i.group === "manage");
  const plain = items.filter((i) => i.group !== "manage");
  const before = plain.filter((i) => i.key === "profile");
  const after = plain.filter((i) => i.key !== "profile");

  const renderItem = (it: AvatarSheetItem) => (
    <li key={it.key}>
      <button type="button" className="avatar-sheet__item" onClick={() => go(it.to)}>
        {it.label}
      </button>
    </li>
  );

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="avatar-sheet__trigger"
        aria-label={AVATAR_SHEET_COPY.trigger}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        {photoUrl ? (
          <img className="avatar-sheet__photo" src={photoUrl} alt="" />
        ) : (
          initialsOf(name)
        )}
        {dot ? <span className="avatar-sheet__dot" aria-label="есть изменения" /> : null}
      </button>
      {open ? (
        <div className="avatar-sheet" role="presentation">
          <button
            type="button"
            className="avatar-sheet__backdrop"
            aria-label={AVATAR_SHEET_COPY.close}
            onClick={close}
          />
          <div
            ref={panelRef}
            className="avatar-sheet__panel"
            role="dialog"
            aria-modal="true"
            aria-label={AVATAR_SHEET_COPY.title}
          >
            <div className="avatar-sheet__grip" aria-hidden="true" />
            <ul className="avatar-sheet__list">
              {before.map(renderItem)}
              {manage.length > 0 ? (
                <li className="avatar-sheet__group">
                  <p className="avatar-sheet__group-title">{AVATAR_SHEET_COPY.manage}</p>
                  <ul className="avatar-sheet__list avatar-sheet__list--nested">
                    {manage.map(renderItem)}
                  </ul>
                </li>
              ) : null}
              {after.map(renderItem)}
            </ul>
          </div>
        </div>
      ) : null}
    </>
  );
}
