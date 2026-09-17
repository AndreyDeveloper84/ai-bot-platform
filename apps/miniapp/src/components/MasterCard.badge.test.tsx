/**
 * MasterCard — бейдж «Принимает сегодня» и чипы категорий (DRF-1814, часть B).
 *
 * B1 — без пропсов карточка клиента не меняется: ни бейджа, ни чипов;
 * B2 — acceptsToday=true → бейдж; false → нет (одни данные, две половины);
 * B3 — categories → чипы в порядке пропса; пустой массив — ничего.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Master } from "../lib/api";
import { ACCEPTS_TODAY_LABEL, MasterCard } from "./MasterCard";

const master: Master = {
  id: "m-1",
  name: "Анна Петрова",
  specialization: "",
  bio: "",
  experience: "",
  rating: null,
  photo_url: "",
};

describe("MasterCard — бейдж и чипы", () => {
  it("B1: без пропсов — ни бейджа, ни чипов", () => {
    const { container } = render(<MasterCard master={master} onSelect={() => {}} />);
    expect(screen.queryByText(ACCEPTS_TODAY_LABEL)).toBeNull();
    expect(container.querySelector(".master-card__chip")).toBeNull();
  });

  it("B2: бейдж только при acceptsToday=true", () => {
    const shown = render(<MasterCard master={master} onSelect={() => {}} acceptsToday />);
    expect(screen.getByText(ACCEPTS_TODAY_LABEL)).toBeInTheDocument();
    shown.unmount();

    render(<MasterCard master={master} onSelect={() => {}} acceptsToday={false} />);
    expect(screen.queryByText(ACCEPTS_TODAY_LABEL)).toBeNull();
  });

  it("B3: чипы — в порядке пропса; пустой массив — ничего", () => {
    const { container, unmount } = render(
      <MasterCard master={master} onSelect={() => {}} categories={["Педикюр", "Маникюр"]} />,
    );
    expect(Array.from(container.querySelectorAll(".master-card__chip")).map((c) => c.textContent)).toEqual([
      "Педикюр",
      "Маникюр",
    ]);
    unmount();
    const empty = render(<MasterCard master={master} onSelect={() => {}} categories={[]} />);
    expect(empty.container.querySelector(".master-card__chip")).toBeNull();
  });
});
