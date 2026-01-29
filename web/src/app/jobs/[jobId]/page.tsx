"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getJob } from "@/lib/api";
import { jobDetailPath } from "@/lib/routes";

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

export default function JobRedirectPage() {
  const params = useParams<{ jobId?: string | string[] }>();
  const router = useRouter();
  const raw = params?.jobId;
  const jobId = Array.isArray(raw) ? raw[0] : raw;
  const validJobId = typeof jobId === "string" && isUuid(jobId);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!validJobId || !jobId) return;
    getJob(jobId)
      .then((job) => router.replace(jobDetailPath(job.type, job.job_id)))
      .catch((e) => setError(String(e)));
  }, [jobId, validJobId, router]);

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      {!validJobId ? (
        <div className="rounded border border-[#2a2e39] bg-[#1e222d] p-4 text-sm text-[#d1d4dc]">
          Invalid job id: <span className="font-mono text-[#868993]">{String(jobId)}</span>
        </div>
      ) : null}
      {error ? (
        <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {error}
        </p>
      ) : null}
      {!error && validJobId ? (
        <div className="mt-4 text-sm text-[#868993]">Redirecting to the job detail page…</div>
      ) : null}
    </main>
  );
}
