// Parses a dropped file off the main thread and transfers the planes back.

import { loadFromBytes } from "./loadedImage";
import { HtrdrParseError } from "./parse";
import type { ParseRequest, ParseResponse } from "./parseMessages";

const post = (message: ParseResponse, transfer: Transferable[] = []) => self.postMessage(message, { transfer });

self.onmessage = async (event: MessageEvent<ParseRequest>) => {
  try {
    const bytes = new Uint8Array(await event.data.file.arrayBuffer());
    const image = loadFromBytes(bytes, { onProgress: (fraction) => post({ type: "progress", fraction }) });
    post({ type: "done", image }, image.planes.map((p) => p.buffer));
  } catch (err) {
    const line = err instanceof HtrdrParseError ? err.line : 0;
    post({ type: "error", message: err instanceof Error ? err.message : String(err), line });
  }
};
