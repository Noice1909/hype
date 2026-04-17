import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Compass,
  Brain,
  Wrench,
  CheckCircle2,
  XCircle,
  MessageSquare,
  Activity,
} from "lucide-react";

const EVENT_ICONS = {
  routing: Compass,
  thinking: Brain,
  tool_call: Wrench,
  tool_result: CheckCircle2,
  llm_response: MessageSquare,
  response_start: MessageSquare,
  response_end: CheckCircle2,
  recovery: Activity,
  error: XCircle,
};

function formatTime(ts) {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function EventItem({ event }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = EVENT_ICONS[event.type] || Activity;

  let summary = event.type;
  const d = event.data;

  switch (event.type) {
    case "routing":
      summary = `Route: ${d.domain || "?"} -> ${d.agent || "?"}`;
      break;
    case "thinking":
      summary = `Thinking (turn ${d.turn || "?"})`;
      break;
    case "tool_call":
      summary = `Tool: ${(d.tools || []).join(", ")}`;
      break;
    case "tool_result":
      summary = `${d.tool}: ${d.status} (${Math.round(d.duration_ms || 0)}ms)`;
      break;
    case "llm_response":
      summary = "LLM response";
      break;
    case "response_start":
      summary = "Streaming started";
      break;
    case "response_end":
      summary = "Response complete";
      break;
    case "recovery":
      summary = `Recovery: ${d.layer}`;
      break;
    case "error":
      summary = `Error: ${d.error || "unknown"}`;
      break;
  }

  const hasDetail =
    event.type === "tool_result" ||
    event.type === "llm_response" ||
    event.type === "error";

  return (
    <div className="event-item">
      <button
        className="event-item-header"
        onClick={() => hasDetail && setExpanded(!expanded)}
        disabled={!hasDetail}
      >
        <Icon size={13} className={`event-icon event-icon--${event.type}`} />
        <span className="event-summary">{summary}</span>
        <span className="event-time">{formatTime(event.ts)}</span>
        {hasDetail &&
          (expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />)}
      </button>

      {expanded && (
        <div className="event-detail">
          {event.type === "tool_result" && d.result && (
            <pre className="event-detail-pre">
              {String(d.result).slice(0, 500)}
              {String(d.result).length > 500 ? "..." : ""}
            </pre>
          )}
          {event.type === "llm_response" && d.text && (
            <pre className="event-detail-pre">
              {d.text.slice(0, 300)}
              {d.text.length > 300 ? "..." : ""}
            </pre>
          )}
          {event.type === "error" && (
            <pre className="event-detail-pre">{d.error}</pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function EventTimeline({ events }) {
  const [open, setOpen] = useState(false);

  if (!events || events.length === 0) return null;

  return (
    <div className="event-timeline">
      <button
        className="event-timeline-toggle"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <Activity size={14} />
        <span>Agent Activity ({events.length})</span>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>

      {open && (
        <div className="event-timeline-list">
          {events.map((evt, i) => (
            <EventItem key={i} event={evt} />
          ))}
        </div>
      )}
    </div>
  );
}
