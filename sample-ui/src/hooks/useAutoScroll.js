import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Smart auto-scroll hook for chat interfaces.
 *
 * Auto-scrolls to bottom during streaming IF the user is at the bottom.
 * Pauses when the user manually scrolls up.
 * Shows a "scroll to bottom" affordance when paused.
 */
export function useAutoScroll(deps = []) {
  const scrollRef = useRef(null);
  const sentinelRef = useRef(null);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const userScrolledUp = useRef(false);

  // Detect if user is near the bottom
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const threshold = 100;
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    setIsAtBottom(atBottom);
    if (!atBottom) {
      userScrolledUp.current = true;
    } else {
      userScrolledUp.current = false;
    }
  }, []);

  // Auto-scroll when deps change (new messages/tokens)
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || userScrolledUp.current) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, deps);

  // Attach scroll listener
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, [handleScroll]);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    userScrolledUp.current = false;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    setIsAtBottom(true);
  }, []);

  return { scrollRef, sentinelRef, isAtBottom, scrollToBottom };
}
