import { useMemo } from "react";
import MarkdownRenderer from "./MarkdownRenderer";

/**
 * Patch incomplete markdown so it renders cleanly during streaming.
 * Closes unclosed fences, bold, italic, and inline code markers.
 */
function patchIncompleteMarkdown(text) {
  if (!text) return text;

  let patched = text;

  // Close unclosed code fences
  const fenceCount = (patched.match(/```/g) || []).length;
  if (fenceCount % 2 !== 0) {
    patched += "\n```";
  }

  // Close unclosed bold
  const boldCount = (patched.match(/\*\*/g) || []).length;
  if (boldCount % 2 !== 0) {
    patched += "**";
  }

  // Close unclosed italic (single *)
  // Careful not to count ** as two single *
  const stripped = patched.replace(/\*\*/g, "");
  const italicCount = (stripped.match(/\*/g) || []).length;
  if (italicCount % 2 !== 0) {
    patched += "*";
  }

  // Close unclosed inline code
  const codeCount = (patched.match(/(?<!`)`(?!`)/g) || []).length;
  if (codeCount % 2 !== 0) {
    patched += "`";
  }

  return patched;
}

export default function StreamingText({ text }) {
  const patched = useMemo(() => patchIncompleteMarkdown(text), [text]);

  if (!patched) return null;

  return (
    <div className="streaming-text">
      <MarkdownRenderer content={patched} />
      <span className="streaming-cursor" />
    </div>
  );
}
