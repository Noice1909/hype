import { useCallback, useEffect, useRef } from "react";
import { useSSE } from "./hooks/useSSE";
import { useEvalMap } from "./hooks/useEval";
import ChatContainer from "./components/ChatContainer";
import MessageInput from "./components/MessageInput";
import ThemeToggle from "./components/ThemeToggle";
import { Trash2 } from "lucide-react";
import "./App.css";

export default function App() {
  const { messages, isStreaming, error, sendQuery, cancelStream, clearMessages } =
    useSSE();
  const { evalMap, loadingSet, startEval } = useEvalMap();

  // Track which request IDs we've already triggered eval for
  const triggeredRef = useRef(new Set());

  // Auto-trigger eval polling when a message gets a requestId
  useEffect(() => {
    for (const msg of messages) {
      const reqId = msg.metadata?.requestId;
      if (reqId && !triggeredRef.current.has(reqId)) {
        triggeredRef.current.add(reqId);
        startEval(reqId);
      }
    }
  }, [messages, startEval]);

  const handleRetry = useCallback(
    (msg) => {
      // Find the user message before this assistant message
      const idx = messages.indexOf(msg);
      if (idx > 0) {
        const userMsg = messages[idx - 1];
        if (userMsg?.role === "user") {
          sendQuery(userMsg.content);
        }
      }
    },
    [messages, sendQuery]
  );

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-left">
          <h1 className="app-title">Neo4j Agent</h1>
        </div>
        <div className="app-header-right">
          {messages.length > 0 && (
            <button
              className="clear-btn"
              onClick={clearMessages}
              aria-label="Clear chat"
              title="Clear chat"
            >
              <Trash2 size={16} />
            </button>
          )}
          <ThemeToggle />
        </div>
      </header>

      <main className="app-main">
        <ChatContainer
          messages={messages}
          isStreaming={isStreaming}
          onRetry={handleRetry}
          onSuggestionSelect={sendQuery}
          evalMap={evalMap}
          evalLoadingSet={loadingSet}
        />
      </main>

      <footer className="app-footer">
        <MessageInput
          onSend={sendQuery}
          onCancel={cancelStream}
          isStreaming={isStreaming}
        />
        {error && <div className="global-error">{error}</div>}
      </footer>
    </div>
  );
}
