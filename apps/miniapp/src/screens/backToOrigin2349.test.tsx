/**
 * Возврат ведёт туда, откуда пришли (DRF-2349, §61 вопросы 42–43).
 *
 * Решение владельца: «изменить цель» с «Плана» возвращает **в план**, съёмка
 * фото из «Дневника» — **в дневник**. До этого оба экрана выходили жёстким
 * адресом и не знали, откуда их открыли: экран цели уводил на Главную даже
 * того, кто пришёл из плана, а съёмка — того, кто пришёл из дневника.
 *
 * Показательно, что половина работы уже была сделана: `PlanLiteScreen` и
 * `FoodScannerDiaryScreen` **давно передают** `state.returnTo`. Его просто
 * никто не читал — происхождение доезжало до экрана и там пропадало.
 *
 * Главное требование листа — **оба происхождения обязаны работать**. Поэтому
 * каждая пара узлов ниже проверяет обе стороны: с происхождением уходим
 * туда, откуда пришли; без происхождения — на прежний адрес, ровно как
 * раньше. Без второй половины мы просто переставили бы дефект.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { backToOrigin, originFrom } from "../lib/screen-back";

function Probe() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname}</div>;
}

describe("originFrom — происхождение читается только из своего адреса", () => {
  it("берёт внутренний путь", () => {
    expect(originFrom({ returnTo: "/customer/plan" })).toBe("/customer/plan");
  });

  it.each([null, undefined, {}, { returnTo: 42 }, { returnTo: "" }])(
    "без происхождения молчит: %s",
    (state) => {
      expect(originFrom(state)).toBeNull();
    },
  );

  it.each([
    "https://evil.example/phish",
    "//evil.example/phish",
    "javascript:alert(1)",
    "customer/plan",
  ])("чужой адрес не считается происхождением: %s", (raw) => {
    // Происхождение приходит из состояния маршрута, а оно переживает
    // переход по ссылке. Внешний адрес здесь означал бы, что чужая
    // ссылка уводит человека из приложения его же кнопкой «назад».
    expect(originFrom({ returnTo: raw })).toBeNull();
  });
});

describe("backToOrigin — происхождение сильнее умолчания, но умолчание живо", () => {
  it("с происхождением ведёт туда", () => {
    expect(backToOrigin({ returnTo: "/customer/plan" }, "/customer/main")).toEqual({
      kind: "up",
      to: "/customer/plan",
    });
  });

  it("без происхождения ведёт на прежний адрес", () => {
    expect(backToOrigin(null, "/customer/main")).toEqual({
      kind: "up",
      to: "/customer/main",
    });
  });
});

describe("Поток съёмки: вход из дневника возвращает в дневник", () => {
  function renderCapture(state: unknown) {
    render(
      <MemoryRouter
        initialEntries={[{ pathname: "/customer/food-scanner/capture", state }]}
      >
        <Routes>
          <Route path="/customer/food-scanner/capture" element={<CaptureBack />} />
          <Route path="*" element={<Probe />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  // Тонкая обёртка вместо самого экрана: предмет проверки — КУДА ведёт
  // возврат, а не камера, согласие и загрузка фото. Экран целиком поднимают
  // соседние файлы; дублировать их установку значило бы проверять их, а не
  // это правило.
  function CaptureBack() {
    const location = useLocation();
    const intent = backToOrigin(location.state, "/customer/main");
    return <div data-testid="to">{intent.kind === "up" ? intent.to : "—"}</div>;
  }

  it("из дневника — в дневник", () => {
    renderCapture({ returnTo: "/customer/food-scanner/diary" });
    expect(screen.getByTestId("to")).toHaveTextContent("/customer/food-scanner/diary");
  });

  it("с Главной — на Главную, как и было", () => {
    renderCapture(null);
    expect(screen.getByTestId("to")).toHaveTextContent("/customer/main");
  });
});
