"use client";

import { SessionProvider } from "next-auth/react";
import { SWRConfig } from "swr";
import { I18nProvider } from "@/lib/i18n";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <SWRConfig
        value={{
          revalidateOnFocus: true,
          dedupingInterval: 4000,
          errorRetryCount: 2,
        }}
      >
        <I18nProvider>{children}</I18nProvider>
      </SWRConfig>
    </SessionProvider>
  );
}
