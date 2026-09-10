# Multi-Model AI Fallback

## The short version (for a mentor or reviewer)

> The platform uses a multi-model AI strategy. **Grok is the primary model.** If Grok cannot
> process the meeting because of authentication, quota, timeout, model availability, or another
> controlled failure, the system automatically tries **Gemini**. If Gemini also fails, the system
> tries **Groq**. As soon as one provider successfully returns a valid structured response, the
> system stops and does not call the remaining providers. This improves reliability while reducing
> unnecessary API usage.

Two details worth adding, because they are what the implementation actually does:

* A provider with **no API key is skipped without being called at all** — there is no probe
  request, so an unconfigured provider costs nothing.
* A provider's answer only counts as success if it **validates against the Pydantic schema**.
  Unusable JSON is a provider failure, and the chain moves on rather than storing it.

---

## Why this exists

The free/limited xAI tier this project runs on can return `401 Unauthorized`, an insufficient
access error, or a `429` quota error without warning. Before this change, any one of those ended
the whole meeting-analysis workflow: the transcript was already produced and paid for in CPU time,
and the user got nothing.

The fallback layer turns "the AI provider is having a bad day" from a dead end into a detour:

| Goal | How the design meets it |
| --- | --- |
| Reliability | Three independent vendors, tried in order |
| Handle auth failures | `401`/`403` → next provider immediately, no retry |
| Handle quotas | `429` → next provider immediately, never a second call to the exhausted one |
| Avoid total failure | Only a failure of **every** configured provider is a failure |
| Minimise API usage | One request in the normal case; success stops the chain |
| Support free tiers | Unconfigured providers are skipped; retries are strictly bounded |

---

## The chain

```text
                    ┌───────────────┐
                    │   xAI Grok    │
                    │    PRIMARY    │
                    └───────┬───────┘
                            │
                     SUCCESS?
                       /       \
                     YES        NO
                     ↓           ↓
                  RETURN     ┌────────────────┐
                             │ Google Gemini  │
                             │   FALLBACK 1   │
                             └───────┬────────┘
                                     │
                              SUCCESS?
                                /       \
                              YES        NO
                              ↓           ↓
                           RETURN    ┌───────────────┐
                                     │     Groq      │
                                     │  FALLBACK 2   │
                                     └───────┬───────┘
                                             │
                                      SUCCESS?
                                        /       \
                                      YES        NO
                                      ↓           ↓
                                   RETURN   CONTROLLED ERROR
```

The order is fixed by `LLM_PROVIDER_ORDER` and defaults to `grok,gemini,groq`.

---

## Where it lives

```text
backend/app/ai/
├── llm_orchestrator.py          the fallback chain  ← the only file that knows about "next provider"
├── llm_service.py               analysis engine bound to ONE provider (prompt → parse → validate)
├── chunking.py                  unchanged
├── prompts/
│   └── meeting_intelligence_prompt.py   ONE prompt, shared by all three providers
└── providers/
    ├── base.py                  LLMProvider contract, error categories, bounded retry
    ├── openai_compatible.py     shared chat-completions implementation
    ├── grok_provider.py         xAI            (configuration only)
    ├── groq_provider.py         Groq           (configuration only)
    └── gemini_provider.py       Google Gemini  (the one real adapter)
```

Request flow, unchanged apart from the highlighted step:

```text
React  →  POST /api/meetings/{id}/analyze
       →  IntelligenceController
       →  IntelligenceService          (cache check, participant mapping, persistence)
       →  LLMOrchestrator              ←── provider fallback happens here, and only here
       →  LLMService(provider)         (prompt → parse → Pydantic validation → chunk/merge)
       →  GrokProvider | GeminiProvider | GroqProvider
       →  MeetingIntelligence  →  Supabase  →  React
```

Controllers contain no provider logic at all. Neither does `IntelligenceService`: it asks the
orchestrator for meeting intelligence and records which provider is named in the returned metadata.

---

## Provider abstraction

Every provider implements the same contract (`app/ai/providers/base.py`):

```python
class LLMProvider:
    name: str            # "grok" | "gemini" | "groq"
    label: str           # "Grok (xAI)"
    api_key_env: str     # "XAI_API_KEY"

    @property
    def model(self) -> str: ...
    @property
    def is_configured(self) -> bool: ...          # local check, no network call

    async def complete_json(self, *, system_prompt, user_prompt,
                            max_tokens=4000, temperature=None) -> str: ...
```

