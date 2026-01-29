import type { JobType } from "@/lib/types";

export function jobDetailPath(jobType: JobType, jobId: string): string {
  return jobType === "BACKTEST" ? `/backtest/jobs/${jobId}` : `/live/jobs/${jobId}`;
}
