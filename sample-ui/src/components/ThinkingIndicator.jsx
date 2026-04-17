export default function ThinkingIndicator({ phase, events }) {
  let label = "Thinking";

  // Determine label from the most recent event
  const lastEvent = events.length > 0 ? events[events.length - 1] : null;

  if (lastEvent?.type === "tool_result") {
    label = "Observing results";
  } else if (phase === "tool_call") {
    const lastToolCall = [...events]
      .reverse()
      .find((e) => e.type === "tool_call");
    if (lastToolCall?.data?.tools?.length) {
      label = `Running ${lastToolCall.data.tools.join(", ")}`;
    } else {
      label = "Running tool";
    }
  } else if (phase === "thinking") {
    const lastThinking = [...events]
      .reverse()
      .find((e) => e.type === "thinking");
    if (lastThinking?.data?.turn) {
      label = `Thinking (turn ${lastThinking.data.turn})`;
    }
  } else if (phase === "waiting") {
    label = "Starting";
  }

  return (
    <div className="thinking-indicator">
      <div className="thinking-dots">
        <span className="thinking-dot" />
        <span className="thinking-dot" />
        <span className="thinking-dot" />
      </div>
      <span className="thinking-label">{label}...</span>
    </div>
  );
}
