import type { LoadedImage } from "./loadedImage";

export interface ParseRequest {
  file: File;
}

export type ParseResponse =
  | { type: "progress"; fraction: number }
  | { type: "done"; image: LoadedImage }
  | { type: "error"; message: string; line: number };
