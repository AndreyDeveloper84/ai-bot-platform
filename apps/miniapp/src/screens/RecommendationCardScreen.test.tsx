/**
 * Кадр C04.1 «Моё лучшее направление» на экране (DRF-1769, К-3 N3).
 *
 * Экран — тупой рисовальщик ЗАПИСИ: показывает ровно то, что человек
 * увидел в чате, и ничего не пересобирает. Лист карты разрывов старше
 * §60: условия «до D10 не строится» и «флаг до VERIFIED > 0» сняты
 * решением владельца, направление — объект пилота.
 *
 * Что заперто:
 *
 * 1. кадр C04.1 по макету: заголовок, направление, подстрока, причины
 *    (≤3) и четыре действия дословно;
 * 2. **R11 механически** — на экране нет ни услуги, ни мастера, ни цены,
 *    ни слота, ни рейтинга: в записи их нет, и рисовать нечего;
 * 3. «Подобрать вариант» → каталог (исполнение, C05); «Почему» → кадр
 *    C04.3 (раскрытие причин); «Другой вариант» → кадр C04.2 (другие
 *    подходы, ≤2, без «показать больше»); «Не сейчас» — закрыть;
 * 4. `kind: "absence"` → C04.4 тем же текстом, что в DM (общая
 *    константа, паритет с ботом — отдельный тест);
 * 5. запись стёрта или чужая (404) → честный кадр, не пустой экран и не
 *    падение (класс #1918/#2198).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/recommendation-card", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/recommendation-card")>();
  return { ...original, fetchRecommendation: vi.fn() };
});

const navigateSpy = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const original = await importOriginal<typeof import("react-router-dom")>();
  return { ...original, useNavigate: () => navigateSpy };
});

import { ApiError } from "../lib/api";
import {
  ALT_HEAD,
  ALT_OTHERS_HEAD,
  ALT_PRIMARY_HEAD,
  BUTTON_ALT,
  BUTTON_PICK,
  BUTTON_SKIP,
  BUTTON_WHY,
  CARD_HEAD,
  WHY_HEAD,
  WHY_MORE_HEAD,
  fetchRecommendation,
  type RecommendationCard,
} from "../lib/recommendation-card";
import { NO_VERIFIED_EVIDENCE_TEXT } from "../lib/recommendation-absence";
import { RecommendationCardScreen } from "./RecommendationCardScreen";

const mockedFetch = vi.mocked(fetchRecommendation);

const CARD: RecommendationCard = {
  id: "11111111-1111-1111-1111-111111111111",
  kind: "direction",
  what: "Уменьшить утреннюю отёчность",
  subline: "Сфокусируемся на этом.",
  why: ["Ты написала: «хочу выглядеть свежее»", "Ты выбрала: лицо и кожа"],
  alternatives: [
    { what: "Вернуть лёгкость", subline: "Про тело." },
    { what: "Общий уход", subline: "" },
  ],
};

function renderScreen(id = CARD.id) {
  return render(
    <MemoryRouter initialEntries={[`/customer/recommendation/${id}`]}>
      <Routes>
        <Route
          path="/customer/recommendation/:recommendationId"
          element={<RecommendationCardScreen />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedFetch.mockResolvedValue(CARD);
});

describe("кадр C04.1", () => {
  it("рисует направление, причины и четыре действия макета", async () => {
    renderScreen();
    expect(await screen.findByText(CARD_HEAD)).toBeInTheDocument();
    expect(screen.getByText(CARD.what)).toBeInTheDocument();
    expect(screen.getByText(CARD.subline)).toBeInTheDocument();
    expect(screen.getByText(WHY_HEAD)).toBeInTheDocument();
    for (const reason of CARD.why) {
      expect(screen.getByText(reason)).toBeInTheDocument();
    }
    for (const label of [BUTTON_PICK, BUTTON_WHY, BUTTON_ALT, BUTTON_SKIP]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("не показывает больше трёх причин", async () => {
    mockedFetch.mockResolvedValue({
      ...CARD,
      why: ["Причина 1", "Причина 2", "Причина 3", "Причина 4"],
    });
    renderScreen();
    await screen.findByText(CARD_HEAD);
    expect(screen.queryByText("Причина 4")).not.toBeInTheDocument();
  });

  it("ничего бронируемого на экране нет — в записи его и не было", async () => {
    const { container } = renderScreen();
    await screen.findByText(CARD_HEAD);
    const text = (container.textContent ?? "").toLowerCase();
    for (const forbidden of ["₽", "руб", "мастер", "услуг", "рейтинг", ":00"]) {
      expect(text).not.toContain(forbidden);
    }
  });
});

describe("действия карточки", () => {
  it("«Подобрать вариант» ведёт в каталог — исполнение живёт там", async () => {
    renderScreen();
    await screen.findByText(CARD_HEAD);
    await userEvent.click(screen.getByRole("button", { name: BUTTON_PICK }));
    expect(navigateSpy).toHaveBeenCalledWith("/customer/catalog");
  });

  it("«Почему» раскрывает кадр C04.3 теми же причинами", async () => {
    renderScreen();
    await screen.findByText(CARD_HEAD);
    await userEvent.click(screen.getByRole("button", { name: BUTTON_WHY }));
    expect(screen.getByText(WHY_MORE_HEAD)).toBeInTheDocument();
    expect(screen.getByText(CARD.why[0] as string)).toBeInTheDocument();
  });

  it("«Другой вариант» показывает кадр C04.2 без обещания продолжения", async () => {
    renderScreen();
    await screen.findByText(CARD_HEAD);
    await userEvent.click(screen.getByRole("button", { name: BUTTON_ALT }));
    expect(screen.getByText(ALT_HEAD)).toBeInTheDocument();
    expect(screen.getByText(ALT_PRIMARY_HEAD)).toBeInTheDocument();
    expect(screen.getByText(ALT_OTHERS_HEAD)).toBeInTheDocument();
    expect(screen.getByText("Вернуть лёгкость")).toBeInTheDocument();
    expect(screen.getByText("Общий уход")).toBeInTheDocument();
    expect(screen.queryByText(/показать больше/i)).not.toBeInTheDocument();
  });

  it("одно направление — кадра выбора нет вовсе", async () => {
    mockedFetch.mockResolvedValue({ ...CARD, alternatives: [] });
    renderScreen();
    await screen.findByText(CARD_HEAD);
    expect(screen.queryByRole("button", { name: BUTTON_ALT })).not.toBeInTheDocument();
  });
});

describe("состояния, в которых карточки нет", () => {
  it("«нет рекомендации» — тот же текст, что в DM", async () => {
    mockedFetch.mockResolvedValue({
      id: CARD.id,
      kind: "absence",
      what: "",
      subline: "",
      why: [],
      alternatives: [],
    });
    renderScreen();
    expect(await screen.findByText(NO_VERIFIED_EVIDENCE_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(CARD_HEAD)).not.toBeInTheDocument();
  });

  it("стёртая или чужая запись — честный кадр, а не пустой экран", async () => {
    mockedFetch.mockRejectedValue(new ApiError(404, "not_found", "no such recommendation"));
    renderScreen();
    await waitFor(() => {
      expect(screen.queryByText(CARD_HEAD)).not.toBeInTheDocument();
    });
    expect(screen.getByRole("status").textContent ?? "").not.toBe("");
  });
});
