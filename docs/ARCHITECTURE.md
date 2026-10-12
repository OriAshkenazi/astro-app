# Architecture

## Goal
The app turns a hand-run, gated natal-chart pipeline into a **local app**:
- The user installs it and runs `astro-app`. It opens in their browser at `http://127.0.0.1`.
- They sign in with ChatGPT and enter birth date, time and place (for themselves or anyone else; readings are saved locally).
- They receive an interactive English reading that streams in section by section, and can request a translation.
- All LLM calls run on the user's ChatGPT plan through **Sign in with ChatGPT (SIWC)**.

Product decisions:
- **SIWC is the only LLM driver.** No API keys, no own billing.
- **Local only.** This uses OpenAI's open-source SIWC track, which covers open-source and locally hosted apps and needs no partner application. A remotely hosted multi-user website would need OpenAI's partner interest form, so it is out of scope.
- **Settings:** the quality mode (Standard / Thorough) and the translation language.
- **UI:** responsive, so it also works from a phone on the same machine or network if the user chooses.

## System

```
Browser (web UI, static build)  ──HTTP + SSE──▶  local server (FastAPI, 127.0.0.1)
                                                    ├─ SQLite (~/.astro-app/astro.db): readings, sections, jobs
                                                    ├─ job runner (in-process asyncio DAG)
                                                    │    ├─ core/ (astronomy, gates, wheel, sections 1–5)
                                                    │    └─ LLM client → Responses API with the user's SIWC token
                                                    └─ SIWC OAuth loopback callback (127.0.0.1)
```

- **Packaging:**
  - `pipx install astro-app` (or `uvx astro-app`) provides an `astro-app` command. It starts the server on a free loopback port and opens the browser.
  - The web UI is a static build shipped inside the Python package, so users never need Node.
- **Storage:**
  - Readings, sections and jobs live in SQLite under the user's data directory.
  - Birth data never leaves the machine except in LLM requests, which go to OpenAI with `store: false`.
- **Jobs:** an in-process asyncio runner executes a DAG per reading. Each node writes its row and emits an event, and the server streams events over SSE. Unfinished jobs resume on the next start.
- **Auth (OpenAI open-source SIWC flow):**
  - Before the first sign-in, the app generates and persists a stable `ext_agent_host_id`, either a UUID URN or a JWK-thumbprint URN.
  - Sign-in is OIDC with PKCE: the system browser authorises, and the redirect goes to the app's `127.0.0.1` callback. The first sign-in registers the client dynamically, and the issued `client_id` is saved for reuse.
  - The app validates the ID token and the granted scopes, then stores tokens in the OS keychain (`keyring`), falling back to a 0600 file.
  - The app refreshes tokens itself, so a reading continues if the browser tab is closed.
  - If the user's weekly per-app cap is reached, the affected section is marked `paused_quota` and resumes when the user chooses.
  - **Running on a VM:** for a self-hosted VM, the user signs in locally and transfers the credential file over SSH, per OpenAI's self-hosted VM guide.
- **LLM interface:**
  - One `LLMClient` interface, with SIWC (Responses API, streaming, `store: false`) as the only real implementation.
  - A deterministic fake implementation is used in tests and CI.
  - OpenAI's SIWC DevKit is not used: its noncommercial licence is incompatible with AGPL. The flow is implemented from the public docs.

## Layout
```
astro_app/
  core/     pure-Python astronomy, gates, wheel, sections 1–5 (no web deps)
  agents/   prompts, style rules, glossaries, workflow DAG
  server/   local server: FastAPI routes, SSE, SQLite store, job runner, CLI entry point
  auth/     SIWC open-source OAuth flow, host ID, token store
  llm/      LLMClient interface, SIWC Responses client, fake client
web/        web UI source (built to static files shipped in the package)
tests/      unit, edge-case and (private-fixture) golden tests
```

## Reading workflow
1. **Input normalisation:**
   - Place lookup uses geocoding with a typeahead.
   - The time zone comes from the coordinates and is converted to a historical UTC offset with IANA tzdata.
   - DST-gap and overlap times are returned to the user to choose (`timeutil.LocalTimeError`).
   - An unknown birth time produces a noon chart without houses or angles.
2. **G1, the calculation gate:**
   - Skyfield/DE421 is canonical and Swiss Ephemeris confirms it.
   - Tolerances: planets ≤ 0.02°, angles and cusps ≤ 0.05°, identical aspect set (orb-limit edge cases are noted, not failed).
   - Placidus falls back to Porphyry above the polar circle.
3. **Instant render:** the wheel and the deterministic sections (cover, methodology, positions, cusps, wheel) appear immediately. The disclaimer is static.
4. **Interpretation:**
   - Interpretive sections are written in parallel, one agent call each, from the canonical JSON plus the section spec and the plain-language style rules.
   - Output is structured: paragraphs tagged as calculated fact, traditional symbolism or interpretive possibility, plus the factor IDs each paragraph refers to. The factor IDs drive wheel highlighting.
5. **G2, the section gate:** numeric checks first (every degree and orb string must match the canonical data), then the critic agent, which checks accuracy, depth and plain language. The modes differ only in this gate and in translation:
   - **Standard:**
     - Critic: one pass, using the same accuracy, depth and plain-language criteria.
     - Revisions: one revision if the critic fails the section.
   - **Thorough:**
     - Critic: loops up to 3 rounds, plus a final cross-section consistency pass.
     - Revisions: as many as the critic loop needs, within the 3-round cap.
     - Translation: adds back-translation review.
6. **Publish:** when a section passes, its row becomes `ready` and an SSE event updates the UI.
7. **G3, the translation gate:**
   - A blind translator agent works per section from the locked English plus a glossary.
   - Parity checks: section IDs, numbers, tag counts, no bidi control characters.
   - RTL languages render `dir="rtl"`.

Every section version is stored with its SHA-256. Regenerating a section reuses the canonical data.

## Web UI
- **Onboarding:** `astro-app` opens the browser → Continue with ChatGPT (consent to use the ChatGPT plan) → birth form → mode → Generate.
- **Reading view:**
  - Layout: interactive wheel (sticky on desktop, collapsible on mobile) next to the section list.
  - Section list: a status chip on each entry, with skeletons for sections still being written.
  - Wheel interaction: clicking a planet, house or aspect jumps to the relevant paragraphs.
  - Glossary tooltips on technical terms.
  - Language toggle.
  - Aspect table with filters.
- **Settings:** quality mode, default translation language, PDF export, sign out, and deletion of saved readings and credentials.
- **Privacy:** everything is stored on the user's machine; there is no server of ours, no telemetry, and LLM requests use `store: false`.

## Milestones
1. **Core:** two-engine calculation, G1 gate, canonical builder, sensitivity scan, wheel, deterministic tables, text checks.
2. **SIWC auth and LLM client:** host ID, loopback OAuth with dynamic registration, keychain token store, refresh, quota handling, fake client for tests.
3. **Local server and workflow:** CLI entry point, SQLite store, in-process job DAG, Standard mode, SSE, section versioning.
4. **Web reading view.**
5. **Thorough mode and translation.**
6. **Polish:** PDF export, settings, deletion, packaging (`pipx`/`uvx`), self-hosted VM docs.

## Verification
- Golden tests reproduce a hand-verified reference chart field-for-field. They are private and skipped in CI.
- Edge-case tests: DST gap and overlap, unknown time, southern hemisphere, polar latitude.
- Ported text checks must pass on the reference reading.
- End-to-end: `astro-app` on a clean machine with a real ChatGPT Plus/Pro account; generate a reading in both modes and a translation.
