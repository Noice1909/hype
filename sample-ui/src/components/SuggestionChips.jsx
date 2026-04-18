import { ArrowRight, Layers, GitBranch } from "lucide-react";

/**
 * Maps agent IDs to short human-readable labels for the agent badge.
 * Missing agent → no badge (keeps chip uncluttered).
 */
const AGENT_LABELS = {
  neo4j: "Neo4j",
  "dda-agent": "DDA",
  dda: "DDA",
  mongodb: "Mongo",
  oracle: "Oracle",
  dataplex: "Dataplex",
  github: "GitHub",
  confluence: "Confluence",
  jira: "Jira",
  autosys: "Autosys",
  diva: "DIVA",
};

export default function SuggestionChips({ suggestions, onSelect }) {
  if (!suggestions || suggestions.length === 0) return null;

  return (
    <div className="suggestion-chips">
      <span className="suggestion-label">Follow-up questions</span>
      <div className="suggestion-list">
        {suggestions.map((s, i) => {
          // Backend shape: { text, type: "depth"|"breadth", agent: "neo4j" }
          // Legacy shape:  { question, type, from_agent }
          const text = s.text ?? s.question ?? "";
          if (!text) return null;

          const type = s.type || "depth";
          const agent = s.agent ?? s.suggested_agent ?? s.from_agent ?? null;
          const agentLabel = agent ? AGENT_LABELS[agent] ?? agent : null;

          return (
            <button
              key={s.id ?? i}
              className={`suggestion-chip suggestion-chip--${type}`}
              onClick={() => onSelect(text)}
              title={agent ? `Will route to ${agent}` : undefined}
            >
              <span className="suggestion-chip-icon">
                {type === "breadth" ? (
                  <GitBranch size={12} />
                ) : (
                  <Layers size={12} />
                )}
              </span>
              <span className="suggestion-chip-text">{text}</span>
              {agentLabel && (
                <span className={`suggestion-chip-badge agent-${agent}`}>
                  {agentLabel}
                </span>
              )}
              <ArrowRight size={12} className="suggestion-chip-arrow" />
            </button>
          );
        })}
      </div>
    </div>
  );
}
