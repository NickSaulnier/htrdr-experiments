import { describe, expect, it } from "vitest";
import { SHORTCUTS, actionFor } from "../src/ui/shortcuts";

describe("shortcuts", () => {
  it("map keys to actions, case-insensitively", () => {
    expect(actionFor({ key: "f" })).toBe("fit");
    expect(actionFor({ key: "F" })).toBe("fit");
    expect(actionFor({ key: "0" })).toBe("fit");
    expect(actionFor({ key: "1" })).toBe("actualSize");
    expect(actionFor({ key: "+" })).toBe("zoomIn");
    expect(actionFor({ key: "=" })).toBe("zoomIn");
    expect(actionFor({ key: "-" })).toBe("zoomOut");
    expect(actionFor({ key: "s" })).toBe("swap");
    expect(actionFor({ key: "c" })).toBe("cycleCompare");
    expect(actionFor({ key: "m" })).toBe("toggleMode");
    expect(actionFor({ key: "r" })).toBe("reset");
    expect(actionFor({ key: "?" })).toBe("help");
    expect(actionFor({ key: "x" })).toBeNull();
    expect(actionFor({ key: "Enter" })).toBeNull();
  });

  it("list every action exactly once in the help", () => {
    const actions = SHORTCUTS.map((s) => s.action);
    expect(new Set(actions).size).toBe(actions.length);
    for (const s of SHORTCUTS) {
      // Each listed key really triggers the listed action ("−" is the typographic minus).
      for (const key of s.keys) expect(actionFor({ key: key === "−" ? "-" : key })).toBe(s.action);
    }
  });
});
