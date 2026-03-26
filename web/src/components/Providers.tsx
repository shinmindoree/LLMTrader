"use client";

import { SessionProvider } from "next-auth/react";
import { ThemeProvider } from "next-themes";
import { SWRConfig } from "swr";
import { I18nProvider } from "@/lib/i18n";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
      <SWRConfig
        value={{
          revalidateOnFocus: false,
          dedupingInterval: 4000,
          errorRetryCount: 3,
          onErrorRetry: (error, _key, _config, revalidate, { retryCount }) => {
            // Don't retry on auth errors
            if (error?.status === 401 || error?.status === 403) return;
            // 429: respect Retry-After or exponential backoff
            if (error?.status === 429 || (typeof error?.message === "string" && error.message.startsWith("429"))) {
              const delay = Math.min(5000 * Math.pow(2, retryCount), 60_000);
              setTimeout(() => revalidate({ retryCount }), delay);
              return;
            }
            // Other errors: standard backoff, max 5 retries
            if (retryCount >= 5) return;
            const delay = Math.min(1000 * Math.pow(2, retryCount), 30_000);
            setTimeout(() => revalidate({ retryCount }), delay);
          },
        }}
      >
        <I18nProvider>{children}</I18nProvider>
      </SWRConfig>
      </ThemeProvider>
    </SessionProvider>
  );
}
