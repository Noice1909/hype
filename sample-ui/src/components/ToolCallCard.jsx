import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  XCircle,
  Wrench,
  Clock,
} from "lucide-react";

export default function ToolCallCard({ toolCallEvent, resultEvent }) {
  const [expanded, setExpanded] = useState(false);

  const toolName = toolCallEvent?.data?.tools?.[0] || "unknown";
  const status = resultEvent?.data?.status || "running";
  const duration = resultEvent?.data?.duration_ms;
  const result = resultEvent?.data?.result;
  const cached = resultEvent?.data?.cached;

  const isSuccess = status === "success";
  const isRunning = !resultEvent;

  // Truncate result for display
  const resultLines = result ? String(result).split("\n") : [];
  const isTruncated = resultLines.length > 5;
  const displayResult = expanded
    ? result
    : resultLines.slice(0, 5).join("\n") + (isTruncated ? "\n..." : "");

  return (
    <div className={`tool-call-card ${isRunning ? "running" : status}`}>
      <button
        className="tool-call-header"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <div className="tool-call-left">
          <Wrench size={14} className="tool-icon" />
          <span className="tool-name">{toolName}</span>
          {cached && <span className="tool-cached-badge">cached</span>}
        </div>
        <div className="tool-call-right">
          {isRunning ? (
            <span className="tool-status running">
              <Clock size={12} className="spin" /> Running
            </span>
          ) : isSuccess ? (
            <span className="tool-status success">
              <CheckCircle2 size={12} />
              {duration != null && ` ${Math.round(duration)}ms`}
            </span>
          ) : (
            <span className="tool-status error">
              <XCircle size={12} /> Error
            </span>
          )}
          {result && (expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />)}
        </div>
      </button>

      {expanded && result && (
        <div className="tool-call-body">
          <pre className="tool-result">{displayResult}</pre>
          {isTruncated && !expanded && (
            <button
              className="tool-expand-btn"
              onClick={() => setExpanded(true)}
            >
              Show all ({resultLines.length} lines)
            </button>
          )}
        </div>
      )}
    </div>
  );
}
