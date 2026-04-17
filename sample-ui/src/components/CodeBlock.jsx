import { useCallback, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import {
  vscDarkPlus,
  vs,
} from "react-syntax-highlighter/dist/esm/styles/prism";
import { Check, Copy } from "lucide-react";

export default function CodeBlock({ children, language, inline }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(children).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [children]);

  if (inline) {
    return <code className="inline-code">{children}</code>;
  }

  // Detect theme from data attribute
  const isDark =
    typeof document !== "undefined" &&
    document.documentElement.getAttribute("data-theme") !== "light";

  const displayLang = language || "text";

  return (
    <div className="code-block">
      <div className="code-header">
        <span className="code-language">{displayLang}</span>
        <button
          className="code-copy-btn"
          onClick={handleCopy}
          aria-label={copied ? "Copied" : "Copy code"}
          title={copied ? "Copied!" : "Copy code"}
        >
          {copied ? (
            <>
              <Check size={14} />
              <span>Copied!</span>
            </>
          ) : (
            <>
              <Copy size={14} />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <SyntaxHighlighter
        language={language || "text"}
        style={isDark ? vscDarkPlus : vs}
        showLineNumbers={children.split("\n").length > 5}
        wrapLines
        customStyle={{
          margin: 0,
          borderRadius: "0 0 8px 8px",
          fontSize: "0.875rem",
          maxHeight: "400px",
          overflow: "auto",
        }}
      >
        {children}
      </SyntaxHighlighter>
    </div>
  );
}
