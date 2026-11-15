# DAYLI signup and resend email

Supabase uses **Confirm signup** for both password registration and `resend({type: 'signup', email})`. There is no separate resend email template. [dayli-signup-otp.html](dayli-signup-otp.html) is deliberately suitable for both messages and displays `{{ .Token }}` without a confirmation link.

In the explicitly connected project, open **Authentication → Email Templates → Confirm signup**, set the subject to **Your DAYLI verification code**, and replace the body with the complete HTML file. Keep **Confirm email** enabled. Ensure **Email OTP length** is 6 and choose an **Email OTP Expiration**; the template makes no promise about a fixed expiry interval.

The connected Supabase MCP tools do not expose an Auth configuration/email-template update operation. The template is prepared locally for manual dashboard application; it has not been installed remotely. Do not use SQL to edit Supabase-managed Auth configuration.

Manual flow: register → optionally resend-otp → read email → verify-otp → use the returned access token. Provider rate limits and configured SMTP delivery settings still apply. Accepted resend responses do not confirm account existence or email delivery.
