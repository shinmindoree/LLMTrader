import { useI18n } from "@/lib/i18n";
import type { JobStatus } from "@/lib/types";

const STATUS_STYLES: Record<JobStatus, string> = {
  PENDING: "bg-[#2a2e39] text-[#d1d4dc]",
  RUNNING: "bg-[#26a69a] text-white",
  STOP_REQUESTED: "bg-[#f9a825] text-[#1e222d]",
  SUCCEEDED: "bg-[#2962ff] text-white",
  STOPPED: "bg-[#868993] text-white",
  FAILED: "bg-[#ef5350] text-white",
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const { t } = useI18n();
  const labels: Record<JobStatus, string> = {
    PENDING: t.status.queued,
    RUNNING: t.status.running,
    STOP_REQUESTED: t.status.stopping,
    SUCCEEDED: t.status.completed,
    STOPPED: t.status.stopped,
    FAILED: t.status.failed,
  };
  return (
    <span className={`rounded px-2 py-1 text-xs font-medium ${STATUS_STYLES[status]}`}>
      {labels[status]}
    </span>
  );
}
