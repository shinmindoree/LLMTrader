"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);
  return (
    <html lang="en">
      <body style={{ background: "#131722", color: "#d1d4dc", fontFamily: "sans-serif" }}>
        <div
          style={{
            display: "flex",
            minHeight: "100vh",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "1.5rem",
            textAlign: "center",
          }}
        >
          <div
            style={{
              borderRadius: "0.5rem",
              border: "1px solid rgba(239, 83, 80, 0.3)",
              background: "rgba(45, 31, 31, 0.5)",
              padding: "2rem",
              maxWidth: "28rem",
            }}
          >
            <h2 style={{ fontSize: "1.25rem", fontWeight: 600, color: "#ef5350", marginBottom: "0.75rem" }}>
              Something went wrong
            </h2>
            <p style={{ fontSize: "0.875rem", color: "#868993", marginBottom: "1.5rem" }}>
              An unexpected error occurred. Please try again.
            </p>
            {error.digest && (
              <p style={{ fontSize: "0.75rem", color: "rgba(134, 137, 147, 0.6)", marginBottom: "1rem", fontFamily: "monospace" }}>
                Error ID: {error.digest}
              </p>
            )}
            <button
              onClick={reset}
              style={{
                borderRadius: "0.25rem",
                background: "#2962ff",
                padding: "0.5rem 1.25rem",
                fontSize: "0.875rem",
                fontWeight: 500,
                color: "white",
                border: "none",
                cursor: "pointer",
              }}
            >
              Try Again
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
