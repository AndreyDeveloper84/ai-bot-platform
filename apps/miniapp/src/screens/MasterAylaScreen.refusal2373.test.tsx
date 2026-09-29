/**
 * Отказ подтверждения: обречённая кнопка исчезает, живая остаётся (DRF-2373).
 *
 * ### Что было сломано
 *
 * `AylaChat` снимал карточку подтверждения **только в ветви успеха**. При
 * отказе она оставалась со всеми кнопками — включая «Отправить заявку».
 *
 * Для устаревшего, нечитаемого или чужого талона это ловушка: его аргументы
 * лежат внутри подписи, поэтому второй нажим пошлёт ровно то же самое и
 * получит ровно тот же отказ. Человеку был виден **единственный обречённый
 * выход**. Это не отсутствие выхода, а нарисованный выход, которого нет.
 *
 * ### Почему подменяется `fetch`, а не `master-api`
 *
 * Соседний `MasterAylaScreen.test.tsx` подменяет функции клиента — и для
 * своего предмета правильно. Здесь так нельзя: предмет — **провод**.
 *
 * Живучесть едет в `details.retriable`, а `master-api.ts` до этого листа
 * конструировал `ApiError` **без** `details` и ронял поле целиком. Узел на
 * подменённой функции этого бы не увидел: он вернул бы заранее собранный
 * `ApiError`, минуя разбор тела. Правка «работала» бы в функции и не
 * доезжала наружу — ровно то, на чём мы обожглись в DRF-2362.
 *
 * Поэтому здесь настоящий не-2xx с настоящим телом, а `master-api.ts` и
 * `ApiError` работают как в бою.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MasterAylaScreen } from "./MasterAylaScreen";

const CONFIRM_LABEL = "Отправить заявку";
const PROPOSAL = {
  action: "block_time",
  summary:
    "Собираюсь отправить администратору заявку на нерабочее время: " +
    "12 сентября, 09:00–18:00. Пока вы не подтвердите, ничего не меняется.",
  confirm_label: CONFIRM_LABEL,
  token: "signed-token-1",
  expires_in_sec: 900,
};

const EXPIRED_DETAIL = "подтверждение устарело — спросите заново";
const BUSY_DETAIL = "это время занято";

const fetchMock = vi.fn();

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Ответы по адресу; `confirm` задаётся отдельно каждым узлом. */
function route(confirmResponse: () => Response) {
  return (url: string) => {
    if (url.includes("/assistant/history")) {
      return Promise.resolve(jsonResponse(200, { messages: [] }));
    }
    if (url.includes("/assistant/context")) {
      // Форма настоящая: стартовый экран разыменовывает `today` без
      // проверки, и `null` уронил бы его ещё до предмета узла.
      return Promise.resolve(
        jsonResponse(200, {
          today: { date: "2026-09-24", count: 0, next: null },
          chips: [],
          chip_hints: {},
        }),
      );
    }
    if (url.includes("/assistant/ask")) {
      return Promise.resolve(
        jsonResponse(200, {
          answer: PROPOSAL.summary,
          tool: "block_time",
          pending_action: PROPOSAL,
          cards: [],
          message_id: "m1",
        }),
      );
    }
    if (url.includes("/assistant/confirm")) {
      return Promise.resolve(confirmResponse());
    }
    throw new Error(`unrouted: ${url}`);
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function reachTheCard() {
  render(
    <MemoryRouter initialEntries={["/master/ayla"]}>
      <MasterAylaScreen />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Вопрос к Ayla"), "хочу выходной");
  await user.click(screen.getByLabelText("Отправить"));
  // Положительная стража: карточка действительно появилась. Без неё узлы
  // ниже зеленели бы на экране, который вообще ничего не отрисовал.
  await screen.findByRole("button", { name: CONFIRM_LABEL });
  return user;
}

describe("мёртвый талон", () => {
  it("убирает карточку — обречённой кнопки на экране не остаётся", async () => {
    fetchMock.mockImplementation(
      route(() =>
        jsonResponse(400, {
          error: "action_expired",
          detail: EXPIRED_DETAIL,
          details: { retriable: false, cards: [] },
        }),
      ),
    );
    const user = await reachTheCard();

    await user.click(screen.getByRole("button", { name: CONFIRM_LABEL }));

    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: CONFIRM_LABEL }),
      ).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "Отмена" })).not.toBeInTheDocument();
  });

  it("показывает текст отказа — он же и говорит, что делать", async () => {
    fetchMock.mockImplementation(
      route(() =>
        jsonResponse(400, {
          error: "action_expired",
          detail: EXPIRED_DETAIL,
          details: { retriable: false, cards: [] },
        }),
      ),
    );
    const user = await reachTheCard();

    await user.click(screen.getByRole("button", { name: CONFIRM_LABEL }));

    expect(await screen.findByText(EXPIRED_DETAIL)).toBeInTheDocument();
  });

  it("оставляет поле ввода — «спросите заново» исполнимо", async () => {
    fetchMock.mockImplementation(
      route(() =>
        jsonResponse(400, {
          error: "action_invalid",
          detail: "подтверждение не читается",
          details: { retriable: false, cards: [] },
        }),
      ),
    );
    const user = await reachTheCard();

    await user.click(screen.getByRole("button", { name: CONFIRM_LABEL }));

    await screen.findByText("подтверждение не читается");
    expect(screen.getByLabelText("Вопрос к Ayla")).toBeEnabled();
    expect(screen.getByLabelText("Отправить")).toBeInTheDocument();
  });
});

describe("талон цел, отказало исполнение", () => {
  it("карточку НЕ убирает — повтор осмыслен", async () => {
    // Второй узел пары. Без него мы «починили» бы окончательный отказ и
    // незаметно сломали временный, где человек вправе нажать ещё раз.
    fetchMock.mockImplementation(
      route(() =>
        jsonResponse(400, {
          error: "action_rejected",
          detail: BUSY_DETAIL,
          details: { retriable: true, cards: [] },
        }),
      ),
    );
    const user = await reachTheCard();

    await user.click(screen.getByRole("button", { name: CONFIRM_LABEL }));

    expect(await screen.findByText(BUSY_DETAIL)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: CONFIRM_LABEL }),
    ).toBeInTheDocument();
  });
});

describe("сервер промолчал о живучести", () => {
  it("считается мёртвым — умолчание фейл-клоуз", async () => {
    // Старый сервер, не-JSON 5xx, сетевой сбой. Ошибиться можно в обе
    // стороны, и цены разные: лишний вопрос человека против кнопки,
    // которая не может сработать.
    fetchMock.mockImplementation(
      route(() =>
        jsonResponse(400, { error: "action_expired", detail: EXPIRED_DETAIL }),
      ),
    );
    const user = await reachTheCard();

    await user.click(screen.getByRole("button", { name: CONFIRM_LABEL }));

    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: CONFIRM_LABEL }),
      ).not.toBeInTheDocument();
    });
  });
});
