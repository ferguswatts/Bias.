import { describe, it, expect } from "vitest";
import { matchJournalist, type DataFile } from "../../extension/src/lib/match";

const MOCK_DATA: DataFile = {
  version: "2026-03-29",
  journalists: {
    "katie-bradford-1news": {
      name: "Katie Bradford",
      aliases: [],
      outlet: "1News",
      beat: "Parliamentary politics",
      article_count: 142,
      confidence: "high",
      distribution: { left: 62, centre_left: 22, centre: 11, centre_right: 4, right: 1 },
      connections: [
        { type: "family", target: "Sue Bradford", role: "Former Green MP", source: "https://en.wikipedia.org/wiki/Sue_Bradford" },
      ],
      facts: [],
      methodology: "Based on 142 articles scored by AI",
    },
    "heather-du-plessis-allan-newstalkzb": {
      name: "Heather du Plessis-Allan",
      aliases: ["Heather du Plessis Allan", "HDPA"],
      outlet: "Newstalk ZB",
      beat: "Current affairs",
      article_count: 200,
      confidence: "high",
      distribution: { left: 5, centre_left: 10, centre: 20, centre_right: 40, right: 25 },
      connections: [],
      facts: [],
      methodology: "Based on 200 articles scored by AI",
    },
  },
};

describe("matchJournalist", () => {
  it("matches by exact slug", () => {
    const result = matchJournalist("Katie Bradford", "1news.co.nz", MOCK_DATA);
    expect(result).not.toBeNull();
    expect(result!.journalist.name).toBe("Katie Bradford");
  });

  it("matches by alias", () => {
    const result = matchJournalist("HDPA", "newstalkzb.com", MOCK_DATA);
    expect(result).not.toBeNull();
    expect(result!.journalist.name).toBe("Heather du Plessis-Allan");
  });

  it("matches by name across outlets", () => {
    const result = matchJournalist("Katie Bradford", "stuff.co.nz", MOCK_DATA);
    expect(result).not.toBeNull();
    expect(result!.journalist.name).toBe("Katie Bradford");
  });

  it("returns null for unknown journalist", () => {
    const result = matchJournalist("Unknown Person", "nzherald.co.nz", MOCK_DATA);
    expect(result).toBeNull();
  });

  it("skips staff bylines", () => {
    const result = matchJournalist("Staff Reporter", "nzherald.co.nz", MOCK_DATA);
    expect(result).toBeNull();
  });

  it("skips wire service bylines", () => {
    const result = matchJournalist("Reuters", "stuff.co.nz", MOCK_DATA);
    expect(result).toBeNull();
  });

  it("handles joint bylines — matches first author", () => {
    const result = matchJournalist("Katie Bradford and Someone Else", "1news.co.nz", MOCK_DATA);
    expect(result).not.toBeNull();
    expect(result!.journalist.name).toBe("Katie Bradford");
  });

  it("strips 'By' prefix before matching", () => {
    const result = matchJournalist("By Katie Bradford", "1news.co.nz", MOCK_DATA);
    expect(result).not.toBeNull();
  });

  it("is case-insensitive", () => {
    const result = matchJournalist("KATIE BRADFORD", "1news.co.nz", MOCK_DATA);
    expect(result).not.toBeNull();
  });
});
