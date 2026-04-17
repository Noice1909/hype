import { useState } from "react";
import {
  Activity,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  XCircle,
  Loader2,
  Target,
  ShieldCheck,
  Database,
  Code2,
} from "lucide-react";

const METRIC_CONFIG = [
  {
    key: "answer_relevancy",
    label: "Answer Relevancy",
    reasonKey: "answer_relevancy_reason",
    icon: Target,
    description: "How relevant is the answer to the query",
  },
  {
    key: "faithfulness",
    label: "Faithfulness",
    reasonKey: "faithfulness_reason",
    icon: ShieldCheck,
    description: "How faithful is the answer to the retrieved context",
  },
  {
    key: "geval",
    label: "Response Quality",
    reasonKey: "geval_reason",
    icon: Database,
    description: "Overall quality of the Neo4j response",
  },
  {
    key: "cypher_quality",
    label: "Cypher Quality",
    reasonKey: "cypher_quality_reason",
    icon: Code2,
    description: "Quality and correctness of generated Cypher queries",
  },
];

function scoreToColor(score) {
  if (score == null) return "var(--muted-foreground)";
  if (score >= 0.8) return "var(--success)";
  if (score >= 0.5) return "var(--warning)";
  return "var(--destructive)";
}

function scoreToLabel(score) {
  if (score == null) return "N/A";
  if (score >= 0.8) return "Good";
  if (score >= 0.5) return "Fair";
  return "Poor";
}

function OverallScore({ evalData }) {
  const scores = METRIC_CONFIG.map((m) => evalData[m.key]).filter(
    (s) => s != null
  );
  if (scores.length === 0) return null;
  const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
  const color = scoreToColor(avg);

  return (
    <div className="eval-overall">
      <div className="eval-overall-ring" style={{ "--score-color": color }}>
        <svg viewBox="0 0 36 36" className="eval-ring-svg">
          <path
            className="eval-ring-bg"
            d="M18 2.0845a 15.9155 15.9155 0 0 1 0 31.831a 15.9155 15.9155 0 0 1 0 -31.831"
          />
          <path
            className="eval-ring-fill"
            strokeDasharray={`${avg * 100}, 100`}
            style={{ stroke: color }}
            d="M18 2.0845a 15.9155 15.9155 0 0 1 0 31.831a 15.9155 15.9155 0 0 1 0 -31.831"
          />
        </svg>
        <span className="eval-overall-value" style={{ color }}>
          {Math.round(avg * 100)}
        </span>
      </div>
      <span className="eval-overall-label">Quality Score</span>
    </div>
  );
}

function MetricCard({ config, evalData }) {
  const [expanded, setExpanded] = useState(false);
  const score = evalData[config.key];
  const reason = evalData[config.reasonKey];
  const color = scoreToColor(score);
  const Icon = config.icon;
  const passed = score != null && score >= 0.5;

  return (
    <div className="eval-metric-card">
      <button
        className="eval-metric-header"
        onClick={() => reason && setExpanded(!expanded)}
        disabled={!reason}
        aria-expanded={expanded}
      >
        <div className="eval-metric-left">
          <Icon size={14} style={{ color: "var(--muted-foreground)" }} />
          <span className="eval-metric-name">{config.label}</span>
        </div>
        <div className="eval-metric-right">
          {score != null ? (
            <>
              <span className="eval-metric-score" style={{ color }}>
                {(score * 100).toFixed(0)}%
              </span>
              {passed ? (
                <CheckCircle2 size={14} style={{ color: "var(--success)" }} />
              ) : (
                <XCircle size={14} style={{ color: "var(--destructive)" }} />
              )}
            </>
          ) : (
            <span className="eval-metric-score" style={{ color }}>
              N/A
            </span>
          )}
          {reason &&
            (expanded ? (
              <ChevronUp size={12} style={{ color: "var(--muted-foreground)" }} />
            ) : (
              <ChevronDown size={12} style={{ color: "var(--muted-foreground)" }} />
            ))}
        </div>
      </button>
      {expanded && reason && (
        <div className="eval-metric-reason">
          <p>{reason}</p>
          {config.key === "cypher_quality" && evalData.cypher_source && (
            <div className="eval-cypher-source">
              <span className="eval-cypher-label">Source:</span>
              <code>{evalData.cypher_source}</code>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Inline eval badge — shows a small quality indicator next to metadata badges.
 * Click to toggle the full eval detail panel.
 */
export function EvalBadge({ evalData, isLoading, onClick }) {
  if (isLoading) {
    return (
      <button className="eval-badge eval-badge--loading" onClick={onClick} title="Evaluating...">
        <Loader2 size={12} className="spin" />
        <span>Evaluating</span>
      </button>
    );
  }

  if (!evalData || evalData.status === "failed") return null;

  const scores = METRIC_CONFIG.map((m) => evalData[m.key]).filter(
    (s) => s != null
  );
  if (scores.length === 0) return null;

  const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
  const color = scoreToColor(avg);
  const label = scoreToLabel(avg);

  return (
    <button
      className="eval-badge"
      onClick={onClick}
      title={`Quality: ${Math.round(avg * 100)}% — Click for details`}
      style={{ "--badge-color": color }}
    >
      <Activity size={12} />
      <span>{Math.round(avg * 100)}% {label}</span>
    </button>
  );
}

/**
 * Full eval detail panel — shown inline below the message when expanded.
 */
export default function EvalPanel({ evalData, isLoading }) {
  if (isLoading) {
    return (
      <div className="eval-panel eval-panel--loading">
        <div className="eval-panel-loading">
          <Loader2 size={16} className="spin" />
          <span>Running evaluation...</span>
        </div>
      </div>
    );
  }

  if (!evalData) return null;

  if (evalData.status === "failed") {
    return (
      <div className="eval-panel eval-panel--error">
        <span>Evaluation failed</span>
      </div>
    );
  }

  return (
    <div className="eval-panel">
      <div className="eval-panel-header">
        <Activity size={14} />
        <span>Evaluation Metrics</span>
      </div>
      <div className="eval-panel-body">
        <OverallScore evalData={evalData} />
        <div className="eval-metrics-list">
          {METRIC_CONFIG.map((config) => (
            <MetricCard key={config.key} config={config} evalData={evalData} />
          ))}
        </div>
      </div>
    </div>
  );
}
