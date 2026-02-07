"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { JobEvent } from "@/lib/types";

export function JobEventsConsole({ jobId }: { jobId: string }) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastIdRef = useRef<number>(0);
  const [lastId, setLastId] = useState<number>(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const url = useMemo(() => `/api/backend/api/jobs/${jobId}/events/stream?after_event_id=0`, [jobId]);

  useEffect(() => {
    const es = new EventSource(url);
    es.onopen = () => {
      setConnected(true);
      setError(null);
    };
    es.onerror = () => {
      setConnected(false);
      setError("SSE disconnected");
    };
    es.onmessage = (msg) => {
      try {
        const ev = JSON.parse(msg.data) as JobEvent;
        lastIdRef.current = ev.event_id;
        setLastId(ev.event_id);
        setEvents((prev) => {
          const newEvents = [...prev, ev].slice(-500);
          // Keep the latest event in view.
          setTimeout(() => {
            if (scrollRef.current) {
              scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
            }
          }, 0);
          return newEvents;
        });
      } catch {
        // ignore
      }
    };
    return () => es.close();
  }, [url]);

  const getEventColor = (event: JobEvent) => {
    if (event.kind === "STATUS") return "text-[#2962ff]";
    if (event.kind === "ERROR" || event.level === "ERROR") return "text-[#ef5350]";
    if (event.kind === "TRADE") {
      const side = (event.payload as { side?: string })?.side;
      return side === "BUY" ? "text-[#26a69a]" : "text-[#ef5350]";
    }
    return "text-[#868993]";
  };

  return (
    <div className="rounded border border-[#2a2e39] bg-[#1e222d]">
      <div className="flex items-center justify-between border-b border-[#2a2e39] bg-[#131722] px-4 py-2 text-xs">
        <span className="font-medium text-[#d1d4dc]">Events</span>
        <div className="flex items-center gap-2">
          <div
            className={`h-2 w-2 rounded-full ${
              connected ? "bg-[#26a69a]" : "bg-[#868993]"
            }`}
          />
          <span className="text-[#868993]">
            {connected ? "connected" : "disconnected"} • last event #{lastId}
          </span>
        </div>
      </div>
      {error ? (
        <div className="border-b border-[#2a2e39] bg-[#2d1f1f]/50 px-4 py-2 text-xs text-[#ef5350]">
          {error}
        </div>
      ) : null}
      <div
        ref={scrollRef}
        className="max-h-[420px] overflow-auto bg-[#0a0a0a] px-4 py-3 font-mono text-xs"
      >
        {events.length === 0 ? (
          <div className="text-center text-[#868993]">Waiting for events...</div>
        ) : (
          events.map((e) => (
            <div key={e.event_id} className={`whitespace-pre-wrap ${getEventColor(e)}`}>
              <span className="text-[#868993]">[{new Date(e.ts).toLocaleTimeString()}]</span>{" "}
              <span className="text-[#868993]">{e.kind}</span> {e.message}{" "}
              {e.payload ? (
                <span className="text-[#868993]">{JSON.stringify(e.payload)}</span>
              ) : (
                ""
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
