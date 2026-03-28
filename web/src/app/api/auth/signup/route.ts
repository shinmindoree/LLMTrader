import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";
const RECAPTCHA_SECRET_KEY = process.env.RECAPTCHA_SECRET_KEY ?? "";

async function verifyCaptcha(token: string): Promise<boolean> {
  if (!RECAPTCHA_SECRET_KEY) return true; // skip if not configured
  try {
    const res = await fetch("https://www.google.com/recaptcha/api/siteverify", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `secret=${encodeURIComponent(RECAPTCHA_SECRET_KEY)}&response=${encodeURIComponent(token)}`,
    });
    const data = await res.json();
    return data.success === true;
  } catch {
    return false;
  }
}

export async function POST(req: NextRequest): Promise<Response> {
  try {
    const body = await req.json();
    const { captchaToken, ...registrationData } = body;

    // Verify reCAPTCHA if configured
    if (RECAPTCHA_SECRET_KEY) {
      if (!captchaToken) {
        return NextResponse.json({ error: "CAPTCHA verification required" }, { status: 400 });
      }
      const valid = await verifyCaptcha(captchaToken);
      if (!valid) {
        return NextResponse.json({ error: "CAPTCHA verification failed" }, { status: 400 });
      }
    }

    const res = await fetch(`${API_ORIGIN}/api/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(registrationData),
    });
    const data = await res.json();
    if (!res.ok) {
      return NextResponse.json(
        { error: data.detail ?? "Registration failed" },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      { error: "Registration service unavailable" },
      { status: 502 },
    );
  }
}

// Legacy route — redirect to auth page
export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL("/auth", req.url);
  return NextResponse.redirect(url);
}
