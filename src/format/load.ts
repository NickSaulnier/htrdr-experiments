// Main-thread side of the parse worker: one worker per file, cancellable.

import type { LoadedImage } from "./loadedImage";
import type { ParseRequest, ParseResponse } from "./parseMessages";

export interface LoadJob {
  promise: Promise<LoadedImage>;
  cancel(): void;
}

export class LoadError extends Error {
  constructor(
    message: string,
    readonly line: number,
  ) {
    super(message);
    this.name = "LoadError";
  }
}

export function loadImageFile(file: File, onProgress: (fraction: number) => void): LoadJob {
  const worker = new Worker(new URL("./parse.worker.ts", import.meta.url), { type: "module" });
  let reject!: (reason: unknown) => void;
  const promise = new Promise<LoadedImage>((resolve, rej) => {
    reject = rej;
    worker.onmessage = (event: MessageEvent<ParseResponse>) => {
      const msg = event.data;
      if (msg.type === "progress") {
        onProgress(msg.fraction);
        return;
      }
      worker.terminate();
      if (msg.type === "done") resolve(msg.image);
      else rej(new LoadError(msg.message, msg.line));
    };
    worker.onerror = (event) => {
      worker.terminate();
      rej(new LoadError(event.message || "the parser worker failed", 0));
    };
  });
  worker.postMessage({ file } satisfies ParseRequest);
  return {
    promise,
    cancel() {
      worker.terminate();
      reject(new DOMException("Load cancelled", "AbortError"));
    },
  };
}
