/**
 * Слово «оператор» уходит с экранов мастера; адресат — студия (DRF-2378).
 *
 * Решение владельца (§77 п. 27 каноничного реестра, 24.09): **роли
 * «оператор» в коде не существует** — ни группы, ни роли, ни поля. Одним
 * словом названы три разные вещи: группа `Ayla Operations`, флаг
 * `User.is_platform_admin` в каталоге (это НЕ право привязки) и пустой по
 * умолчанию `HANDOFF_DUTY_OPERATORS` (он вообще про клиентские передачи).
 * Роли салона — `receptionist / admin / owner / master`. Значит «обратитесь
 * к оператору» отправляет мастера к адресату, которого нет.
 *
 * Адресат на экране — **студия**: чат существует (`/master/internal-chat`,
 * заголовок «Со студией»), и это настоящий получатель.
 *
 * ЧТО ЗДЕСЬ НЕ ДЕЛАЕТСЯ, И ЭТО РЕШЕНИЕ:
 *
 * * **внутри слово остаётся.** Провенанс `OPERATOR_VERIFIED` различает
 *   «подтвердил человек» и «проставило правило» — его не трогаем;
 * * **утверждённый текст ожидания** («Настраиваю профиль — обычно это
 *   занимает пару минут.») здесь НЕ ставится ни на один экран. Состояния
 *   «привязка идёт» сегодня не существует: у экранов один признак —
 *   403 `not_linked`, а привязку никто не делает автоматически. Обещать
 *   «пару минут» за работу, которой никто не начал, — ровно то, что
 *   запрещает граница листа. Текст станет верным вместе с авто-привязкой
 *   (DRF-2379), и тогда ему найдётся состояние;
 * * **тишина студии** (`apps/internal_chat/notify.py`) в этом заходе не
 *   чинится: прежний запасной путь снят решением владельца 07.09 —
 *   общий канал не несёт тенанта, и переписка всех салонов сходилась в
 *   один диалог. Чем чинить, решает владелец; вопрос у него.
 *
 * ПРЕДЕЛЫ ЭТОГО СТОРОЖА, названные, а не спрятанные:
 *
 * * читаются только литералы в кавычках. **Голый текст JSX**
 *   (`<p>Напишите оператору</p>`) сторожу не виден — путь живой, такой
 *   текст в этих экранах есть (например `×` в карточке профиля);
 * * строки, собранные в рантайме из кусков, тоже не видны: сторож читает
 *   исходник, а не результат;
 * * серверные строки живут под своим сторожем
 *   (`apps/master_api/tests/test_operator_word_guard_2378.py`) — у него
 *   свой охват и свои названные пределы.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return { ...original, getWorkingHours: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { ApiError } from "../lib/api";
import { getWorkingHours } from "../lib/master-api";
import {
  MasterWorkingHoursScreen,
  NOT_LINKED_MESSAGE,
} from "./MasterWorkingHoursScreen";
import { PROFILE_COPY } from "./MasterProfileScreen";
import { SELECT_COPY } from "./MasterServiceSelectScreen";
import { DIRECTIONS_COPY } from "./MasterDirectionsScreen";
import { PLACE_COPY } from "./MasterPlaceScreen";
import { WRITE_TO_STUDIO_LABEL } from "../components/StudioCallout";
import { stringLiteralsOf } from "../testing/string-literals";

/**
 * ВЕСЬ `src`, а не экраны мастера.
 *
 * Сперва здесь стояло `./Master*.tsx` — и сторож не видел файла, который
 * этот же лист и создал: утверждённые строки уехали в
 * `components/StudioCallout.tsx`, а экраны держат только половину про
 * предмет. Слово в `NOT_CONNECTED_PREFIX` прошло бы мимо обоих сторожей и
 * встало бы разом на шесть экранов (найдено ревью). Сторож по признаку,
 * сузившийся до каталога, — это сторож по списку, только незаметнее.
 */
const SOURCES = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/**
 * Слово в любом падеже и числе, отдельным словом, в любом регистре.
 *
 * `i` — потому что `[Оо]ператор` пропускал «ОПЕРАТОР» заглавными (найдено
 * ревью). Латинского `OPERATOR_VERIFIED` это не касается: шаблон
 * кириллический, и провенанс остаётся невидимым для сторожа.
 */
const OPERATOR_RE = /(?<![А-Яа-яЁё])оператор[а-яё]*(?![А-Яа-яЁё])/iu;

/**
 * Экраны, где слово ещё стоит и снимать его НЕЛЬЗЯ без слова владельца.
 *
 * Оба говорят про подтверждение ЛИЧНОСТИ, а не про привязку профиля,
 * поэтому в список из семи адресов не попали, и утверждённые формулировки
 * листа (они написаны под привязку) сюда не годятся. Замер нашёл их сверх
 * листа; вопрос владельцу задан. Свой текст не придумываем — это было бы
 * решением, выданным за правку.
 *
 * Список ЗАКРЫТЫЙ: появится новый экран со словом — сторож покраснеет, а
 * не промолчит. Придёт ответ владельца — строки уйдут отсюда, и список
 * опустеет.
 */
// Ключи приходят нормализованными относительно этого файла: соседи по
// каталогу — «./Имя», остальные — «../каталог/Имя».
const PENDING_OWNER_WORD = [
  "./MasterPublicationScreen.tsx",
  "./MasterSetupLandingScreen.tsx",
];

