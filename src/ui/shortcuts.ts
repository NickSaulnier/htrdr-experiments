// Keyboard shortcuts. The same table drives the key handling and the help
// overlay, so they can't drift apart.

export type ShortcutAction = "fit" | "actualSize" | "zoomIn" | "zoomOut" | "swap" | "cycleCompare" | "toggleMode" | "reset" | "help";

export interface Shortcut {
  keys: readonly string[];
  action: ShortcutAction;
  label: string;
}

export const SHORTCUTS: readonly Shortcut[] = [
  { keys: ["F", "0"], action: "fit", label: "Fit the image (also double-click)" },
  { keys: ["1"], action: "actualSize", label: "Zoom to 1:1" },
  { keys: ["+"], action: "zoomIn", label: "Zoom in" },
  { keys: ["−"], action: "zoomOut", label: "Zoom out" },
  { keys: ["S"], action: "swap", label: "Swap A and B (press repeatedly to flip between them)" },
  { keys: ["C"], action: "cycleCompare", label: "Cycle compare: off, split, difference" },
  { keys: ["M"], action: "toggleMode", label: "Toggle image and map mode" },
  { keys: ["R"], action: "reset", label: "Reset exposure, white, ranges and thresholds" },
  { keys: ["?"], action: "help", label: "Show or hide this help" },
];

/** The action for a key event, or null. Matches on `key`, so layouts with + on a shifted key work. */
export function actionFor(e: Pick<KeyboardEvent, "key">): ShortcutAction | null {
  const key = e.key.length === 1 ? e.key.toUpperCase() : e.key;
  switch (key) {
    case "F":
    case "0":
      return "fit";
    case "1":
      return "actualSize";
    case "+":
    case "=":
      return "zoomIn";
    case "-":
    case "_":
      return "zoomOut";
    case "S":
      return "swap";
    case "C":
      return "cycleCompare";
    case "M":
      return "toggleMode";
    case "R":
      return "reset";
    case "?":
      return "help";
    default:
      return null;
  }
}

/** Don't steal keys from form fields, or from browser and OS shortcuts. */
function ignored(e: KeyboardEvent): boolean {
  if (e.ctrlKey || e.metaKey || e.altKey) return true;
  const t = e.target;
  return t instanceof HTMLInputElement || t instanceof HTMLSelectElement || t instanceof HTMLTextAreaElement;
}

export function attachShortcuts(run: (action: ShortcutAction) => boolean): void {
  window.addEventListener("keydown", (e) => {
    if (ignored(e)) return;
    const action = actionFor(e);
    // `run` returns false when the action doesn't apply right now, so the
    // key keeps its default behaviour.
    if (action && run(action)) e.preventDefault();
  });
}

export function renderHelp(list: HTMLElement): void {
  list.replaceChildren(
    ...SHORTCUTS.map((s) => {
      const row = document.createElement("div");
      const keys = document.createElement("dt");
      keys.append(
        ...s.keys.flatMap((k, i) => {
          const kbd = document.createElement("kbd");
          kbd.textContent = k;
          return i === 0 ? [kbd] : [document.createTextNode(" "), kbd];
        }),
      );
      const label = document.createElement("dd");
      label.textContent = s.label;
      row.append(keys, label);
      return row;
    }),
  );
}
