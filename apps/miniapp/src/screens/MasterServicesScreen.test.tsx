/**
 * «Услуги» мастера — секция «Свои услуги» и форма «Добавить мою» (DRF-1896, M18a).
 *
 * Сторожа:
 * A — счётчик «Свои услуги · N» и статусы — только из ответа сервера;
 * B — заявки не смешиваются с каталогом: pending-заявка не попадает в список
 *     каталога и его счёт (фриз §11.2, «pending не в знаменателе»);
 * C — пустой каталог не прячет секцию и кнопку «Добавить мою»;
 * D — форма: без названия / длительности / цены запрос не уходит;
 * E — похожая услуга: подсказка показана, заявка НЕ отправлена, «Выбрать эту
 *     услугу» выключена (выбор канона — M8); «Всё равно добавить мою» шлёт
 *     заявку, список перечитывается с сервера;
 * F — нет похожей → заявка уходит сразу;
 * G — профиль не связан (not_linked) → объяснение, формы нет.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getMasterCatalog: vi.fn(),
    listCanonGapRequests: vi.fn(),
    createCanonGapRequest: vi.fn(),
    getSimilarCanonTemplates: vi.fn(),
  };
});

import { ApiError } from "../lib/api";
import {
  createCanonGapRequest,
  getMasterCatalog,
  getSimilarCanonTemplates,
  listCanonGapRequests,
  type CanonGapRequest,
  type MasterServiceItem,
} from "../lib/master-api";
import {
  ADD_ANYWAY_LABEL,
  ADD_OWN_LABEL,
  ERR_DURATION,
  ERR_NAME,
  ERR_PRICE,
  FIELD_DURATION,
  FIELD_NAME,
  FIELD_PRICE,
  MasterServicesScreen,
  NOT_LINKED_MESSAGE,
  OWN_TITLE,
  PICK_CANON_LABEL,
  SENT_MESSAGE,
  validateOwnService,
} from "./MasterServicesScreen";

const mockedCatalog = vi.mocked(getMasterCatalog);
const mockedList = vi.mocked(listCanonGapRequests);
const mockedCreate = vi.mocked(createCanonGapRequest);
const mockedSimilar = vi.mocked(getSimilarCanonTemplates);

function svc(id: string, name: string, category = "Брови"): MasterServiceItem {
  return {
    service_id: id,
    name,
    category,
    duration_min: 60,
    price_rub: 1500,
    description: "",
  } as MasterServiceItem;
}

function req(id: string, name: string, extra: Partial<CanonGapRequest> = {}): CanonGapRequest {
  return {
    id,
    specialist_id: "m1",
    name,
    description: "",
    duration_minutes: 120,
    price: "4500.00",
    status: "pending",
    status_label: "На проверке",
    resolved_template_id: null,
    clarification_question: null,
    rejection_reason: null,
    decided_at: null,
    created_at: "2026-09-15T10:00:00+00:00",
    ...extra,
  };
}

const ownSection = () => screen.getByRole("region", { name: OWN_TITLE });
const catalog = () => screen.getByTestId("catalog-services");

function fill(label: string, value: string) {
  fireEvent.change(within(ownSection()).getByLabelText(label), { target: { value } });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedCatalog.mockResolvedValue([svc("s1", "Коррекция бровей"), svc("s2", "Окрашивание бровей")]);
  mockedList.mockResolvedValue({ requests: [req("r1", "Татуаж бровей пудровый")] });
  mockedSimilar.mockResolvedValue({ similar: [] });
  mockedCreate.mockResolvedValue({ request: req("r2", "Ламинирование"), similar: [] });
});

describe("MasterServicesScreen — «Свои услуги»", () => {
  it("A+B: counter and status come from the server; requests stay out of the catalog", async () => {
    mockedList.mockResolvedValue({
      requests: [
        req("r1", "Татуаж бровей пудровый"),
        req("r3", "Микроблейдинг", {
          status: "needs_clarification",
          status_label: "Нужно уточнение",
          clarification_question: "Это брови или губы?",
        }),
      ],
    });
    render(<MasterServicesScreen />);

    expect(await screen.findByRole("heading", { name: `${OWN_TITLE} · 2` })).toBeInTheDocument();
    const own = ownSection();
    expect(within(own).getAllByTestId("own-service")).toHaveLength(2);
    expect(within(own).getByText("На проверке")).toBeInTheDocument();
    expect(within(own).getByText("Нужно уточнение")).toBeInTheDocument();
    expect(within(own).getByText("Это брови или губы?")).toBeInTheDocument();

    // Каталог — ровно две услуги сервера; заявок в нём нет.
    expect(within(catalog()).getByText("Коррекция бровей")).toBeInTheDocument();
    expect(within(catalog()).getByText("Окрашивание бровей")).toBeInTheDocument();
    expect(within(catalog()).queryByText("Татуаж бровей пудровый")).not.toBeInTheDocument();
    expect(within(catalog()).queryAllByTestId("own-service")).toHaveLength(0);
  });

  it("C: an empty catalog still shows the section and the add button", async () => {
    mockedCatalog.mockResolvedValue([]);
    mockedList.mockResolvedValue({ requests: [] });
    render(<MasterServicesScreen />);
    expect(await screen.findByRole("heading", { name: `${OWN_TITLE} · 0` })).toBeInTheDocument();
    expect(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL })).toBeInTheDocument();
  });

  it("D: invalid form sends nothing and names each field", async () => {
    render(<MasterServicesScreen />);
    fireEvent.click(await within(await screen.findByRole("region", { name: OWN_TITLE })).findByRole("button", { name: ADD_OWN_LABEL }));
    fill(FIELD_DURATION, "0");
    fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL }));

    expect(await within(ownSection()).findByText(ERR_NAME)).toBeInTheDocument();
    expect(within(ownSection()).getByText(ERR_DURATION)).toBeInTheDocument();
    expect(within(ownSection()).getByText(ERR_PRICE)).toBeInTheDocument();
    expect(mockedSimilar).not.toHaveBeenCalled();
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("E: a similar service is a hint — nothing sent until «Всё равно добавить мою»", async () => {
    mockedSimilar.mockResolvedValue({
      similar: [{ template_id: "t1", name: "Перманентный макияж бровей", matched_by: "synonym" }],
    });
    render(<MasterServicesScreen />);
    fireEvent.click(await within(await screen.findByRole("region", { name: OWN_TITLE })).findByRole("button", { name: ADD_OWN_LABEL }));
    fill(FIELD_NAME, "Татуаж бровей");
    fill(FIELD_DURATION, "90");
    fill(FIELD_PRICE, "3000");
    fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL }));

    expect(await within(ownSection()).findByText("Перманентный макияж бровей")).toBeInTheDocument();
    expect(mockedSimilar).toHaveBeenCalledWith("Татуаж бровей");
    expect(mockedCreate).not.toHaveBeenCalled();
    expect(within(ownSection()).getByRole("button", { name: PICK_CANON_LABEL })).toBeDisabled();

    mockedList.mockResolvedValue({ requests: [req("r1", "Татуаж бровей пудровый"), req("r2", "Татуаж бровей")] });
    fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_ANYWAY_LABEL }));

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    expect(mockedCreate).toHaveBeenCalledWith({
      name: "Татуаж бровей",
      description: "",
      duration_minutes: 90,
      price: "3000",
    });
    expect(await within(ownSection()).findByText(SENT_MESSAGE)).toBeInTheDocument();
    // Счётчик — перечитанный с сервера, а не дописанный локально.
    expect(await screen.findByRole("heading", { name: `${OWN_TITLE} · 2` })).toBeInTheDocument();
    expect(mockedList).toHaveBeenCalledTimes(2);
  });

  it("F: no similar service → the request goes straight away", async () => {
    render(<MasterServicesScreen />);
    fireEvent.click(await within(await screen.findByRole("region", { name: OWN_TITLE })).findByRole("button", { name: ADD_OWN_LABEL }));
    fill(FIELD_NAME, "Ламинирование");
    fill(FIELD_DURATION, "60");
    fill(FIELD_PRICE, "2000,50");
    fireEvent.click(within(ownSection()).getByRole("button", { name: ADD_OWN_LABEL }));
    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    expect(mockedCreate).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Ламинирование", duration_minutes: 60, price: "2000.50" }),
    );
  });

  it("G: not linked → explanation, no form", async () => {
    mockedList.mockRejectedValue(new ApiError(403, "not_linked", "…"));
    render(<MasterServicesScreen />);
    expect(await within(await screen.findByRole("region", { name: OWN_TITLE })).findByText(NOT_LINKED_MESSAGE)).toBeInTheDocument();
    expect(within(ownSection()).queryByRole("button", { name: ADD_OWN_LABEL })).not.toBeInTheDocument();
    // Каталог при этом на месте.
    expect(within(catalog()).getByText("Коррекция бровей")).toBeInTheDocument();
  });
});

describe("validateOwnService", () => {
  it("accepts a complete draft and refuses each missing field", () => {
    expect(validateOwnService({ name: "X", description: "", duration: "60", price: "0" })).toEqual({});
    expect(validateOwnService({ name: " ", description: "", duration: "1.5", price: "-1" })).toEqual({
      name: ERR_NAME,
      duration: ERR_DURATION,
      price: ERR_PRICE,
    });
  });
});
