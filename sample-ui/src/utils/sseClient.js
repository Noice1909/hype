/**
 * POST-based SSE client using fetch + ReadableStream.
 *
 * The native EventSource API only supports GET requests.
 * Our backend endpoint is POST /api/v1/query/stream, so we
 * use fetch with a readable stream and manually parse the
 * SSE wire format: "event: <type>\ndata: <json>\n\n"
 */

/**
 * Connect to a POST SSE endpoint and yield parsed events.
 *
 * @param {string} url       - Endpoint URL
 * @param {object} body      - JSON body to POST
 * @param {object} [options] - { headers, signal }
 * @yields {{ event: string, data: any }}
 */
export async function* fetchSSE(url, body, options = {}) {
  const { headers = {}, signal } = options;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    throw new Error(`SSE request failed: ${response.status} ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by double newlines (handle \r\n and \n)
      const parts = buffer.split(/\r?\n\r?\n/);
      // Keep the last (possibly incomplete) part in the buffer
      buffer = parts.pop() || "";

      for (const part of parts) {
        const parsed = parseSSEBlock(part);
        if (parsed) {
          yield parsed;
        }
      }
    }

    // Process any remaining buffer
    if (buffer.trim()) {
      const parsed = parseSSEBlock(buffer);
      if (parsed) {
        yield parsed;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Parse a single SSE text block into { event, data }.
 *
 * @param {string} block - Raw SSE text block
 * @returns {{ event: string, data: any } | null}
 */
function parseSSEBlock(block) {
  let event = "message";
  let dataLines = [];

  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
    // Ignore id:, retry:, and comments (:)
  }

  if (dataLines.length === 0) return null;

  const raw = dataLines.join("\n");
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    data = raw;
  }

  return { event, data };
}
