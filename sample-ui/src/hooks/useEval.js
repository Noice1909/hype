import { useCallback, useEffect, useRef, useState } from "react";

const POLL_INTERVAL = 3000;
const MAX_POLLS = 20;

/**
 * Hook to poll DeepEval evaluation results for a given request_id.
 *
 * Returns { evalData, isLoading, error } where evalData is null until
 * the evaluation completes (or fails).
 */
export function useEval(requestId) {
  const [evalData, setEvalData] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const pollCountRef = useRef(0);
  const timerRef = useRef(null);

  useEffect(() => {
    if (!requestId) return;

    let cancelled = false;
    pollCountRef.current = 0;
    setIsLoading(true);
    setError(null);
    setEvalData(null);

    const poll = async () => {
      if (cancelled) return;
      pollCountRef.current += 1;

      try {
        const res = await fetch(`/api/v1/eval/${requestId}`);
        if (!res.ok) {
          if (res.status === 404) {
            // Not ready yet — keep polling
            if (pollCountRef.current < MAX_POLLS) {
              timerRef.current = setTimeout(poll, POLL_INTERVAL);
            } else {
              setIsLoading(false);
            }
            return;
          }
          throw new Error(`Eval fetch failed: ${res.status}`);
        }

        const data = await res.json();

        if (data.status === "pending") {
          if (pollCountRef.current < MAX_POLLS) {
            timerRef.current = setTimeout(poll, POLL_INTERVAL);
          } else {
            setIsLoading(false);
          }
          return;
        }

        if (!cancelled) {
          setEvalData(data);
          setIsLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
          setIsLoading(false);
        }
      }
    };

    // Start first poll after a short delay to give backend time to start evaluation
    timerRef.current = setTimeout(poll, 2000);

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [requestId]);

  return { evalData, isLoading, error };
}

/**
 * Hook to manage eval state for multiple messages.
 * Maps requestId → evalData for all completed assistant messages.
 */
export function useEvalMap() {
  const [evalMap, setEvalMap] = useState({});
  const [loadingSet, setLoadingSet] = useState(new Set());
  const activePolls = useRef(new Set());

  const startEval = useCallback((requestId) => {
    if (!requestId || activePolls.current.has(requestId)) return;
    activePolls.current.add(requestId);
    setLoadingSet((prev) => new Set([...prev, requestId]));

    let pollCount = 0;
    let cancelled = false;

    const poll = async () => {
      if (cancelled) return;
      pollCount += 1;

      try {
        const res = await fetch(`/api/v1/eval/${requestId}`);
        if (!res.ok) {
          if (res.status === 404 && pollCount < MAX_POLLS) {
            setTimeout(poll, POLL_INTERVAL);
            return;
          }
          if (res.status !== 404) {
            throw new Error(`${res.status}`);
          }
          // Max polls reached on 404
          setLoadingSet((prev) => {
            const next = new Set(prev);
            next.delete(requestId);
            return next;
          });
          return;
        }

        const data = await res.json();
        if (data.status === "pending" && pollCount < MAX_POLLS) {
          setTimeout(poll, POLL_INTERVAL);
          return;
        }

        setEvalMap((prev) => ({ ...prev, [requestId]: data }));
        setLoadingSet((prev) => {
          const next = new Set(prev);
          next.delete(requestId);
          return next;
        });
      } catch {
        setLoadingSet((prev) => {
          const next = new Set(prev);
          next.delete(requestId);
          return next;
        });
      }
    };

    setTimeout(poll, 2000);
  }, []);

  return { evalMap, loadingSet, startEval };
}
