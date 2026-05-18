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

  // SSE messages are delimited by a blank line. Each message can carry
  // multiple `data:` lines whose values are concatenated with newlines.
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let sep: number;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const payload = raw
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).replace(/^ /, ""))
        .join("\n")
        .trim();
      if (!payload) continue;
      try {
        const evt = JSON.parse(payload) as SSEEvent;
        onEvent(evt);
      } catch (err) {
        console.error("sse parse error", err, payload);
      }
    }
  }
}
