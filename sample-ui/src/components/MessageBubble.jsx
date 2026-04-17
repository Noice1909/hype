import { useState } from "react";
import { User, Bot, RefreshCw, AlertCircle } from "lucide-react";
import ThinkingIndicator from "./ThinkingIndicator";
import StreamingText from "./StreamingText";
import MarkdownRenderer from "./MarkdownRenderer";
import EventTimeline from "./EventTimeline";
import ToolCallCard from "./ToolCallCard";
import EvalPanel, { EvalBadge } from "./EvalPanel";
import SuggestionChips from "./SuggestionChips";

function AssistantToolCalls({ events }) {
  // Pair tool_call events with their corresponding tool_result
  const toolCalls = events.filter((e) => e.type === "tool_call");
  const toolResults = events.filter((e) => e.type === "tool_result");

  if (toolCalls.length === 0) return null;

  return (
    <div className="tool-calls-section">
      {toolCalls.map((tc, i) => {
        // Match by tool name — each tool_call may have multiple tools
        const tools = tc.data?.tools || [];
        const matchingResults = tools.map((toolName) =>
          toolResults.find((tr) => tr.data?.tool === toolName)
        );
        return tools.map((toolName, j) => (
          <ToolCallCard
            key={`${i}-${j}`}
            toolCallEvent={{ ...tc, data: { ...tc.data, tools: [toolName] } }}
            resultEvent={matchingResults[j] || null}
          />
        ));
      })}
    </div>
  );
}

function MetadataBadges({ metadata }) {
  if (!metadata) return null;
  return (
    <>
      {metadata.agent && (
        <span className="meta-badge">{metadata.agent}</span>
      )}
      {metadata.loopUsed && (
        <span className="meta-badge">{metadata.loopUsed}</span>
      )}
      {metadata.turnsUsed != null && (
        <span className="meta-badge">{metadata.turnsUsed} turns</span>
      )}
      {metadata.durationMs != null && (
        <span className="meta-badge">
          {(metadata.durationMs / 1000).toFixed(1)}s
        </span>
      )}
    </>
  );
}

export default function MessageBubble({ message, onRetry, onSuggestionSelect, evalData, evalLoading }) {
  const [showEvalPanel, setShowEvalPanel] = useState(false);

  if (message.role === "user") {
    return (
      <div className="message message-user">
        <div className="message-avatar user-avatar">
          <User size={16} />
        </div>
        <div className="message-content user-content">{message.content}</div>
      </div>
    );
  }

  // Assistant message

  const {
    content,
    streamingText,
    phase,
    events = [],
    metadata,
    error,
    suggestions = [],
  } = message;

  const showThinking =
    phase === "waiting" || phase === "thinking" || phase === "tool_call";
  const showStreaming = phase === "streaming";
  const showDone = phase === "done" && content;
  const showError = phase === "error";

  return (
    <div className="message message-assistant">
      <div className="message-avatar assistant-avatar">
        <Bot size={16} />
      </div>
      <div className="message-content assistant-content">
        {/* Live activity indicator — visible during all non-done phases */}
        {showThinking && <ThinkingIndicator phase={phase} events={events} />}

        {/* Tool calls — show as they happen */}
        <AssistantToolCalls events={events} />

        {/* Streaming text with cursor */}
        {showStreaming && (
          <StreamingText text={streamingText} />
        )}

        {/* Final rendered content */}
        {showDone && (
          <MarkdownRenderer content={content} />
        )}

        {/* Suggested follow-up questions */}
        {showDone && suggestions.length > 0 && (
          <SuggestionChips
            suggestions={suggestions}
            onSelect={onSuggestionSelect}
          />
        )}

        {/* Error */}
        {showError && (
          <div className="message-error">
            <AlertCircle size={16} />
            <span>{error || "Something went wrong"}</span>
            {onRetry && (
              <button className="retry-btn" onClick={onRetry}>
                <RefreshCw size={14} />
                Retry
              </button>
            )}
          </div>
        )}

        {/* Metadata badges + eval badge */}
        <div className="message-metadata">
          <MetadataBadges metadata={metadata} />
          {(evalData || evalLoading) && (
            <EvalBadge
              evalData={evalData}
              isLoading={evalLoading}
              onClick={() => setShowEvalPanel((v) => !v)}
            />
          )}
        </div>

        {/* Eval detail panel — toggled by badge click */}
        {showEvalPanel && (
          <EvalPanel evalData={evalData} isLoading={evalLoading} />
        )}

        {/* Event timeline */}
        {events.length > 0 && <EventTimeline events={events} />}
      </div>
    </div>
  );
}
