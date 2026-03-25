import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/api/", "/dashboard/", "/backtest/", "/live/", "/strategies/", "/settings/", "/billing/", "/admin/", "/jobs/"],
    },
    sitemap: `${process.env.NEXT_PUBLIC_SITE_URL ?? "https://alphaweaver.com"}/sitemap.xml`,
  };
}
