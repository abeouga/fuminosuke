import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SireNamesPage } from "./SireNamesPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("lists and adds sire-name OCR candidates", async () => {
  const names = [
    { id: "sire-1", name: "勝忠平", createdAt: "2026-07-30T00:00:00Z" },
    { id: "sire-2", name: "光晴重", createdAt: "2026-07-30T00:00:00Z" },
    { id: "sire-3", name: "重波平", createdAt: "2026-07-30T00:00:00Z" },
    { id: "sire-4", name: "百合茂", createdAt: "2026-07-30T00:00:00Z" },
  ];
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as { name: string };
      const created = {
        id: "sire-5",
        name: body.name,
        createdAt: "2026-07-30T01:00:00Z",
      };
      names.push(created);
      return new Response(JSON.stringify(created), { status: 201 });
    }
    return new Response(JSON.stringify({
      items: names,
      count: names.length,
      allowedSuffixes: ["ET"],
    }), { status: 200 });
  }));

  render(<SireNamesPage />);

  expect(await screen.findByText("勝忠平")).toBeInTheDocument();
  expect(screen.getByText("光晴重")).toBeInTheDocument();
  expect(screen.getAllByText(/勝忠平ET/).length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "勝忠平を削除" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "光晴重を削除" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("種雄牛名"), {
    target: { value: "美津照重" },
  });
  fireEvent.click(screen.getByRole("button", { name: "候補に追加" }));

  expect(await screen.findByText("美津照重")).toBeInTheDocument();
  expect(screen.getByText("5件")).toBeInTheDocument();
});
