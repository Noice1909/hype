import { ArrowRight, Layers, GitBranch } from "lucide-react";

export default function SuggestionChips({ suggestions, onSelect }) {
  if (!suggestions || suggestions.length === 0) return null;

  return (
    <div className="suggestion-chips">
      <span className="suggestion-label">Follow-up questions</span>
      <div className="suggestion-list">
        {suggestions.map((s, i) => (
          <button
            key={i}
            className={`suggestion-chip suggestion-chip--${s.type || "depth"}`}
            onClick={() => onSelect(s.question)}
            title={s.from_agent ? `From ${s.from_agent}` : undefined}
          >
            <span className="suggestion-chip-icon">
              {s.type === "breadth" ? (
                <GitBranch size={12} />
              ) : (
                <Layers size={12} />
              )}
            </span>
            <span className="suggestion-chip-text">{s.question}</span>
            <ArrowRight size={12} className="suggestion-chip-arrow" />
          </button>
        ))}
      </div>
    </div>
  );
}
