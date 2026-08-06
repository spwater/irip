/**
 * useRunSSE — SSE 连接管理 Hook + 轮询 fallback
 *
 * 功能：
 * - 使用 EventSource API 连接 SSE 端点
 * - 自动重连（最多 3 次，指数退避）
 * - 失败后降级为轮询（5 秒间隔调用 apiGetRunStatus）
 * - 组件卸载时关闭连接
 *
 * 使用方式：
 * const { connected, fallbackToPolling } = useRunSSE({
 *   workspaceId,
 *   runId,
 *   onEvent: (eventType, data) => { ... }
 * });
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import { getRunSSEUrl, apiGetRunStatus } from '../../api/research';

export type SSEEvent = {
  event: string;
  data: string;
};

export type UseRunSSEOptions = {
  workspaceId: string;
  runId: string;
  onEvent: (eventType: string, data: string) => void;
  enabled?: boolean;
};

export type UseRunSSEResult = {
  connected: boolean;
  fallbackToPolling: boolean;
  reconnect: () => void;
};

const MAX_RETRIES = 3;
const POLLING_INTERVAL = 5000; // 5 秒

export function useRunSSE(options: UseRunSSEOptions): UseRunSSEResult {
  const { workspaceId, runId, onEvent, enabled = true } = options;
  const [connected, setConnected] = useState(false);
  const [fallbackToPolling, setFallbackToPolling] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const retryCountRef = useRef(0);
  const pollingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onEventRef = useRef(onEvent);

  // 保持 onEvent 引用最新
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  const cleanupSSE = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  const cleanupPolling = useCallback(() => {
    if (pollingTimerRef.current) {
      clearInterval(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    setFallbackToPolling(true);
    setConnected(false);
    cleanupSSE();

    // 立即拉取一次
    apiGetRunStatus(workspaceId, runId)
      .then((run) => {
        onEventRef.current('run.status_changed', JSON.stringify(run));
      })
      .catch(() => {});

    // 设置定时轮询
    pollingTimerRef.current = setInterval(() => {
      apiGetRunStatus(workspaceId, runId)
        .then((run) => {
          onEventRef.current('run.status_changed', JSON.stringify(run));
        })
        .catch(() => {});
    }, POLLING_INTERVAL);
  }, [workspaceId, runId, cleanupSSE]);

  const connectSSE = useCallback(() => {
    if (!enabled) return;

    const url = getRunSSEUrl(workspaceId, runId);
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onopen = () => {
      setConnected(true);
      setFallbackToPolling(false);
      retryCountRef.current = 0;
    };

    es.onmessage = (event: MessageEvent) => {
      onEventRef.current('message', event.data);
    };

    es.addEventListener('run.status_changed', (event: MessageEvent) => {
      onEventRef.current('run.status_changed', event.data);
    });

    es.addEventListener('step.status_changed', (event: MessageEvent) => {
      onEventRef.current('step.status_changed', event.data);
    });

    es.addEventListener('step.progress', (event: MessageEvent) => {
      onEventRef.current('step.progress', event.data);
    });

    es.addEventListener('coverage.updated', (event: MessageEvent) => {
      onEventRef.current('coverage.updated', event.data);
    });

    es.addEventListener('artifact.created', (event: MessageEvent) => {
      onEventRef.current('artifact.created', event.data);
    });

    es.addEventListener('queue.position_changed', (event: MessageEvent) => {
      onEventRef.current('queue.position_changed', event.data);
    });

    es.onerror = () => {
      setConnected(false);
      es.close();

      retryCountRef.current += 1;
      if (retryCountRef.current >= MAX_RETRIES) {
        // 超过重试次数，降级为轮询
        startPolling();
      } else {
        // 指数退避重连
        const delay = Math.pow(2, retryCountRef.current) * 1000;
        setTimeout(() => {
          connectSSE();
        }, delay);
      }
    };
  }, [workspaceId, runId, enabled, startPolling]);

  const reconnect = useCallback(() => {
    retryCountRef.current = 0;
    cleanupPolling();
    setFallbackToPolling(false);
    connectSSE();
  }, [connectSSE, cleanupPolling]);

  useEffect(() => {
    if (!enabled) {
      cleanupSSE();
      cleanupPolling();
      return;
    }

    connectSSE();

    return () => {
      cleanupSSE();
      cleanupPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, runId, enabled]);

  return {
    connected,
    fallbackToPolling,
    reconnect,
  };
}
