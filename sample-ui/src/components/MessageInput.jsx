import { useCallback, useRef, useState } from "react";
import { Send, Square, Settings } from "lucide-react";

export default function MessageInput({
  onSend,
  onCancel,
  isStreaming,
  disabled,
}) {
  const [input, setInput] = useState("");
  const [apiKey, setApiKey] = useState(
    () => localStorage.getItem("apiKey") || ""
  );
  const [showSettings, setShowSettings] = useState(false);
  const inputRef = useRef(null);

  const handleSubmit = useCallback(
    (e) => {
      e.preventDefault();
      if (!input.trim() || isStreaming) return;
      onSend(input.trim(), apiKey);
      setInput("");
    },
    [input, isStreaming, onSend, apiKey]
  );

  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit(e);
      }
    },
    [handleSubmit]
  );

  const handleApiKeyChange = useCallback((e) => {
    const val = e.target.value;
    setApiKey(val);
    localStorage.setItem("apiKey", val);
  }, []);

  return (
    <div className="input-area">
      {showSettings && (
        <div className="input-settings">
          <label className="input-settings-label">
            API Key
            <input
              type="password"
              value={apiKey}
              onChange={handleApiKeyChange}
              placeholder="Bearer token (optional)"
              className="input-settings-field"
            />
          </label>
        </div>
      )}
      <form className="input-form" onSubmit={handleSubmit}>
        <button
          type="button"
          className="input-settings-btn"
          onClick={() => setShowSettings(!showSettings)}
          aria-label="Settings"
          title="API settings"
        >
          <Settings size={18} />
        </button>
        <textarea
          ref={inputRef}
          className="input-textarea"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your Neo4j graph..."
          rows={1}
          disabled={disabled}
          aria-label="Message input"
        />
        {isStreaming ? (
          <button
            type="button"
            className="input-btn cancel-btn"
            onClick={onCancel}
            aria-label="Stop generating"
            title="Stop generating"
          >
            <Square size={18} />
          </button>
        ) : (
          <button
            type="submit"
            className="input-btn send-btn"
            disabled={!input.trim() || disabled}
            aria-label="Send message"
            title="Send message"
          >
            <Send size={18} />
          </button>
        )}
      </form>
    </div>
  );
}
