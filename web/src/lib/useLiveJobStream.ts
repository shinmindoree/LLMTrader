"use client";

import { useEffect, useRef, useState } from "react";
import type { Trade } from "@/lib/types";

export type LiveStreamJob = {
  job_id: string;
  status: string;
  strategy_path: string;
  config: Record<string, unknown>;
  started_at: string | null;
  trades: Trade[];
};

type LiveStreamPayload = {
  jobs: LiveStreamJob[];
};

/**
 * SSE hook that subscribes to `/api/backend/api/jobs/live/stream`.
 * Returns the latest snapshot of running live jobs with their trades.
 * The server pushes updates every ~5 seconds, eliminating the need
 * for per-job polling.
 */
export function useLiveJobStream(enabled: boolean): {
  jobs: LiveStreamJob[];
  tradesMap: Map<string, Trade[]>;
  connected: boolean;
} {
  const [jobs, setJobs] = useState<LiveStreamJob[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!enabled) {
      setJobs([]);
      setConnected(false);
      return;
    }

    let retryDelay = 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;

    function connect() {
      if (disposed) return;

      const es = new EventSource("/api/backend/api/jobs/live/stream");
      esRef.current = es;

      es.onopen = () => {
        setConnected(true);
        retryDelay = 1000; // reset on success
      };

      es.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as LiveStreamPayload;
          setJobs(payload.jobs);
        } catch {
          // ignore malformed messages
        }
      };

      es.onerror = () => {
        setConnected(false);
        es.close();
        esRef.current = null;
        if (!disposed) {
          // Exponential backoff: 1s → 2s → 4s → … → 30s max
          timer = setTimeout(() => connect(), retryDelay);
          retryDelay = Math.min(retryDelay * 2, 30_000);
        }
      };
    }

    connect();

    return () => {
      disposed = true;
      if (timer !== undefined) clearTimeout(timer);
      esRef.current?.close();
      esRef.current = null;
    };
  }, [enabled]);

  // Derived trades map for easy per-job lookup
  const tradesMap = new Map<string, Trade[]>();
  for (const job of jobs) {
    tradesMap.set(job.job_id, job.trades);
  }

  return { jobs, tradesMap, connected };
}
