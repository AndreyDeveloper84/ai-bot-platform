/**
 * DRF-2933 — адреса возврата салонных потоков: закрытый список, а не «куда
 * сказано». Адрес приходит снаружи (состояние маршрута, параметры ссылки),
 * и подставленная строка не должна решать, куда уйдёт человек.
 */
import { describe, expect, it } from "vitest";

import { bookingDetailBack, scheduleReturnPath } from "./salon-return";

describe("возврат из карточки записи", () => {
  it("принимает «Сегодня», «Расписание» с датой и без, день мастера и день «Рабочего времени»", () => {
    for (const from of [
      "/admin/today",
      "/admin/schedule",
      "/admin/schedule?date=2034-08-21",
      "/admin/schedule/master/7f3c0f0e-1111-4222-8333-444455556666?date=2034-08-21",
      "/admin/working-time/m-1/2034-08-21",
    ]) {
      expect(bookingDetailBack({ from })).toBe(from);
    }
  });

  it("чужой и испорченный адрес — «Расписание» без даты", () => {
    for (const from of [
      "https://evil.example/admin/schedule",
      "//evil.example",
      "/admin/schedule?date=2034-08-21&next=//evil.example",
      "/admin/schedule/master/m-1",
      "/admin/schedule/master/../../settings?date=2034-08-21",
      "/admin/schedule/master/m-1?date=завтра",
      "/admin/settings",
      "",
    ]) {
      expect(bookingDetailBack({ from })).toBe("/admin/schedule");
    }
    expect(bookingDetailBack(null)).toBe("/admin/schedule");
    expect(bookingDetailBack({ from: 42 })).toBe("/admin/schedule");
    expect(bookingDetailBack("строка")).toBe("/admin/schedule");
  });

  it("несуществующая дата отбрасывается, а не уезжает в адрес", () => {
    expect(bookingDetailBack({ from: "/admin/schedule?date=2034-02-30" })).toBe("/admin/schedule");
    expect(bookingDetailBack({ from: "/admin/schedule/master/m-1?date=2034-02-30" })).toBe("/admin/schedule");
  });
});

describe("возврат из формы новой записи", () => {
  const params = (query: string) => new URLSearchParams(query);

  it("с «Расписания» — на тот же день", () => {
    expect(scheduleReturnPath(params("return=schedule&date=2034-08-21"))).toBe("/admin/schedule?date=2034-08-21");
    expect(scheduleReturnPath(params("return=schedule"))).toBe("/admin/schedule");
    expect(scheduleReturnPath(params("return=schedule&date=мусор"))).toBe("/admin/schedule");
  });

  it("с дня мастера — к тому же мастеру и дню", () => {
    expect(scheduleReturnPath(params("return=schedule-master&date=2034-08-21&master_id=m-1"))).toBe(
      "/admin/schedule/master/m-1?date=2034-08-21",
    );
  });

  it("мастер или дата не годятся — в «Расписание», а не по подставленному пути", () => {
    expect(scheduleReturnPath(params("return=schedule-master&date=2034-08-21&master_id=..%2F..%2Fsettings"))).toBe(
      "/admin/schedule?date=2034-08-21",
    );
    expect(scheduleReturnPath(params("return=schedule-master&master_id=m-1"))).toBe("/admin/schedule");
  });

  it("другой возврат — не этот вопрос", () => {
    expect(scheduleReturnPath(params("return=today&date=2034-08-21"))).toBeNull();
    expect(scheduleReturnPath(params(""))).toBeNull();
  });
});
