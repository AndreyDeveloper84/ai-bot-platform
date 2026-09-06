/**
 * «Добавить человека» — один экран вместо двух (DRF-1505).
 *
 * Маршруты: `/admin/team/add`, и прежние `/admin/team/invite` и
 * `/admin/team/access` — они никуда не делись, а выбирают ветку. Старые
 * адреса живут в рунбуках, в спеке админской поверхности и в чужих
 * экранах (`AdminServicesMatrixScreen`), и ломать их ради переезда
 * незачем.
 *
 * # Что здесь свели и почему
 *
 * Было два экрана и две кнопки на экране команды:
 *
 *   `/admin/team/invite` → создаёт карточку мастера, шлёт приглашение
 *   `/admin/team/access` → выдаёт код доступа тому, кто уже заведён
 *
 * Для бэкенда это по-прежнему два эндпоинта с разными моделями и разными
 * жизненными циклами, и там разделение остаётся правильным. Но выбирать
 * между ними должен был владелец салона — ДО того, как что-то сделал, и
 * зная то, чего он знать не обязан: заведён ли этот человек в каталоге.
 * Промахнувшись, он попадал в форму, которая просит не то.
 *
 * Решение владельца §25 п.4 от 05.09.2026: владелец салона решает одну
 * задачу — подключить человека. Поэтому экран один, а разница между
 * ветками задана вопросом на нём, в терминах, которые владелец знает
 * («его ещё нет в салоне» / «уже работает»), а не в терминах моделей.
 *
 * # Почему переключатель отдаётся ветке пропом, а не рисуется здесь
 *
 * У каждой ветки своя закреплённая панель действия и свой заголовок, и
 * обе живут в `ScreenLayout` — каркасе, который требует объявить возврат
 * (DRF-1493) и сам рисует и заголовок, и панель. Если бы каркас держал
 * контейнер, ему пришлось бы принимать заголовок, панель и вид возврата
 * снизу вверх от активной ветки: три пропа ради одного div'а.
 *
 * Поэтому наоборот: каркас держит ветка, а контейнер передаёт ей готовый
 * переключатель, который она кладёт первым в тело. Человек видит один
 * экран: заголовок, вопрос, форма под ним.
 *
 * # Чего этот экран не делает
 *
 * Не показывает переключатель на экранах успеха. Там на экране лежит
 * учётные данные, показываемые один раз, и второй способ уйти с него —
 * это способ их потерять.
 */

import { useState } from "react";
import { useLocation } from "react-router-dom";

import { AddPersonAccessCodeSection } from "./AddPersonAccessCodeSection";
import { AddPersonNewMasterSection } from "./AddPersonNewMasterSection";
import { hapticSelection } from "../../lib/max-sdk";
import type { MeResponse } from "../../lib/admin-api";

/**
 * Две ветки одной задачи.
 *
 * `new-master` — человека нет ни в каталоге, ни в боте. Создаётся
 * карточка мастера и выдаётся приглашение.
 *
 * `access-code` — человек уже есть: карточка мастера без входа, или
 * администратор/ресепшен, которых в каталоге не бывает вовсе. Выдаётся
 * код доступа.
 */
export type AddPersonTrack = "new-master" | "access-code";

interface TrackOption {
  readonly value: AddPersonTrack;
  readonly label: string;
  /** Как узнать, что это про него. Не про модели — про человека. */
  readonly effect: string;
}

export const TRACK_OPTIONS: readonly TrackOption[] = [
  {
    value: "new-master",
    label: "Новый мастер",
    effect: "Его ещё нет в салоне: заведём карточку и дадим приглашение.",
  },
  {
    value: "access-code",
    label: "Уже работает у нас",
    effect:
      "Мастер из каталога, администратор или ресепшен: выдадим код доступа.",
  },
];

interface Props {
  readonly me: MeResponse;
  /**
   * С какой ветки открыть. Задаётся маршрутом: `/admin/team/access`
   * открывает код доступа, остальные — нового мастера.
   *
   * Ветка по умолчанию — новый мастер, и это не вкусовщина: на 06.09 в
   * салоне 34 мастера, из них 4 могут войти в кабинет. Задача, ради
   * которой сюда заходят, — подключить остальных.
   */
  readonly initialTrack?: AddPersonTrack;
}

export function AdminAddPersonScreen({ me, initialTrack }: Props) {
  const location = useLocation();
  const [track, setTrack] = useState<AddPersonTrack>(
    () => initialTrack ?? trackFromPath(location.pathname),
  );

  const switcher = (
    <fieldset className="add-person__tracks">
      <legend className="add-person__legend">Кого добавляете?</legend>
      {TRACK_OPTIONS.map((opt) => (
        <label key={opt.value} className="add-person__track">
          {/*
            `aria-label` + `aria-describedby`, а не имя из `<label>`:
            иначе доступное имя становится «Новый мастер Его ещё нет в
            салоне: заведём карточку и дадим приглашение» — вариант и
            его объяснение читаются одной фразой, и скринридер
            произносит объяснение раньше, чем человек успевает
            различить варианты. Тот же приём, что у ролей кода доступа.
          */}
          <input
            type="radio"
            name="add-person-track"
            aria-label={opt.label}
            aria-describedby={`add-person-effect-${opt.value}`}
            checked={track === opt.value}
            onChange={() => {
              hapticSelection();
              setTrack(opt.value);
            }}
          />
          <span className="add-person__track-text">
            <span className="add-person__track-label">{opt.label}</span>
            <span
              className="add-person__track-effect"
              id={`add-person-effect-${opt.value}`}
            >
              {opt.effect}
            </span>
          </span>
        </label>
      ))}
    </fieldset>
  );

  return track === "access-code" ? (
    <AddPersonAccessCodeSection me={me} switcher={switcher} />
  ) : (
    <AddPersonNewMasterSection me={me} switcher={switcher} />
  );
}

/**
 * Ветка по адресу, когда её не задали пропом.
 *
 * Держится здесь, а не в таблице маршрутов, чтобы старый адрес
 * `/admin/team/access` продолжал открывать то, что открывал, даже если
 * его смонтируют ещё где-нибудь.
 */
function trackFromPath(pathname: string): AddPersonTrack {
  return pathname.endsWith("/access") ? "access-code" : "new-master";
}
