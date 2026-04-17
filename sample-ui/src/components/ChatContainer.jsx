import { useCallback } from "react";
import { useAutoScroll } from "../hooks/useAutoScroll";
import MessageBubble from "./MessageBubble";
import ScrollToBottom from "./ScrollToBottom";
import { Database } from "lucide-react";

export default function ChatContainer({ messages, isStreaming, onRetry, onSuggestionSelect, evalMap, evalLoadingSet }) {
  // Track the last message's streaming text for scroll dependency
  const lastMsg = messages[messages.length - 1];
  const scrollDep = lastMsg?.streamingText || lastMsg?.content || messages.length;

  const { scrollRef, isAtBottom, scrollToBottom } = useAutoScroll([
    scrollDep,
    messages.length,
  ]);

  const handleRetry = useCallback(
    (msg) => {
      if (onRetry) onRetry(msg);
    },
    [onRetry]
  );

  return (
    <div className="chat-container" ref={scrollRef}>
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="empty-state">
            <Database size={48} strokeWidth={1.5} />
            <h2>Neo4j Agent Chat</h2>
            <p>Ask questions about your graph database.</p>
            <div className="empty-examples">
              <button className="example-btn" disabled>
                Show me all node labels
              </button>
              <button className="example-btn" disabled>
                Find the top 10 connected nodes
              </button>
              <button className="example-btn" disabled>
                What relationships exist in the graph?
              </button>
            </div>
          </div>
        )}

        {messages.map((msg) => {
          const reqId = msg.metadata?.requestId;
          return (
            <MessageBubble
              key={msg.id}
              message={msg}
              onRetry={
                msg.phase === "error" ? () => handleRetry(msg) : undefined
              }
              onSuggestionSelect={onSuggestionSelect}
              evalData={reqId ? evalMap?.[reqId] : undefined}
              evalLoading={reqId ? evalLoadingSet?.has(reqId) : false}
            />
          );
        })}

        {/* Scroll sentinel */}
        <div style={{ height: 1 }} />
      </div>

      <ScrollToBottom visible={!isAtBottom} onClick={scrollToBottom} />
    </div>
  );
}
