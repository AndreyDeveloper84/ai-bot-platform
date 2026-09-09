/**
 * Часовой пояс — вопрос человеку, а не замер устройства (DRF-1477).
 *
 * # Почему спрашиваем, а не проставляем молча
 *
 * `Intl.DateTimeFormat().resolvedOptions().timeZone` — честный ЗАМЕР
 * устройства, а не догадка. Но он врёт при VPN и в поездке, и это не
 * редкость, а норма для наших людей. Записать его молча значило бы
 * выдать замер за ответ человека — ровно §73: имя производной не
 * доказывает происхождение.
 *
 * Поэтому: определяем, **показываем видимым значением** («Москва,
 * верно?») и пишем **только по подтверждению**. Тогда у значения
 * появляется происхождение «человек согласился», а не «браузер сказал»,
 * и реестр личных полей честно называет его `USER_STATED`.
 *
 * # Почему видимое значение, а не галочка «использовать пояс устройства»
 *
 * Город человек проверить может, механизм — нет. Согласие с механизмом,
 * который не проверить, согласием не является.
 *
 * # Отказ ведёт к выбору, а не к пустоте
 *
 * «Нет, другой» открывает список, а не возвращает в то же состояние,
 * из которого вышли. Молчаливая пустота после отказа — это вопрос,
 * заданный впустую.
 *
 * # Что видно рядом
 *
 * Лист живёт на экране профиля клиента, где рядом — имя, согласия и
 * отзыв хранения данных. Ничего про здоровье и питание здесь нет и не
 * появляется: пояс к особой категории не относится, и подмешивать к
 * нему соседей нельзя (урок `help_text` на карточке оператора).
 */
import { useCallback, useEffect, useState } from "react";

import { SheetChrome } from "./PersonalDataSheets";

/**
 * Пояса, из которых можно выбрать вручную.
 *
 * Список закрытый и короткий намеренно: полный перечень IANA — это
 * шестьсот строк, в которых человек ищет свой город дольше, чем пишет
 * его в чат. Здесь одиннадцать зон России плюс соседние столицы, где
 * живут наши люди. Не нашлось своего — остаётся честное «не задавать»,
 * а не «выбрать похожее».
 */
export const TIMEZONE_CHOICES: ReadonlyArray<{ zone: string; label: string }> = [
  { zone: "Europe/Kaliningrad", label: "Калининград" },
  { zone: "Europe/Moscow", label: "Москва" },
  { zone: "Europe/Samara", label: "Самара" },
  { zone: "Asia/Yekaterinburg", label: "Екатеринбург" },
  { zone: "Asia/Omsk", label: "Омск" },
  { zone: "Asia/Krasnoyarsk", label: "Красноярск" },
  { zone: "Asia/Irkutsk", label: "Иркутск" },
  { zone: "Asia/Yakutsk", label: "Якутск" },
  { zone: "Asia/Vladivostok", label: "Владивосток" },
  { zone: "Asia/Magadan", label: "Магадан" },
  { zone: "Asia/Kamchatka", label: "Камчатка" },
  { zone: "Asia/Almaty", label: "Алматы" },
  { zone: "Asia/Tbilisi", label: "Тбилиси" },
  { zone: "Asia/Yerevan", label: "Ереван" },
];

/** Человеческое имя зоны, если она нам знакома; иначе сама зона. */
export function zoneLabel(zone: string): string {
  return TIMEZONE_CHOICES.find((c) => c.zone === zone)?.label ?? zone;
}

/**
 * Что говорит устройство. `null` — не сказало ничего: старый браузер
 * либо приватный режим. Тогда предлагать нечего, и лист сразу
 * открывается списком, а не вопросом про пустоту.
 */
export function detectZone(): string | null {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof zone === "string" && zone.length > 0 ? zone : null;
  } catch {
    return null;
  }
}

type View = "suggest" | "choose" | "busy" | "error";

interface Props {
  open: boolean;
  triggerRef: React.RefObject<HTMLButtonElement>;
  onClose: () => void;
  /** Пояс, который уже стоит; пусто — «не задан» (DRF-1606). */
  current: string;
  /** Записать выбранный пояс. Ошибку бросает — лист её показывает. */
  onSave: (zone: string) => Promise<void>;
}

export function TimezoneSheet({ open, triggerRef, onClose, current, onSave }: Props) {
  const detected = detectZone();
  // Спрашивать «Москва, верно?» у того, у кого уже стоит Москва, незачем:
  // предложение имеет смысл, только пока ответа нет либо он расходится
  // с устройством.
  const worthSuggesting = detected !== null && detected !== current;
  const [view, setView] = useState<View>(worthSuggesting ? "suggest" : "choose");

  useEffect(() => {
    if (open) setView(worthSuggesting ? "suggest" : "choose");
  }, [open, worthSuggesting]);

  const save = useCallback(
    async (zone: string) => {
      setView("busy");
      try {
        await onSave(zone);
        onClose();
      } catch {
        // Не подставляем и не «почти сохранили»: человек должен видеть,
        // что ответа не записали, иначе он уйдёт уверенным в обратном.
        setView("error");
      }
    },
    [onSave, onClose],
  );

  if (!open) return null;
  const busy = view === "busy";

  return (
    <SheetChrome
      headlineId="timezone-sheet-headline"
      headline="Часовой пояс"
      closeDisabled={busy}
      triggerRef={triggerRef}
      onClose={onClose}
    >
      {view === "suggest" && detected !== null && (
        <>
          <p className="profile-support-sheet__body">
            Похоже, ты в зоне <strong>{zoneLabel(detected)}</strong>. Верно?
          </p>
          <p className="profile-support-sheet__body">
            По нему я считаю время в напоминаниях. Если ты в поездке или
            пользуешься VPN, лучше выбрать вручную.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-secondary profile-support-sheet__cancel"
              onClick={() => setView("choose")}
            >
              Нет, другой
            </button>
            <button
              type="button"
              data-initial-focus
              className="btn-primary"
              onClick={() => save(detected)}
            >
              Да, верно
            </button>
          </div>
        </>
      )}

      {view === "choose" && (
        <>
          <p className="profile-support-sheet__body">
            {detected === null
              ? "Не удалось определить пояс автоматически — выбери свой."
              : "Выбери свой пояс."}
          </p>
          <ul className="timezone-sheet__list">
            {TIMEZONE_CHOICES.map(({ zone, label }) => (
              <li key={zone}>
                <button
                  type="button"
                  className="timezone-sheet__option"
                  aria-current={zone === current ? "true" : undefined}
                  onClick={() => save(zone)}
                >
                  {label}
                </button>
              </li>
            ))}
          </ul>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
          </div>
        </>
      )}

      {busy && <p className="profile-support-sheet__body">Сохраняю…</p>}

      {view === "error" && (
        <>
          <p className="profile-support-sheet__body">
            Не получилось сохранить пояс. Он остался прежним.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Закрыть
            </button>
            <button
              type="button"
              data-initial-focus
              className="btn-primary"
              onClick={() => setView(worthSuggesting ? "suggest" : "choose")}
            >
              Попробовать снова
            </button>
          </div>
        </>
      )}
    </SheetChrome>
  );
}
