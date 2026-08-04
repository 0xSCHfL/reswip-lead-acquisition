import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { NewJobPage } from "./NewJobPage";

vi.mock("../api/client", () => ({
  api: { listInputFiles: vi.fn().mockResolvedValue([]), createJob: vi.fn() },
}));

describe("NewJobPage", () => {
  it("starts with automatic iQualif enrichment", async () => {
    render(<MemoryRouter><NewJobPage /></MemoryRouter>);
    expect(screen.getByText("Enrich your iQualif database.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start enrichment" })).toBeInTheDocument();
  });
});
