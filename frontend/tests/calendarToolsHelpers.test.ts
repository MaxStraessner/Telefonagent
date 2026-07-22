import { describe, expect, it } from "vitest";
import { latestUserUtterance } from "../src/features/realtime/calendarTools";

describe("Kalenderwerkzeug-Kontext", () => {
  it("verwendet die tatsächliche letzte Benutzeräußerung für die Buchungsbestätigung", () => {
    const context = { context: { history: [
      { role: "user", content: [{ type: "input_audio", transcript: "Morgen um elf" }] },
      { role: "assistant", content: [{ type: "output_audio", transcript: "Soll ich buchen?" }] },
      { role: "user", content: [{ type: "input_audio", transcript: "Ja, das passt." }] },
    ] } };
    expect(latestUserUtterance(context)).toBe("Ja, das passt.");
  });

  it("liefert ohne Benutzertext keine erfundene Zustimmung", () => {
    expect(latestUserUtterance({ context: { history: [] } })).toBe("");
  });
});
