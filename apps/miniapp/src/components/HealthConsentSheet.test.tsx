/**
 * Лист согласия на медданные (DRF-1453).
 *
 * Проверяется не «рендерится ли», а те свойства, из-за которых 152-ФЗ ст. 10
 * требует отдельного согласия:
 *
 * * раскрытие показано ДО подтверждения и не спрятано за «подробнее»;
 * * выдача уходит с версией показанного текста — не с пустым «да»;
 * * отзыв доступен тем же весом, что и выдача;
 * * устаревшее раскрытие не «дожимается» повтором, а честно останавливает.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/health-consent", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../lib/health-consent")>();
  return {
    ...original,
    fetchHealthConsent: vi.fn(),
    grantHealthConsent: vi.fn(),
    withdrawHealthConsent: vi.fn(),
  };
});

import {
  grantHealthConsent,
  HEALTH_CONSENT_DOCUMENT_VERSION,
  StaleDisclosureError,
  withdrawHealthConsent,
  type HealthConsentState,
} from "../lib/health-consent";
import { HealthConsentSheet } from "./PersonalDataSheets";

const mockedGrant = vi.mocked(grantHealthConsent);
const mockedWithdraw = vi.mocked(withdrawHealthConsent);

function state(granted: boolean): HealthConsentState {
  return {
    granted,
    granted_at: granted ? "2026-09-03T10:00:00Z" : null,
    document_version: granted ? HEALTH_CONSENT_DOCUMENT_VERSION : "",
    current_document_version: HEALTH_CONSENT_DOCUMENT_VERSION,
  };
}

function renderSheet(granted: boolean, onSettled = vi.fn(), onClose = vi.fn()) {
  const triggerRef = createRef<HTMLButtonElement>();
  render(
    <>
      {/* Открывашка. Подпись отличается от кнопок листа намеренно —
          иначе запрос по имени попадал бы в две кнопки сразу. */}
      <button ref={triggerRef} type="button">
        Строка согласия
      </button>
      <HealthConsentSheet
        open
        triggerRef={triggerRef}
        onClose={onClose}
        granted={granted}
        onSettled={onSettled}
      />
    </>,
  );
  return { onSettled, onClose };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("выдача", () => {
  it("показывает, что именно передаётся и зачем, до подтверждения", () => {
    renderSheet(false);

    // Перечень виден сразу — не за аккордеоном, не после нажатия.
    expect(screen.getByText(/Что передаётся:/)).toBeInTheDocument();
    expect(screen.getByText(/Зачем:/)).toBeInTheDocument();
    expect(screen.getByText(/дневник питания/)).toBeInTheDocument();
    // И прямо сказано, что разрешение отдельное.
    expect(screen.getByText(/особой категории/)).toBeInTheDocument();
  });

  it("называет обратимость до нажатия, а не после", () => {
    renderSheet(false);
    expect(screen.getByText(/Отозвать можно в любой момент/)).toBeInTheDocument();
  });

  it("отправляет версию показанного раскрытия", async () => {
    mockedGrant.mockResolvedValue(state(true));
    const { onSettled } = renderSheet(false);

    await userEvent.click(screen.getByRole("button", { name: "Разрешить" }));

    expect(mockedGrant).toHaveBeenCalledWith(HEALTH_CONSENT_DOCUMENT_VERSION);
    expect(onSettled).toHaveBeenCalledWith(state(true));
  });

  it("фокус не приземляется на подтверждающую кнопку (WCAG 2.5.5)", () => {
    renderSheet(false);
    expect(document.activeElement).toHaveTextContent("Отмена");
  });
});

describe("отзыв", () => {
  it("доступен тем же действием, что и выдача", async () => {
    mockedWithdraw.mockResolvedValue(state(false));
    const { onSettled } = renderSheet(true);

    await userEvent.click(screen.getByRole("button", { name: "Отозвать" }));

    expect(mockedWithdraw).toHaveBeenCalledTimes(1);
    expect(mockedGrant).not.toHaveBeenCalled();
    expect(onSettled).toHaveBeenCalledWith(state(false));
  });

  it("говорит, что дневник остаётся у человека", () => {
    renderSheet(true);
    expect(screen.getByText(/Дневник и записи в нём останутся/)).toBeInTheDocument();
  });
});

describe("устаревшее раскрытие", () => {
  it("останавливает вместо «попробовать ещё раз»", async () => {
    mockedGrant.mockRejectedValue(new StaleDisclosureError());
    const { onSettled } = renderSheet(false);

    await userEvent.click(screen.getByRole("button", { name: "Разрешить" }));

    expect(screen.getByText(/Текст про данные обновился/)).toBeInTheDocument();
    // Повтор отправил бы ту же устаревшую версию — кнопки повтора нет.
    expect(
      screen.queryByRole("button", { name: "Попробовать ещё раз" }),
    ).not.toBeInTheDocument();
    expect(onSettled).not.toHaveBeenCalled();
  });
});

describe("сбой записи", () => {
  it("не сообщает об изменении, которого не произошло", async () => {
    mockedGrant.mockRejectedValue(new Error("network"));
    const { onSettled, onClose } = renderSheet(false);

    await userEvent.click(screen.getByRole("button", { name: "Разрешить" }));

    expect(screen.getByText(/Ничего не изменилось/)).toBeInTheDocument();
    expect(onSettled).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