Grok and Groq both speak the OpenAI chat-completions protocol, so they share one implementation
and differ only in credentials, model and base URL. Gemini is the only genuine adapter: the same
system prompt becomes `systemInstruction`, the user prompt becomes `contents[0].parts[].text`,
JSON mode becomes `generationConfig.responseMimeType`, and the answer is read back out of
`candidates[0].content.parts[].text`.

**The prompt itself is not duplicated.** All three providers receive the identical system prompt
and user prompt from `prompts/meeting_intelligence_prompt.py`, including the same anti-hallucination
rules, and all three are validated against the same `LLMMeetingIntelligence` model. Nothing
downstream can tell which vendor answered.

---

## API-usage discipline

This is the part that matters on a free tier.

| Rule | Implementation |
| --- | --- |
| One provider at a time | The orchestrator is a plain `for` loop with `await`. Nothing is run concurrently across providers. |
| Success stops the chain | The loop `return`s the moment a result validates. |
| Never fan out | The transcript is never sent to more than one provider at a time, and never to all three "to compare". |
| Skip unconfigured providers | `is_configured` reads the environment. No probe request. |
| No health check before work | `/api/health/llm` is operator-triggered only, and by default live-checks just the primary provider. |
| Don't retry a dead provider | `401`, `403`, `404`, `429` are never retried. Only network errors, timeouts and 5xx are, at most `LLM_PROVIDER_MAX_ATTEMPTS` times. |
| Don't re-analyse | `IntelligenceService.analyze` returns the stored result unless `force=true`. Opening a meeting never calls a provider. |
| Stop a dying provider mid-transcript | If a long transcript is chunked and the provider fails fatally on one chunk, the remaining chunks are cancelled instead of spending one dead call each. |

### What one analysis actually costs

| Situation | Provider requests |
| --- | --- |
| Grok works (the normal case) | **1** |
| Grok down, Gemini works | 2 |
| Grok and Gemini down, Groq works | 3 |
| Everything down | 3, then a controlled error |
| Meeting already analysed, user re-opens it | **0** |
| Providers with no key configured | **0** each |

A long transcript is chunked first, so the count above is "per request to a provider" and
multiplies by the number of chunks — but the *provider fallback* still adds at most the chain
length, and a fatally-failing provider stops immediately instead of burning one call per chunk.

Two settings tighten this further if you want the absolute minimum:

```env
LLM_PROVIDER_MAX_ATTEMPTS=1   # no transport retry at all, even for a timeout
LLM_SCHEMA_RETRY_ATTEMPTS=1   # no corrective re-prompt for malformed JSON
```

---

## Error classification

Every provider failure is tagged with a category, and every category advances the chain. The
category decides the wording and whether a retry was allowed *within* that provider:

| Category | Triggered by | Retried? | Then |
| --- | --- | --- | --- |
| `not_configured` | no API key | — | provider skipped, never called |
| `auth` | `401`, `403`, Gemini's `400 API_KEY_INVALID` | **no** | next provider |
| `rate_limit` | `429`, `RESOURCE_EXHAUSTED` | **no** | next provider |
| `model_unavailable` | `404`, `NOT_FOUND` | **no** | next provider |
| `timeout` | request deadline exceeded | once | next provider |
| `network` | DNS/connection failure | once | next provider |
| `server_error` | `5xx` | once | next provider |
| `bad_request` | other `4xx` | no | next provider |
| `invalid_response` | unparseable JSON, or JSON that fails Pydantic validation | one corrective re-prompt | next provider |

Google reports a bad key as `400 API_KEY_INVALID` rather than `401`, so the Gemini provider reads
the error body before classifying — otherwise a missing key would surface as a vague "request
rejected" and nobody would know what to fix.

---

## Validation: malformed output is never stored

```text
Provider response
       ↓
extract_json_object()        tolerates markdown fences, prose, trailing commas, smart quotes
       ↓
LLMMeetingIntelligence       Pydantic: summary required, priorities/statuses normalised,
       ↓                     "N/A"/"TBD"/"unknown" collapsed to null
   Valid?
   /     \
 YES      NO
  ↓        ↓
Return   one corrective re-prompt, then provider failure → next provider
```

If Grok returns malformed output it is **not stored**; the chain moves to Gemini. If Gemini's
output is malformed it is not stored either; the chain moves to Groq. If Groq's is malformed too,
the request ends in a controlled error and the database is untouched.

---

## What the user sees when everything fails

`LLM_ALL_PROVIDERS_FAILED`, HTTP 502, with a message like:

