/**
 * Разбор дня салона для «Сегодня» (DRF-1236).
 *
 * Проверяется то, что легко сломать незаметно: что «Сейчас» берётся с
 * серверного признака, а не пересчитывается; что «Дальше» не тащит
 * отменённые, закрытые и уже начавшиеся; что время печатается в поясе
 * САЛОНА, а не устройства.
 *
 * Тесты умеют падать: снимите фильтр по `is_in_progress` — покраснеет
 * «Сейчас»; уберите отсечку по времени в `visitsNext` — покраснеет
 * «Дальше»; передайте пояс устройства вместо `day.timezone` —
 * покраснеет `formatTime`.
 */
import { describe, expect, it } from "vitest";

import type { SalonDayResponse, SalonDayVisit } from "./admin-api";
import {
  clientLabel,
  formatRange,
  formatTime,
  masterInitial,
  mastersToday,
  visitCountLabel,
  visitsNext,
  visitsNow,
} from "./salon-today";

/** Полдень 22 августа в UTC — 15:00 в Москве. */
const NOON_UTC = Date.parse("2026-08-22T12:00:00Z");

function visit(over: Partial<SalonDayVisit> = {}): SalonDayVisit {
  return {
    id: "v-1",
    service_id: "svc-1",
    start_at: "2026-08-22T13:00:00Z",
    end_at: "2026-08-22T14:00:00Z",
    duration_min: 60,
    status: "confirmed",
    service_name: "Классический массаж",
    client_first_name: "Анна",
    client_last_initial: "П.",
    is_in_progress: false,
    ...over,
  };
}

function day(over: Partial<SalonDayResponse> = {}): SalonDayResponse {
  return {
    date: "2026-08-22",
    timezone: "Europe/Moscow",
    summary: { total: 0, upcoming: 0, completed: 0, released: 0 },
    masters: [],
    orphan_visits: [],
    ...over,
  };
}

describe("visitsNow", () => {
  it("берёт признак сервера, а не считает время сама", () => {
    const running = visit({ id: "running", is_in_progress: true });
    // Это время накрывает NOON_UTC, но сервер идущей её не считает —
    // и экран не должен считать тоже.
    const looksCurrent = visit({
      id: "looks-current",
      start_at: "2026-08-22T11:00:00Z",
      end_at: "2026-08-22T13:00:00Z",
      is_in_progress: false,
    });
    const rows = visitsNow(
      day({
        masters: [
          {
            master_id: "m-1",
            name: "Денис",
            is_active: true,
            visits: [running, looksCurrent],
          },
        ],
      }),
    );
    // Присутствие сначала: список не пуст и содержит именно идущую.
    expect(rows.map((r) => r.visit.id)).toEqual(["running"]);
    expect(rows[0]!.masterName).toBe("Денис");
  });

  it("не показывает отменённую, даже если признак остался", () => {
    const alive = visit({ id: "alive", is_in_progress: true });
    const cancelled = visit({
      id: "cancelled",
      is_in_progress: true,
      status: "cancelled",
    });
    const rows = visitsNow(
      day({
        masters: [
          {
            master_id: "m-1",
            name: "Денис",
            is_active: true,
            visits: [alive, cancelled],
          },
        ],
      }),
    );
    const ids = rows.map((r) => r.visit.id);
    // Присутствие и отсутствие — на одних данных и по одному имени.
    expect(ids).toContain("alive");
    expect(ids).not.toContain("cancelled");
  });

  it("не показывает закрытую, хотя сервер оставил ей признак", () => {
    // Сервер снимает `is_in_progress` только с отменённых и неявок
    // (`RELEASED_STATUSES`), а `completed` туда не входит. Визит,
    // закрытый до конца интервала — а закрывают его именно так, — придёт
    // с признаком «идёт».
    const alive = visit({ id: "alive", is_in_progress: true });
    const closed = visit({
      id: "closed",
      is_in_progress: true,
      status: "completed",
    });
    const rows = visitsNow(
      day({
        masters: [
          {
            master_id: "m-1",
            name: "Денис",
            is_active: true,
            visits: [alive, closed],
          },
        ],
      }),
    );
    const ids = rows.map((r) => r.visit.id);
    expect(ids).toContain("alive");
    expect(ids).not.toContain("closed");
  });

  it("показывает запись без мастера, а не прячет её", () => {
    const rows = visitsNow(
      day({ orphan_visits: [visit({ id: "orphan", is_in_progress: true })] }),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]!.masterName).toBe("");
  });
});

