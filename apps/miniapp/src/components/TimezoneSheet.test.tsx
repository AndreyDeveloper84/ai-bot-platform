/**
 * Часовой пояс — вопрос человеку, а не замер устройства (DRF-1477).
 *
 * Сторожит три вещи, каждая из которых легко теряется при правке:
 *
 * 1. **Замер не становится ответом молча.** `Intl` определяет пояс, но
 *    записывается он только после подтверждения. Иначе VPN и поездка
 *    записывали бы человеку чужой пояс, а реестр личных полей называл
 *    бы это `USER_STATED` — то есть врал бы про происхождение (§73).
 * 2. **Отказ ведёт к выбору, а не к пустоте.** «Нет, другой» открывает
 *    список; вернуть человека в то же состояние значило бы задать
 *    вопрос впустую.
 * 3. **Сбой записи не выдаётся за успех.** Человек видит, что пояс
 *    остался прежним.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TimezoneSheet, zoneLabel } from "./TimezoneSheet";

function renderSheet(opts: {
  current?: string;
  onSave?: (zone: string) => Promise<void>;
}) {
  const onSave = opts.onSave ?? vi.fn().mockResolvedValue(undefined);
  const onClose = vi.fn();
  render(
    <TimezoneSheet
      open
      triggerRef={createRef<HTMLButtonElement>()}
      onClose={onClose}
      current={opts.current ?? ""}
      onSave={onSave}
    />,
  );
  return { onSave, onClose };
}

/** Подменить то, что «говорит устройство». */
function deviceSays(zone: string | null): void {
  vi.spyOn(Intl, "DateTimeFormat").mockImplementation(
    () =>
      ({
        resolvedOptions: () => ({ timeZone: zone }) as Intl.ResolvedDateTimeFormatOptions,
      }) as Intl.DateTimeFormat,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("замер устройства предлагается, а не записывается", () => {
  it("показывает ВИДИМОЕ значение и ждёт подтверждения", async () => {
    deviceSays("Asia/Yekaterinburg");
    const { onSave } = renderSheet({});

    // Присутствие: человек видит ГОРОД, а не механизм. Проверить город
    // он может; «использовать пояс устройства» — не может.
    expect(await screen.findByText(/Екатеринбург/)).toBeInTheDocument();
    // Отсутствие: до подтверждения не записано ничего.
    expect(onSave).not.toHaveBeenCalled();
  });

  it("записывает ровно подтверждённое значение", async () => {
    deviceSays("Asia/Yekaterinburg");
    const user = userEvent.setup();
    const { onSave } = renderSheet({});

    await user.click(await screen.findByRole("button", { name: "Да, верно" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("Asia/Yekaterinburg"));
  });

  it("не предлагает то, что уже стоит", async () => {
    // Спрашивать «Москва, верно?» у того, у кого стоит Москва, —
    // вопрос ради вопроса.
    deviceSays("Europe/Moscow");
    renderSheet({ current: "Europe/Moscow" });

    expect(await screen.findByText(/Выбери свой пояс/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Да, верно" })).not.toBeInTheDocument();
  });

  it("устройство молчит — сразу список, а не вопрос про пустоту", async () => {
    deviceSays(null);
    renderSheet({});

    expect(
      await screen.findByText(/Не удалось определить пояс автоматически/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Да, верно" })).not.toBeInTheDocument();
  });
});

describe("отказ ведёт к выбору, а не к пустоте", () => {
  it("«Нет, другой» открывает список и даёт записать выбранное", async () => {
    deviceSays("Europe/Moscow");
    const user = userEvent.setup();
    const { onSave, onClose } = renderSheet({});

    await user.click(await screen.findByRole("button", { name: "Нет, другой" }));

    // Присутствие: список открылся…
    const option = await screen.findByRole("button", { name: "Владивосток" });
    // …отсутствие: лист не закрылся молча, оставив человека ни с чем.
    expect(onClose).not.toHaveBeenCalled();

    await user.click(option);
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("Asia/Vladivostok"));
  });

  it("текущий пояс в списке помечен, а не спрятан", async () => {
    deviceSays(null);
    renderSheet({ current: "Asia/Omsk" });

    const current = await screen.findByRole("button", { name: "Омск" });
    expect(current).toHaveAttribute("aria-current", "true");
  });
});

describe("сбой записи не выдаётся за успех", () => {
  it("говорит, что пояс остался прежним, и предлагает повтор", async () => {
    deviceSays("Europe/Moscow");
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue(new Error("[500] boom"));
    const { onClose } = renderSheet({ current: "", onSave });

    await user.click(await screen.findByRole("button", { name: "Да, верно" }));

    expect(await screen.findByText(/Он остался прежним/)).toBeInTheDocument();
    // Лист НЕ закрылся: закрыть после неудачи значило бы показать
    // человеку прежнее значение без объяснения, почему новое не встало.
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("zoneLabel", () => {
  it("знакомую зону называет городом", () => {
    expect(zoneLabel("Asia/Krasnoyarsk")).toBe("Красноярск");
  });

  it("незнакомую отдаёт как есть, а не подменяет похожей", () => {
    // Подставить «ближайший» город значило бы соврать о том, где человек.
    expect(zoneLabel("America/Sao_Paulo")).toBe("America/Sao_Paulo");
  });
});
