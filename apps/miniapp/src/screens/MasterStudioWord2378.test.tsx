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
  WRITE_TO_STUDIO_LABEL,
} from "./MasterWorkingHoursScreen";
import { stringLiteralsOf } from "../no-person-names.guard.test";

const SOURCES = import.meta.glob("./Master*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Слово в любом падеже и числе, с заглавной или без, отдельным словом. */
const OPERATOR_RE = /(?<![А-Яа-яЁё])[Оо]ператор[а-яё]*(?![А-Яа-яЁё])/u;

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
const PENDING_OWNER_WORD = ["./MasterPublicationScreen.tsx", "./MasterSetupLandingScreen.tsx"];

describe("Сторож класса: на экранах мастера нет слова, за которым нет роли (DRF-2378)", () => {
  const withWord = Object.entries(SOURCES)
    .filter(([path]) => !path.endsWith(".test.tsx"))
    .filter(([, src]) => stringLiteralsOf(src).some((text) => OPERATOR_RE.test(text)))
    .map(([path]) => path)
    .sort();

  it("слова нет ни в одной пользовательской строке — кроме названных вслух", () => {
    // Присутствие первым: файлы прочитаны, и их немало. Без этого пустой
    // `SOURCES` (опечатка в шаблоне) дал бы зелёный на ровном месте.
    expect(Object.keys(SOURCES).length).toBeGreaterThan(10);
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
