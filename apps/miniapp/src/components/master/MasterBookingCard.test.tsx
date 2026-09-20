/**
 * Одна карточка записи для «Сегодня» и «Расписания» — макет DRF-1181 п.5
 * (DRF-2157, М-6). «Показываем только кто · что · когда. Не показываем:
 * телефон, стоимость, оплату, источник, комментарии, тех. статусы.»
 *
 *   today    (крупная):   10:30–11:30 / Анна К. / Классический массаж / ⏱ 1 ч / ›
 *   schedule (компактная): 10:30 | Анна К. / Классический массаж · 1 ч / ›
 *
 * Карточка — ссылка на «Детали записи» (DRF-1183 «нажатие на запись → экран
 * деталей»). Набор полей закрыт: лишнего пропа нет, лишнего текста нет.
 * Метки времени — без смещения (локальные), TZ-независимо.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { MasterBookingCard } from "./MasterBookingCard";

const BASE = {
  clientName: "Анна К.",
  serviceName: "Классический массаж",
  startIso: "2026-08-20T10:30:00",
  endIso: "2026-08-20T11:30:00",
  durationMin: 60,
  to: "/master/bookings/b-1",
};

function renderCard(props: Partial<Parameters<typeof MasterBookingCard>[0]> = {}) {
  return render(
    <MemoryRouter initialEntries={["/master/dashboard"]}>
      <MasterBookingCard variant="today" {...BASE} {...props} />
    </MemoryRouter>,
  );
}

describe("режим «Сегодня» (крупная)", () => {
  it("начало–конец, имя, услуга, длительность; ссылка на детали", () => {
    renderCard();
    const link = screen.getByRole("link", { name: /Анна К\./ });
    expect(link).toHaveAttribute("href", "/master/bookings/b-1");
    expect(link).toHaveTextContent("10:30–11:30");
    expect(link).toHaveTextContent("Анна К.");
    expect(link).toHaveTextContent("Классический массаж");
    expect(link).toHaveTextContent("1 ч");
  });

  it("длительность — общим форматтером: 90 → «1 ч 30 мин», не «90 мин»", () => {
    renderCard({ durationMin: 90 });
    expect(screen.getByRole("link")).toHaveTextContent("1 ч 30 мин");
    expect(screen.queryByText(/90 мин/)).toBeNull();
  });

  it("quiet — те же поля, тише по тону (класс)", () => {
    renderCard({ quiet: true });
    expect(screen.getByRole("link")).toHaveClass("master-booking-card--quiet");
    expect(screen.getByRole("link")).toHaveTextContent("Анна К.");
  });
});

describe("режим «Расписание» (компактная)", () => {
  it("время начала слева, имя и «услуга · длительность» справа; ссылка на детали", () => {
    renderCard({ variant: "schedule", to: "/solo/bookings/b-1" });
    const link = screen.getByRole("link", { name: /Анна К\./ });
    expect(link).toHaveAttribute("href", "/solo/bookings/b-1");
    expect(link).toHaveTextContent("10:30");
    expect(link).toHaveTextContent("Классический массаж · 1 ч");
    // Компактная — без конца интервала.
    expect(link).not.toHaveTextContent("11:30");
  });
});

describe("закрытый набор полей (DRF-1181 «Важно»)", () => {
  it("телефон, цена, оплата, источник, комментарии, тех. статусы — на карточке нет", () => {
    renderCard({
      // Лишнее, что могло бы прилететь с сервера, до карточки не доходит:
      // у компонента просто нет таких пропов. Проверяем итоговый текст.
      variant: "schedule",
    });
    const text = screen.getByRole("link").textContent ?? "";
    for (const forbidden of [
      "+7",
      "₽",
      "оплат",
      "Оплат",
      "источник",
      "постоянный клиент",
      "Постоянный клиент",
      "идёт сейчас",
      "заканчивается",
      "≈",
      "Сказала",
    ]) {
      expect(text).not.toContain(forbidden);
    }
    expect(screen.queryByLabelText("идёт сейчас")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
