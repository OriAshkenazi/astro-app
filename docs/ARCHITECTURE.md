# Architecture

## Goal
The app turns a hand-run, gated natal-chart pipeline into a web app:
- A user signs in with ChatGPT and enters birth date, time and place.
- They receive an interactive English reading that streams in section by section.
- They can request a translation.
- All LLM calls run on the user's ChatGPT plan through **Sign in with ChatGPT (SIWC)**.

Product decisions:
- SIWC is the only LLM driver.
- The quality mode (Standard / Thorough) and the translation language are user settings.
- The client is a responsive web app.

## System

```
Next.js (web, responsive)  ──SSE──▶  FastAPI (Python)  ──▶  Postgres (users, charts, sections, jobs)
        │  SIWC OAuth                      │                         ▲
        └──────────────────────────────────┘                         │
                                     Worker (Python, job DAG) ───────┘
                                       ├─ core/ (astronomy, gates)
                                       └─ LLM client → OpenAI via user's SIWC token
```

- **Backend:** Python, because the proven calculation code is Python (pyswisseph, Skyfield).
- **Jobs:** a Postgres-backed queue (Procrastinate) runs a DAG per reading. Each node writes its result row and emits an event, and the API streams the events over SSE.
- **Auth:**
  - SIWC OAuth with PKCE. Refresh tokens are encrypted at rest.
  - The worker refreshes tokens server-side, so jobs survive a closed tab.
  - If an LLM call fails because the user's per-app cap is reached, the section is marked `paused_quota` and resumes when the user chooses.
- **LLM interface:** one `LLMClient` interface, with SIWC as its only implementation. Self-hosters register their own SIWC client ID.

## Layout
```
core/       pure-Python astronomy + gates (no web deps)
agents/     prompts, style rules, glossaries, workflow DAG
api/        FastAPI: auth, charts, sections, settings, SSE
worker/     job tasks
web/        Next.js App Router
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
- **Onboarding:** Sign in with ChatGPT → birth form → mode → Generate.
- **Reading view:**
  - Layout: interactive wheel (sticky on desktop, collapsible on mobile) next to the section list.
  - Section list: a status chip on each entry, with skeletons for sections still being written.
  - Wheel interaction: clicking a planet, house or aspect jumps to the relevant paragraphs.
  - Glossary tooltips on technical terms.
  - Language toggle.
  - Aspect table with filters.
- **Settings:** quality mode, default translation language, PDF export, account and data deletion.
- **Privacy:** birth data is encrypted at rest, users can hard-delete it, and there is no analytics on chart content.

## Milestones
1. **Core:** two-engine calculation, G1 gate, canonical builder, sensitivity scan, wheel, deterministic tables, text checks.
2. **SIWC auth and LLM client:** token store, refresh, quota handling.
3. **Workflow and worker:** DAG, Standard mode, SSE, section storage and versioning.
4. **Web reading view.**
5. **Thorough mode and translation.**
6. **Polish:** PDF export, settings, deletion, self-hosting docs.

## Verification
- Golden tests reproduce a hand-verified reference chart field-for-field. They are private and skipped in CI.
- Edge-case tests: DST gap and overlap, unknown time, southern hemisphere, polar latitude.
- Ported text checks must pass on the reference reading.
- End-to-end: docker-compose stack plus a SIWC test account.
