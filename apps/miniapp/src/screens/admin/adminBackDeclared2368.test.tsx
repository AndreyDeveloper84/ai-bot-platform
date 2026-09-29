/**
 * Возврат шести экранов кабинета: объявлен и ведёт по адресу (DRF-2368).
 *
 * Сторож `backContract` проверяет, что объявление **есть**. Эти узлы
 * проверяют, что оно **верное**: куда именно ведёт возврат и что ведёт он
 * адресом, а не смещением по истории.
 *
 * Разделение не формальное. Объявление без проверки адреса пустило бы
 * `useScreenBack(backTo("/"))` у карточки мастера — сторож был бы зелен,
 * человек уезжал бы не туда.
 *
 * **Дефекта для человека здесь не было.** Все шесть и раньше возвращали по
 * адресу своими кнопками; не было объявления, то есть защиты от следующего
 * автора. Эти узлы закрепляют оба свойства разом, чтобы следующая правка не
 * потеряла ни одно.
 */
import { describe, expect, it } from "vitest";

const SOURCES = import.meta.glob("./*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

function source(name: string): string {
  const entry = Object.entries(SOURCES).find(([p]) =>
    p.endsWith(`/${name}.tsx`),
  );
  expect(entry, `Исходник ${name} не прочитан — проверка ослепла`).toBeTruthy();
  return entry![1];
}

/** Экран → куда обязан вести его возврат. */
const BACK_TARGETS: ReadonlyArray<readonly [string, string]> = [
  ["AdminHandoffQueueScreen", "SALON_PILOT_LANDING"],
  ["AdminReadinessScreen", "SALON_PILOT_LANDING"],
  ["AdminInternalChatThreadScreen", '"/admin/internal-chat"'],
  ["AdminMasterDetailScreen", '"/admin/team"'],
  // Новая запись открывается и с «Сегодня», и с «Дня салона»: адрес берётся
  // из закрытого списка возвратов, а не из параметра ссылки.
  ["AdminNewBookingScreen", "returnTo.path"],
];

describe("Возврат объявлен адресом, и адрес тот самый", () => {
  it.each(BACK_TARGETS)("%s возвращает в %s", (screen, target) => {
    const src = source(screen);

    expect(src).toContain(`backTo(${target})`);
    expect(src).toContain("useScreenBack(");
  });
});

describe("Поток деактивации объявляет шаг, а не адрес", () => {
  const src = () => source("AdminDeactivationFlowScreen");

  it("возврат объявлен действием", () => {
    // Адрес здесь был бы неправдой: на шагах 2 и 3 «назад» означает шаг
    // назад. Аппаратная кнопка MAX появляется у экрана впервые, и с
    // адресом она выносила бы человека из потока с уже принятыми
    // решениями по будущим записям.
    expect(src()).toContain("backByAction(");
    expect(src()).toContain("useScreenBack(");
  });

  it("с первого шага выходит из потока, с прочих — шагает назад", () => {
    const body = src();

    expect(body).toContain('navigate("/admin/team")');
    expect(body).toMatch(/step\/set["']?,\s*step:\s*state\.step === 3 \? 2 : 1/);
  });
});

describe("Возврат в кабинете — адрес, а не история", () => {
  it.each([
    "AdminDeactivationFlowScreen",
    "AdminHandoffQueueScreen",
    "AdminInternalChatThreadScreen",
    "AdminMasterDetailScreen",
    "AdminNewBookingScreen",
    "AdminReadinessScreen",
  ])("%s не ходит по истории", (screen) => {
    // `navigate(-1)` в кабинете не встречался ни разу — пусть так и
    // останется: в приложение заходят по ссылке из бота, истории может не
    // быть вовсе, и тогда «назад» либо молчит, либо закрывает приложение.
    expect(source(screen)).not.toMatch(/navigate\(-1\)|history\.back\(/);
  });
});
