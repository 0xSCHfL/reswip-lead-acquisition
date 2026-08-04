import { describe, expect, it } from "vitest";

describe("API client", () => {
  it("builds an encoded artifact URL", async () => {
    const { api } = await import("./client");
    expect(api.artifactUrl("job-1", "report final.csv")).toContain("report%20final.csv");
  });
});
