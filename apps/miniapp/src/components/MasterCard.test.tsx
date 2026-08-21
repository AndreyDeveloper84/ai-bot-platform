/**
 * DRF-1224 — «★ 0.00» must never reach a user, in the Mini App either.
 *
 * The pilot bug was found in the bot's discovery card, but the same value
 * flows to this component through `GET /masters` (`_master_to_dict` in
 * apps/miniapp_api/views.py serialises the mirror rating verbatim, so
 * "0.00" arrives as a STRING). `master.rating ? …` treats "0.00" as
 * truthy, so the existing guard passes it straight through as «★ 0.0».
 *
 * Rating domain is 1..5 — a zero can only mean «no reviews behind it».
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MasterCard } from "./MasterCard";
import type { Master } from "../lib/api";

const base: Master = {
  id: "mst-1",
  name: "Архипкин Денис",
  specialization: "массажист",
  bio: "",
  experience: "",
  rating: null,
  photo_url: "",
};

describe("MasterCard rating", () => {
  it("does not render a star for a 0.00 rating", () => {
    render(<MasterCard master={{ ...base, rating: "0.00" }} onSelect={vi.fn()} />);
    expect(screen.queryByText(/★/)).toBeNull();
    expect(screen.queryByLabelText(/Рейтинг/)).toBeNull();
  });

  it("does not render a star for a null rating", () => {
    render(<MasterCard master={base} onSelect={vi.fn()} />);
    expect(screen.queryByText(/★/)).toBeNull();
  });

  it("still renders a real rating", () => {
    render(<MasterCard master={{ ...base, rating: "4.90" }} onSelect={vi.fn()} />);
    expect(screen.getByText(/★ 4\.9/)).toBeTruthy();
  });
});