```text
AI analysis could not be completed at this time. We attempted the configured AI
providers (grok (authentication rejected), gemini (rate limited or out of quota)),
but none returned a valid response. Not configured, so not attempted: groq.
Adding a key for one of those gives the analysis another chance.
```

If **no** provider is configured at all, the answer is `LLM_NOT_CONFIGURED` (HTTP 503) naming the
three variables to set — the application never pretends a provider exists that does not.

No API key, authorization header, stack trace or provider secret appears in any response. Error
bodies from providers are logged at `DEBUG` only.

---

## Logging

```text
AI processing started. Provider chain: grok -> gemini -> groq
Trying provider: Grok (xAI) (model=grok-4-fast, 1 of 3)
Grok (xAI) API error 401
Grok (xAI) failed: authentication rejected. Falling back to the next provider.
Trying provider: Google Gemini (model=gemini-2.0-flash, 2 of 3)
Gemini usage: prompt=1840 candidates=612 total=2452
AI processing completed using Google Gemini (model=gemini-2.0-flash) after falling back
```

Never logged: API keys, authorization headers, or transcript bodies. Provider error bodies go to
`DEBUG` because they can echo request content. The information kept at `INFO` is meeting id,
provider, stage, token usage and error category.

---

## Configuration

```env
# Fallback behaviour
LLM_PROVIDER_ORDER=grok,gemini,groq
LLM_PROVIDER_MAX_ATTEMPTS=2      # requests one provider may spend on one prompt
LLM_SCHEMA_RETRY_ATTEMPTS=2      # corrective re-prompts on unusable JSON
LLM_TEMPERATURE=0.1

# 1. xAI Grok — PRIMARY          https://console.x.ai
XAI_API_KEY=
XAI_MODEL=grok-4-fast
XAI_TIMEOUT_SECONDS=120

# 2. Google Gemini — FALLBACK 1  https://aistudio.google.com/apikey
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.0-flash
GEMINI_TIMEOUT_SECONDS=120

# 3. Groq — FALLBACK 2           https://console.groq.com/keys
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_TIMEOUT_SECONDS=120
```

All three keys are **backend-only**. They are read by `pydantic-settings` from `backend/.env`, are
never exposed to React, and must never be committed. `frontend/.env` contains only
`VITE_API_BASE_URL`.

Model names are configuration, not code — nothing in the business logic hardcodes one. Expected
defaults:

| Provider | Default model | Notes |
| --- | --- | --- |
| Grok | `grok-4-fast` | any model your xAI account can use |
| Gemini | `gemini-2.0-flash` | `gemini-2.5-flash`/`-pro` also work, but spend output tokens on thinking |
| Groq | `llama-3.3-70b-versatile` | any Groq model with JSON-mode support |

Models get deprecated. When one does, change the variable — no code change is needed.

---

## Database

The only schema change is one nullable column:

```sql
alter table public.meeting_summaries add column if not exists provider text;
```

It records which provider produced each analysis (`grok` / `gemini` / `groq`) and is surfaced in
the UI footnote. Run `backend/database/migrations/001_add_provider_to_meeting_summaries.sql` in the
Supabase SQL editor, or re-run `backend/database/schema.sql`, which now includes it.

**The migration is optional.** If the column is missing, the repository detects it on the first
write, logs a warning naming the migration, and stores the summary without the provider name.
Nothing else changes, and no other table was touched.

---

## Tests

`backend/tests/test_llm_fallback.py` — 32 tests, **all providers mocked, no key required, no real
API request made**, which is the point: the free-tier quota must not be spent on CI.

| Test | Asserts |
| --- | --- |
| Grok succeeds | Grok called once; Gemini and Groq **not called** |
| Grok fails → Gemini succeeds | Grok 1, Gemini 1, Groq **0**; `provider == "gemini"` |
| Grok + Gemini fail → Groq succeeds | 1, 1, 1; `provider == "groq"` |
| All fail | `LLM_ALL_PROVIDERS_FAILED`, no unhandled exception |
| Grok auth error | Grok called exactly once — no retry |
| Grok rate limited | Grok called exactly once — no extra quota spent |
| Grok returns invalid JSON | falls through to Gemini; the bad output is never returned |
| Gemini key missing | Gemini **never called**; Groq attempted |
| No keys at all | configuration error, zero requests |
| Only Grok configured and failing | error names `gemini` and `groq` as not configured |
| Order | providers called strictly `grok → gemini → groq` |
| Dead provider on a chunked transcript | fewer than one wasted call per chunk |
| Error mapping | each vendor's status → correct category, for all three providers |
| Secret hygiene | the API key appears in no message, detail or internal field |
