import { useCallback, useRef, useState } from "react";
import { fetchSSE } from "../utils/sseClient";

const API_URL = "/api/v1/query/stream";

/**
 * Hook for streaming agent responses via SSE.
 *
 * Uses a ref-based approach for accumulating message state during
 * streaming to avoid React state batching issues, then flushes
 * to React state for rendering.
 */
export function useSSE() {
  const [messages, setMessages] = useState([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);
  // Ref to hold the live assistant message during streaming
  const liveMsgRef = useRef(null);
  // Track conversation ID for multi-turn context
  const conversationIdRef = useRef(null);

  const sendQuery = useCallback(async (query, opts = {}) => {
    if (!query.trim() || isStreaming) return;
    // Support both sendQuery(query, apiKey) and sendQuery(query, { apiKey, cypher })
    // for backwards compat with existing callers.
    const apiKey = typeof opts === "string" ? opts : opts.apiKey || "";
    const cypher = typeof opts === "string" ? null : opts.cypher || null;

    // Add user message
    const userMsg = { role: "user", content: query, id: Date.now() };
    const assistantMsg = {
      role: "assistant",
      id: Date.now() + 1,
      content: "",
      streamingText: "",
      isStreaming: true,
      events: [],
      metadata: null,
      phase: "waiting",
      suggestions: [],
    };

    // Store live message in ref for direct mutation
    liveMsgRef.current = assistantMsg;

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);
    setError(null);

    // Abort any previous stream
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const headers = {};
    if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;

    // Helper to flush the live message ref into React state
    const flush = () => {
      const snapshot = { ...liveMsgRef.current, events: [...liveMsgRef.current.events] };
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = snapshot;
        return updated;
      });
    };

    try {
      const requestBody = {
        query,
        stream: true,
        conversation_id: conversationIdRef.current,
      };
      if (cypher) requestBody.cypher = cypher;

      const stream = fetchSSE(API_URL, requestBody, {
        headers,
        signal: controller.signal,
      });

      for await (const { event, data } of stream) {
        if (controller.signal.aborted) break;

        const msg = liveMsgRef.current;

        switch (event) {
          case "start":
            msg.conversationId = data.conversation_id;
            conversationIdRef.current = data.conversation_id;
            msg.phase = "waiting";
            break;

          case "routing":
            msg.events.push({ type: "routing", data, ts: Date.now() });
            break;

          case "thinking":
            msg.phase = "thinking";
            msg.streamingText = "";
            msg.events.push({ type: "thinking", data, ts: Date.now() });
            break;

          case "tool_call":
            msg.phase = "tool_call";
            msg.streamingText = "";
            msg.events.push({ type: "tool_call", data, ts: Date.now() });
            break;

          case "tool_result":
            msg.events.push({ type: "tool_result", data, ts: Date.now() });
            break;

          case "llm_response":
            if (data.text && msg.phase !== "streaming") {
              msg.content = data.text;
              msg.phase = "done";
            }
            msg.events.push({ type: "llm_response", data, ts: Date.now() });
            break;

          case "agent_done":
            if (!msg.content && msg.phase !== "streaming") {
              const llmEvt = msg.events.find((e) => e.type === "llm_response");
              if (llmEvt?.data?.text) {
                msg.content = llmEvt.data.text;
                msg.phase = "done";
              }
            }
            msg.events.push({ type: "agent_done", data, ts: Date.now() });
            break;

          case "response_start":
            msg.phase = "streaming";
            msg.streamingText = "";
            break;

          case "token":
            msg.phase = "streaming";
            msg.streamingText = (msg.streamingText || "") + (data.text || "");
            break;

          case "response_end":
            msg.content = data.full_text || msg.streamingText || "";
            msg.streamingText = "";
            msg.phase = "done";
            break;

          case "result":
            // result.response is the authoritative full text from
            // the backend — always prefer it over the potentially
            // truncated response_end.full_text.
            if (data.response) {
              msg.content = data.response;
            }
            msg.phase = "done";
            msg.metadata = {
              agent: data.agent,
              loopUsed: data.loop_used,
              turnsUsed: data.turns_used,
              durationMs: data.duration_ms,
              toolsCalled: data.tools_called,
              requestId: data.request_id,
            };
            break;

          case "done":
            msg.isStreaming = false;
            msg.phase = msg.phase === "error" ? "error" : "done";
            break;

          case "suggestions":
            msg.suggestions = data.suggestions || [];
            break;

          case "error":
            msg.isStreaming = false;
            msg.phase = "error";
            msg.error = data.error || "Unknown error";
            break;

          default:
            msg.events.push({ type: event, data, ts: Date.now() });
        }

        // Flush to React state after every event
        flush();
      }
    } catch (err) {
      if (err.name === "AbortError") return;
      setError(err.message);
      const msg = liveMsgRef.current;
      if (msg) {
        msg.isStreaming = false;
        msg.phase = "error";
        msg.error = err.message;
        const snapshot = { ...msg, events: [...msg.events] };
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = snapshot;
          return updated;
        });
      }
    } finally {
      setIsStreaming(false);
      liveMsgRef.current = null;
    }
  }, [isStreaming]);

  const cancelStream = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      setIsStreaming(false);
    }
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setError(null);
    conversationIdRef.current = null;
  }, []);

  return {
    messages,
    isStreaming,
    error,
    sendQuery,
    cancelStream,
    clearMessages,
  };
}
