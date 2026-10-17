// Whole-window file drop. Once an image is open the overlay splits in two:
// drop on the left half to replace A, on the right half to compare as B.

export type DropSlot = "a" | "b";

const hasFiles = (e: DragEvent) => e.dataTransfer?.types.includes("Files") ?? false;

export function attachDropZone(
  overlay: HTMLElement,
  /** Whether a dropped file may go to slot B. */
  allowB: () => boolean,
  onFiles: (files: File[], slot: DropSlot) => void,
): void {
  const zones = [...overlay.querySelectorAll<HTMLElement>(".drop-zone")];
  const zone = (slot: DropSlot) => zones.find((z) => z.dataset.slot === slot)!;
  const slotAt = (e: DragEvent): DropSlot => (allowB() && e.clientX > window.innerWidth / 2 ? "b" : "a");

  // dragenter/dragleave fire for every child element; count them.
  let depth = 0;
  const hide = () => {
    depth = 0;
    overlay.hidden = true;
  };

  window.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth++;
    zone("b").hidden = !allowB();
    overlay.hidden = false;
  });
  window.addEventListener("dragover", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer!.dropEffect = "copy";
    const slot = slotAt(e);
    for (const z of zones) z.classList.toggle("active", z.dataset.slot === slot);
  });
  window.addEventListener("dragleave", (e) => {
    if (!hasFiles(e)) return;
    depth = Math.max(0, depth - 1);
    if (depth === 0) overlay.hidden = true;
  });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    hide();
    const files = Array.from(e.dataTransfer!.files);
    if (files.length > 0) onFiles(files, slotAt(e));
  });
}
