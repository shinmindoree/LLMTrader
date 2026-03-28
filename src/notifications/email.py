"""Email sending utility using Azure Communication Services Email."""

from __future__ import annotations

import asyncio

from settings import get_settings


def _build_verification_html(verify_url: str, user_name: str) -> str:
    """Build a professional HTML email for email verification."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0e1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0e1117;padding:40px 0">
<tr><td align="center">
  <table role="presentation" width="520" cellpadding="0" cellspacing="0" style="background:#1e222d;border:1px solid #2a2e39;border-radius:12px;overflow:hidden">

    <!-- Header with gradient -->
    <tr><td style="background:linear-gradient(135deg,#2563eb,#7c3aed,#06b6d4);padding:32px 40px;text-align:center">
      <!-- Logo mark -->
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto">
      <tr><td style="width:48px;height:48px;background:rgba(255,255,255,0.2);border-radius:12px;text-align:center;vertical-align:middle;font-size:24px;color:#fff">
        &#x224B;
      </td>
      <td style="padding-left:12px;font-size:22px;font-weight:700;color:#ffffff;letter-spacing:-0.5px">
        AlphaWeaver
      </td></tr></table>
      <p style="margin:12px 0 0;font-size:13px;color:rgba(255,255,255,0.8)">AI-Powered Trading Strategy Platform</p>
    </td></tr>

    <!-- Body -->
    <tr><td style="padding:36px 40px">
      <h1 style="margin:0 0 8px;font-size:20px;font-weight:600;color:#d1d4dc">
        Verify your email address
      </h1>
      <p style="margin:0 0 24px;font-size:14px;color:#868993;line-height:1.6">
        Hi <strong style="color:#d1d4dc">{user_name}</strong>,<br>
        Thanks for signing up for AlphaWeaver! Please verify your email address to activate your account and start building trading strategies.
      </p>

      <!-- CTA Button -->
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto 28px">
      <tr><td align="center" style="background:#2962ff;border-radius:8px">
        <a href="{verify_url}" target="_blank" style="display:inline-block;padding:14px 36px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;letter-spacing:0.3px">
          Verify Email Address
        </a>
      </td></tr></table>

      <p style="margin:0 0 20px;font-size:13px;color:#868993;line-height:1.6">
        Or copy and paste this link into your browser:
      </p>
      <p style="margin:0 0 28px;padding:12px 16px;background:#131722;border:1px solid #2a2e39;border-radius:8px;font-size:12px;color:#7eb8ff;word-break:break-all;line-height:1.5">
        {verify_url}
      </p>

      <!-- Divider -->
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr><td style="border-top:1px solid #2a2e39;padding-top:20px">
        <p style="margin:0;font-size:12px;color:#5d6068;line-height:1.5">
          This verification link will expire in <strong style="color:#868993">24 hours</strong>.<br>
          If you didn&rsquo;t create an account, you can safely ignore this email.
        </p>
      </td></tr></table>
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:20px 40px 28px;border-top:1px solid #2a2e39;text-align:center">
      <p style="margin:0 0 4px;font-size:12px;color:#5d6068">
        &copy; AlphaWeaver &middot; AI-Powered Trading Strategy Platform
      </p>
      <p style="margin:0;font-size:11px;color:#3d4048">
        You received this email because someone signed up with this address.
      </p>
    </td></tr>

  </table>
</td></tr></table>
</body>
</html>"""


def _build_verification_text(verify_url: str, user_name: str) -> str:
    return (
        f"Hi {user_name},\n\n"
        "Thanks for signing up for AlphaWeaver!\n"
        "Please verify your email address by visiting the link below:\n\n"
        f"{verify_url}\n\n"
        "This link will expire in 24 hours.\n"
        "If you didn't create an account, you can safely ignore this email.\n\n"
        "— AlphaWeaver Team"
    )


async def send_verification_email(to_email: str, user_name: str, verify_url: str) -> bool:
    """Send verification email via Azure Communication Services. Returns True on success."""
    settings = get_settings()
    acs_cfg = settings.acs_email

    if not acs_cfg.is_configured:
        print(f"[email] ACS Email not configured — skipping verification email to {to_email}")
        print(f"[email] Verification URL: {verify_url}")
        return False

    def _send() -> bool:
        try:
            from azure.communication.email import EmailClient

            client = EmailClient.from_connection_string(acs_cfg.connection_string)

            message = {
                "senderAddress": acs_cfg.sender_address,
                "content": {
                    "subject": "Verify your email — AlphaWeaver",
                    "plainText": _build_verification_text(verify_url, user_name),
                    "html": _build_verification_html(verify_url, user_name),
                },
                "recipients": {
                    "to": [{"address": to_email}],
                },
                "headers": {
                    "X-Priority": "1",
                },
            }

            poller = client.begin_send(message)
            result = poller.result()
            print(f"[email] ACS email sent to {to_email}, messageId={result['id']}, status={result['status']}")
            return result["status"] == "Succeeded"
        except Exception as exc:
            print(f"[email] Failed to send verification email to {to_email}: {exc}")
            return False

    return await asyncio.to_thread(_send)
