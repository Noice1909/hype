import { ArrowDown } from "lucide-react";

export default function ScrollToBottom({ visible, onClick }) {
  if (!visible) return null;

  return (
    <button
      className="scroll-to-bottom"
      onClick={onClick}
      aria-label="Scroll to bottom"
    >
      <ArrowDown size={18} />
    </button>
  );
}
