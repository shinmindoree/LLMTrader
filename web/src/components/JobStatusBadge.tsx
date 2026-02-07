import type { JobStatus } from "@/lib/types";

const STATUS_STYLES: Record<JobStatus, string> = {
  PENDING: "bg-[#2a2e39] text-[#d1d4dc]",
  RUNNING: "bg-[#26a69a] text-white",
  STOP_REQUESTED: "bg-[#f9a825] text-[#1e222d]",
  SUCCEEDED: "bg-[#2962ff] text-white",
  STOPPED: "bg-[#868993] text-white",
  FAILED: "bg-[#ef5350] text-white",
};

const STATUS_LABELS: Record<JobStatus, string> = {
  PENDING: "Queued",
  RUNNING: "Running",
  STOP_REQUESTED: "Stopping",
  SUCCEEDED: "Completed",
  STOPPED: "Stopped",
  FAILED: "Failed",
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className={`rounded px-2 py-1 text-xs font-medium ${STATUS_STYLES[status]}`}>
      {STATUS_LABELS[status]}
    </span>
  );
}
