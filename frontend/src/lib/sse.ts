import type { SSEEvent } from "../types";

const API_URL = "http://localhost:8001/api/match";

export type StreamInput = { file: File } | { text: string };

export async function streamMatch(
  input: StreamInput,
  onEvent: (e: SSEEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const form = new FormData();
  if ("file" in input) {
    form.append("file", input.file);
  } else {
    form.append("resume_text", input.text);
  }

  const resp = await fetch(API_URL, {
    method: "POST",
    body: form,
    signal,
  });

  if (!resp.ok || !resp.body) {
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(`match request failed: ${resp.status} ${detail}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  // SSE messages are delimited by a blank line. sse-starlette emits CRLF
  // (`\r\n\r\n`); the spec also allows LF-only (`\n\n`). Match both.
  const delim = /\r?\n\r?\n/;

  const flush = () => {
    while (true) {
      const m = buf.match(delim);
      if (!m) return;
      const idx = m.index!;
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + m[0].length);
      const payload = raw
        .split(/\r?\n/)
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).replace(/^ /, ""))
        .join("\n")
        .trim();
      if (!payload) continue;
      try {
        onEvent(JSON.parse(payload) as SSEEvent);
      } catch (err) {
        console.error("sse parse error", err, payload);
      }
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (value) buf += decoder.decode(value, { stream: true });
    flush();
    if (done) break;
  }
  // Flush any trailing UTF-8 bytes and parse any tail event missing a
  // blank-line terminator (sse-starlette always sends one, but be safe).
  buf += decoder.decode();
  if (buf.trim()) {
    buf += "\n\n";
    flush();
  }
}
