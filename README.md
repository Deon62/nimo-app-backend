# Nimo API

FastAPI backend for the Nimo app.

| Concern | Service |
| --- | --- |
| Auth + database | Supabase (the app signs in with Supabase; the API verifies its JWT) |
| Conversation | DeepSeek (`deepseek-chat`, OpenAI-compatible) |
| Voices | ElevenLabs text-to-speech, speech-to-text (Scribe) |
| Card payments | Paystack |
| M-Pesa | Safaricom Daraja (STK push) |

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # fill in your keys
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs · Tests: `pytest`

### Deploy on Render
1. Render → **New → Blueprint** → choose this repo. `render.yaml` creates the `nimo-api` web service
   (Python 3.13, free plan, health check `/health`).
2. Paste the secrets it asks for (Supabase, DeepSeek, ElevenLabs, Paystack, Daraja).
   `DARAJA_CALLBACK_TOKEN` is generated for you.
3. Your API URL is `https://<service>.onrender.com`. M-Pesa callbacks use it automatically
   (`RENDER_EXTERNAL_URL`); set the Paystack webhook to `https://<service>.onrender.com/webhooks/paystack`.

Free services sleep after 15 minutes idle and take about a minute to wake. The first request after a
pause is slow, and an M-Pesa callback can arrive while it wakes. The app's status polling
(`/billing/mpesa/status/...`) confirms payments either way. Use a paid instance for production.

### Supabase
1. Run `supabase/migrations/0001_init.sql`, then `0002_payment_methods_and_storage.sql` and `0003_grants.sql`, in the SQL editor
   (tables, RLS, profile trigger, storage buckets for avatars and recordings).
2. Authentication → Providers: enable Email, Google, Apple and Phone (SMS).
3. Put the project URL and **service role** key in `.env`. The service key stays on the server only;
   the app uses the anon key.

### Callbacks (Paystack + M-Pesa)
On Render this is automatic. Locally, both need a public HTTPS URL: run a tunnel (`ngrok http 8000` or `cloudflared`)
and set `PUBLIC_BASE_URL` to it.

- Paystack dashboard → Settings → API Keys & Webhooks → Webhook URL: `<PUBLIC_BASE_URL>/webhooks/paystack`
- M-Pesa callback is sent automatically with each STK push:
  `<PUBLIC_BASE_URL>/webhooks/mpesa/<DARAJA_CALLBACK_TOKEN>`

## Endpoints

All except `/health`, `/agents`, `/billing/plans` and `/webhooks/*` need
`Authorization: Bearer <supabase access token>`.

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/agents` | Public agent list |
| GET | `/agents/{id}/preview` | MP3 of the agent's voice sample (cached) |
| POST | `/calls` | Start a call `{agent_id}` (checks the plan's daily minutes) |
| POST | `/calls/{id}/voice` | Multipart `audio` → transcript, reply text, reply audio (base64 MP3) |
| POST | `/calls/{id}/text` | `{text, speak}` → reply text (+ audio) |
| POST | `/calls/{id}/end` | `{duration_seconds}` |
| GET | `/calls` | Call history |
| GET | `/calls/{id}` | One call with its messages |
| PATCH | `/calls/{id}` | `{recorded}`: turn recording on/off (paid plans) |
| GET | `/calls/{id}/recording` | Signed links to the recorded segments, in order |
| DELETE | `/calls/{id}` | Delete a call and its recording |
| GET | `/agents/{id}/sample/{line}` | MP3 of one line of the agent's sample call |
| DELETE | `/me` | Delete the account, recordings and photo |
| GET | `/billing/plans` | Plans with USD and KES prices |
| GET | `/billing/me` | Current plan |
| GET | `/billing/usage` | Minutes per day (last 7 days) and today's allowance |
| GET | `/billing/payment-methods` | Saved cards (from Paystack payments) and M-Pesa numbers |
| POST | `/billing/payment-methods/mpesa` | `{phone}` save an M-Pesa number |
| POST | `/billing/payment-methods/{id}/default` | Make default |
| DELETE | `/billing/payment-methods/{id}` | Remove |
| POST | `/billing/paystack/charge-saved` | `{plan_id, period, method_id}` pay with a saved card |
| POST | `/billing/paystack/initialize` | `{plan_id, period}` → `authorization_url` to open in a browser |
| GET | `/billing/paystack/verify/{reference}` | Confirms the card payment and activates the plan |
| POST | `/billing/mpesa/stk-push` | `{plan_id, period, phone}` → PIN prompt on the phone |
| GET | `/billing/mpesa/status/{checkout_request_id}` | Poll until `paid` / `failed` |
| POST | `/webhooks/paystack` | Paystack events (HMAC-SHA512 verified) |
| POST | `/webhooks/mpesa/{token}` | Daraja STK callback |

## How a call works
1. App: `POST /calls` with the agent.
2. App records the caller's speech and sends it to `POST /calls/{id}/voice`.
3. API: ElevenLabs speech-to-text → DeepSeek (agent's system prompt + recent history) → ElevenLabs
   text-to-speech in the agent's voice → returns text + audio. The app plays it and shows captions.
4. App: `POST /calls/{id}/end` with the duration.

## Payments
- Prices live in `app/plans.py` (USD, charged in KES at `USD_TO_KES`). The app never sends an amount.
- A payment row is created as `pending`, then marked `paid` by whichever arrives first: the app's
  verify/status poll or the webhook. Activation is idempotent.
- The app never handles card numbers: Paystack's hosted page collects them.

## Agents and voices
`app/data/agents.json` is generated from the app's `src/data/agents.ts` (github.com/Deon62/nimo-app).
With this repo checked out as `backend/` inside the app folder, run from the app folder:

```bash
node --experimental-strip-types backend/scripts/export_agents.mjs
```

ElevenLabs voice IDs live in `src/data/agents.ts`. Ryan has no voice yet and uses
`ELEVENLABS_FALLBACK_VOICE_ID` until his custom voice is generated.

## Phone sign-in SMS (Celcom Africa)

Supabase still creates the code and signs the user in; it calls this API to deliver the SMS.

1. Supabase > Authentication > Sign In / Providers: enable **Phone**.
2. Supabase > Authentication > Hooks: add a **Send SMS** hook, type HTTPS,
   URL `https://nimo-app-backend.onrender.com/hooks/send-sms`. Generate a secret.
3. On Render set `SEND_SMS_HOOK_SECRET` (that secret, `v1,whsec_...`), `CELCOM_API_KEY`,
   `CELCOM_PARTNER_ID` and `CELCOM_SHORTCODE` (your sender ID).

The hook checks the Standard Webhooks signature, then sends "Your Nimo code is ..." via Celcom.
