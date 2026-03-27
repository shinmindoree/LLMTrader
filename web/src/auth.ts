import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import Credentials from "next-auth/providers/credentials";
import type { NextAuthConfig } from "next-auth";

import { isAdminEmail } from "@/lib/admin";

export const authConfig: NextAuthConfig = {
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
    }),
    Credentials({
      name: "Email",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const email = (credentials?.email as string ?? "").trim().toLowerCase();
        const password = credentials?.password as string ?? "";

        if (!email || !password) return null;

        // 1. Check admin credentials from env
        const adminEmail = (process.env.ADMIN_EMAIL ?? "").trim().toLowerCase();
        const adminPassword = (process.env.ADMIN_PASSWORD ?? "").trim();

        if (adminEmail && adminPassword && email === adminEmail && password === adminPassword) {
          return { id: `cred-${email}`, email, name: email.split("@")[0] };
        }

        // 2. Verify against DB via backend API
        const apiOrigin = process.env.API_ORIGIN ?? "http://localhost:8000";
        try {
          const res = await fetch(`${apiOrigin}/api/auth/verify-credentials`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password }),
          });
          if (res.ok) {
            const data = await res.json();
            return {
              id: data.user_id,
              email: data.email,
              name: data.display_name || email.split("@")[0],
            };
          }
        } catch {
          // Backend unreachable — fall through to reject
        }

        return null;
      },
    }),
  ],
  pages: {
    signIn: "/auth",
  },
  session: {
    strategy: "jwt",
    maxAge: 30 * 24 * 60 * 60, // 30 days
  },
  callbacks: {
    jwt({ token, user, account }) {
      if (user) {
        token.userId = user.id;
        token.email = user.email;
        token.provider = account?.provider ?? "credentials";
      }
      return token;
    },
    session({ session, token }) {
      if (session.user) {
        session.user.id = token.userId as string;
        session.user.email = token.email as string;
        (session as unknown as Record<string, unknown>).isAdmin = isAdminEmail(token.email as string);
        (session as unknown as Record<string, unknown>).provider = token.provider as string;
      }
      return session;
    },
    authorized({ auth, request: { nextUrl } }) {
      const isLoggedIn = !!auth?.user;
      const isPublic =
        nextUrl.pathname === "/" ||
        nextUrl.pathname === "/auth" ||
        nextUrl.pathname.startsWith("/api/") ||
        nextUrl.pathname.startsWith("/_next") ||
        nextUrl.pathname === "/favicon.ico" ||
        /\.[^/]+$/.test(nextUrl.pathname);

      if (isPublic) return true;
      if (isLoggedIn) return true;
      return false; // redirects to signIn page
    },
  },
  trustHost: true,
};

export const { handlers, auth, signIn, signOut } = NextAuth(authConfig);