describe("visitsNext", () => {
  it("оставляет только ещё не начавшиеся и сортирует по времени", () => {
    const later = visit({ id: "later", start_at: "2026-08-22T15:00:00Z" });
    const soon = visit({ id: "soon", start_at: "2026-08-22T13:00:00Z" });
    const past = visit({ id: "past", start_at: "2026-08-22T09:00:00Z" });
    const rows = visitsNext(
      day({
        masters: [
          {
            master_id: "m-1",
            name: "Ольга",
            is_active: true,
            visits: [later, soon, past],
          },
        ],
      }),
      NOON_UTC,
    );
    const ids = rows.map((r) => r.visit.id);
    expect(ids).toEqual(["soon", "later"]);
    expect(ids).not.toContain("past");
  });

  it("не тащит идущие, отменённые, не пришедших и закрытые", () => {
    const future = visit({ id: "future", start_at: "2026-08-22T16:00:00Z" });
    const running = visit({
      id: "running",
      start_at: "2026-08-22T16:00:00Z",
      is_in_progress: true,
    });
    const cancelled = visit({
      id: "cancelled",
      start_at: "2026-08-22T16:00:00Z",
      status: "cancelled",
    });
    const noShow = visit({
      id: "no-show",
      start_at: "2026-08-22T16:00:00Z",
      status: "no_show",
    });
    const done = visit({
      id: "done",
      start_at: "2026-08-22T16:00:00Z",
      status: "completed",
    });
    const rows = visitsNext(
      day({
        masters: [
          {
            master_id: "m-1",
            name: "Ольга",
            is_active: true,
            visits: [future, running, cancelled, noShow, done],
          },
        ],
      }),
      NOON_UTC,
    );
    const ids = rows.map((r) => r.visit.id);
    expect(ids).toEqual(["future"]);
    expect(ids).not.toContain("running");
    expect(ids).not.toContain("cancelled");
    expect(ids).not.toContain("no-show");
    expect(ids).not.toContain("done");
  });
});

describe("mastersToday", () => {
  it("не считает освобождённые слоты: число сходится с тем, что видно", () => {
    const rows = mastersToday(
      day({
        masters: [
          {
            master_id: "m-1",
            name: "Денис",
            is_active: true,
            visits: [
              visit({ id: "a" }),
              visit({ id: "b", status: "completed" }),
              visit({ id: "c", status: "cancelled" }),
              visit({ id: "d", status: "no_show" }),
            ],
          },
        ],
      }),
    );
    expect(rows).toHaveLength(1);
    // Состоявшаяся считается — она часть дня мастера; освобождённые нет.
    expect(rows[0]!.visitCount).toBe(2);
  });

  it("прячет выключенную карточку без записей и оставляет с записями", () => {
    const rows = mastersToday(
      day({
        masters: [
          { master_id: "m-1", name: "Денис", is_active: true, visits: [] },
          { master_id: "m-2", name: "Инна", is_active: false, visits: [] },
          {
            master_id: "m-3",
            name: "Ольга",
            is_active: false,
            visits: [visit({ id: "x" })],
          },
        ],
      }),
    );
    const names = rows.map((r) => r.name);
    // Присутствие сначала: активный и выключенный-с-записями на месте...
    expect(names).toContain("Денис");
    expect(names).toContain("Ольга");
    // ...и только потом — что выключенной пустой карточки нет.
    expect(names).not.toContain("Инна");
  });

  it("отдаёт имя и число записей и ничего не выдумывает сверх ответа", () => {
    const rows = mastersToday(
      day({
        masters: [
          {
            master_id: "m-1",
            name: "Денис",
            is_active: true,
            visits: [visit({ id: "a" }), visit({ id: "b" })],
          },
          { master_id: "m-2", name: "Инна", is_active: true, visits: [] },
        ],
      }),
    );
    expect(rows).toEqual([
      { masterId: "m-1", name: "Денис", visitCount: 2 },
      { masterId: "m-2", name: "Инна", visitCount: 0 },
    ]);
    // Полей, которых нет в ответе дня, нет и здесь: часы, кабинет,
    // недоступность и фотография — предмет отдельной задачи на данные.
    const keys = Object.keys(rows[0]!);
    expect(keys).toContain("name");
    expect(keys).not.toContain("hours");
    expect(keys).not.toContain("room");
  });
});

describe("время печатается в поясе салона", () => {
  it("13:00 UTC — это 16:00 в Москве", () => {
    expect(formatTime("2026-08-22T13:00:00Z", "Europe/Moscow")).toBe("16:00");
  });

  it("тот же момент в другом поясе салона читается иначе", () => {
    expect(formatTime("2026-08-22T13:00:00Z", "Asia/Yekaterinburg")).toBe(
      "18:00",
    );
  });

  it("интервал сворачивается в одно время, когда конца нет", () => {
    const full = formatRange(visit(), "Europe/Moscow");
    const open = formatRange(visit({ end_at: null }), "Europe/Moscow");
    expect(full).toBe("16:00 – 17:00");
    expect(open).toBe("16:00");
  });
});

describe("подписи", () => {
  it("клиент — имя и инициал, без фамилии и телефона", () => {
    expect(clientLabel(visit())).toBe("Анна П.");
    expect(
      clientLabel(visit({ client_first_name: "", client_last_initial: "" })),
    ).toBe("Гость");
  });

  it("буква аватара берётся из имени", () => {
    expect(masterInitial("Ольга")).toBe("О");
    expect(masterInitial("")).toBe("");
  });

  it("число записей склоняется", () => {
    expect(visitCountLabel(0)).toBe("Записей нет");
    expect(visitCountLabel(1)).toBe("1 запись");
    expect(visitCountLabel(3)).toBe("3 записи");
    expect(visitCountLabel(5)).toBe("5 записей");
    expect(visitCountLabel(11)).toBe("11 записей");
    expect(visitCountLabel(21)).toBe("21 запись");
  });
});