describe("Сторож класса: на экранах мастера нет слова, за которым нет роли (DRF-2378)", () => {
  const withWord = Object.entries(SOURCES)
    .filter(([path]) => !path.endsWith(".test.tsx"))
    .filter(([, src]) => stringLiteralsOf(src).some((text) => OPERATOR_RE.test(text)))
    .map(([path]) => path)
    .sort();

  it("слова нет ни в одной пользовательской строке — кроме названных вслух", () => {
    // Присутствие первым: файлы прочитаны, и их немало. Без этого пустой
    // `SOURCES` (опечатка в шаблоне) дал бы зелёный на ровном месте.
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100);
    // И охват именно широкий: в переписи есть не только экраны мастера.
    expect(Object.keys(SOURCES).some((p) => p.includes("/components/"))).toBe(true);
    expect(Object.keys(SOURCES).some((p) => p.includes("/lib/"))).toBe(true);
    expect(withWord).toEqual([...PENDING_OWNER_WORD].sort());
  });

  it("сторож умеет падать: подложенная строка со словом ловится", () => {
    const fake = 'const COPY = { hint: "Привязку выполнит оператор." };';
    expect(stringLiteralsOf(fake).some((t) => OPERATOR_RE.test(t))).toBe(true);
  });

  it("внутреннее слово не ловится: провенанс не является пользовательской строкой", () => {
    // `OPERATOR_VERIFIED` — латиница, шаблон её не берёт. Узел стоит,
    // чтобы правка шаблона «на всякий случай» не утащила провенанс.
    expect(OPERATOR_RE.test("OPERATOR_VERIFIED")).toBe(false);
  });
});

/**
 * Все утверждённые строки — под узлом поимённо.
 *
 * Прежде проверялись две из шести: остальные четыре можно было молча
 * переписать (найдено ревью). А ведь спорная половина — именно вторая, та,
 * что меняется по предмету экрана: первая половина общая и защищена сама
 * собой, потому что физически одна.
 */
describe("Утверждённые тексты — поимённо (DRF-2378)", () => {
  it.each([
    ["Рабочий график", NOT_LINKED_MESSAGE, "Профиль пока не подключён — сохранить часы некуда."],
    ["Профиль", PROFILE_COPY.notLinked, "Профиль пока не подключён — фото и текст пока не изменить."],
    ["Выбор услуг", SELECT_COPY.notLinked, "Профиль пока не подключён — выбрать услуги некуда."],
    ["Направления", DIRECTIONS_COPY.notLinked, "Профиль пока не подключён — направления пока не выбрать."],
    ["Место работы", PLACE_COPY.notLinked, "Профиль пока не подключён — сохранить место некуда."],
    [
      "Место работы, отказ сервера",
      PLACE_COPY.refusal.no_workspace_tenant,
      "Профиль пока не подключён — указать место работы некуда.",
    ],
  ])("%s говорит ровно утверждённое", (_screen, actual, expected) => {
    expect(actual).toBe(expected);
  });

  it("первая половина у всех одна и та же — не «почти такая же»", () => {
    const halves = [
      NOT_LINKED_MESSAGE,
      PROFILE_COPY.notLinked,
      SELECT_COPY.notLinked,
      DIRECTIONS_COPY.notLinked,
      PLACE_COPY.notLinked,
      PLACE_COPY.refusal.no_workspace_tenant,
      // `refusal` типизирован как `Record<string, string>`, поэтому по
      // ключу приходит `string | undefined` — пустая строка здесь не
      // маскировка, а видимый провал: множество половин станет больше
      // одной, и узел покраснеет.
    ].map((t) => (t ?? "").split(" — ")[0] ?? "");
    expect(new Set(halves).size).toBe(1);
    expect(halves[0]).toBe("Профиль пока не подключён");
  });
});

function renderHours() {
  return render(
    <MemoryRouter initialEntries={["/solo/working-hours"]}>
      <Routes>
        <Route path="/solo/working-hours" element={<MasterWorkingHoursScreen />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname}</div>;
}

describe("Непривязанный профиль: текст владельца и дверь к студии (DRF-2378)", () => {
  beforeEach(() => {
    vi.mocked(getWorkingHours).mockRejectedValue(
      new ApiError(403, "not_linked", "not_linked"),
    );
  });

  it("говорит словами владельца — дословно, без «попробуйте ещё раз»", async () => {
    expect(NOT_LINKED_MESSAGE).toBe(
      "Профиль пока не подключён — сохранить часы некуда.",
    );
    renderHours();
    expect(await screen.findByText(NOT_LINKED_MESSAGE)).toBeInTheDocument();
    // «Попробовать снова» здесь запрещено решением: повтор не лечит
    // отсутствие привязки.
    expect(screen.queryByRole("button", { name: /снова|ещё раз/i })).toBeNull();
  });

  it("ведёт в существующий чат со студией, а не в новую сущность", async () => {
    renderHours();
    const btn = await screen.findByRole("button", { name: WRITE_TO_STUDIO_LABEL });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent("/master/internal-chat"),
    );
  });

  it("не обещает, что студия подключит профиль", async () => {
    renderHours();
    expect(await screen.findByText(NOT_LINKED_MESSAGE)).toBeInTheDocument();
    // Граница листа: сегодня студия этого НЕ МОЖЕТ — привязка станет
    // автоматической отдельным листом (DRF-2379). Право на обещание
    // появляется вместе с автоматикой, не раньше.
    const shown = document.body.textContent ?? "";
    expect(shown).not.toMatch(/подключ(ит|им|ат)|привяж|сдела(ет|ют)/i);
    expect(shown).not.toMatch(/пару минут/i);
  });
});
