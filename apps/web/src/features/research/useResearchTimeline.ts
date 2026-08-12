/**
 * useResearchTimeline — hook for cursor-paginated timeline loading.
 *
 * First page: cursor=undefined. Load more: pass next_cursor.
 * Append mode: deduplicates by turn_id, preserves server order.
 * No client-side re-sorting.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  listTimeline,
  type TimelineItem,
  type TimelinePage,
} from "../../api/researchTimeline";

interface UseResearchTimelineResult {
  items: TimelineItem[];
  loading: boolean;
  error: Error | null;
  hasMore: boolean;
  activeRunStatus: string | null;
  loadMore: () => void;
  refresh: () => void;
}

/** Statuses that indicate the analysis is still in progress. */
const IN_PROGRESS_STATUSES = new Set([
  "planning",
  "plan_review",
  "plan_confirmed",
  "queued",
  "running",
]);

export function useResearchTimeline(
  workspaceId: string,
  pageSize?: number,
): UseResearchTimelineResult {
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [activeRunStatus, setActiveRunStatus] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadPage = useCallback(
    async (cur: string | null, append: boolean) => {
      // Only show loading spinner on initial load, not on poll refreshes
      if (cur === null && !append) {
        setLoading(true);
      }
      setError(null);
      try {
        const page: TimelinePage = await listTimeline(workspaceId, cur, pageSize);
        setItems((prev) => {
          const newItems = append ? [...prev, ...page.items] : page.items;
          // Deduplicate by turn_id, preserve order
          const seen = new Set<string>();
          return newItems.filter((item) => {
            if (seen.has(item.turn_id)) return false;
            seen.add(item.turn_id);
            return true;
          });
        });
        setCursor(page.next_cursor);
        setHasMore(page.next_cursor !== null);
        setActiveRunStatus(page.active_run_status);
      } catch (e) {
        setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        setLoading(false);
      }
    },
    [workspaceId, pageSize],
  );

  const refresh = useCallback(() => {
    setCursor(null);
    loadPage(null, false);
  }, [loadPage]);

  const loadMore = useCallback(() => {
    if (!loading && hasMore && cursor) {
      loadPage(cursor, true);
    }
  }, [loading, hasMore, cursor, loadPage]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auto-poll when there are turns in progress (planning/running/queued etc.)
  // Stop polling when all turns reach terminal states.
  useEffect(() => {
    const hasInProgress = items.some(
      (item) => IN_PROGRESS_STATUSES.has(item.status),
    );

    if (hasInProgress && pollRef.current === null) {
      // Start polling every 3 seconds
      pollRef.current = setInterval(() => {
        loadPage(null, false);
      }, 3000);
    } else if (!hasInProgress && pollRef.current !== null) {
      // All turns done — stop polling
      clearInterval(pollRef.current);
      pollRef.current = null;
    }

    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [items, loadPage]);

  return { items, loading, error, hasMore, activeRunStatus, loadMore, refresh };
}
