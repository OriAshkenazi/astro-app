# astro-app

An open-source natal-chart reading app that runs on your own computer. You start it with `astro-app`, sign in with ChatGPT, enter a birth date, time and place, and get a full interactive reading in English in your browser. Sections stream in as each one passes its quality gates, and you can ask for a translation.

The LLM work runs on **your own ChatGPT plan allowance** (Plus or Pro) through OpenAI's open-source *Sign in with ChatGPT* flow. There are no API keys, no server of ours, and your readings stay on your machine.

## How a reading is made

1. **Two-engine calculation gate.**
   - Skyfield + JPL DE421 (with its own angle and Placidus math) produces the canonical values.
   - Swiss Ephemeris independently confirms them: planets within 0.02°, angles and cusps within 0.05°, identical aspect set.
   - If the engines disagree, the reading is blocked.
2. **Derived facts in code, not by the LLM.** Rulers, dispositors, dignities, aspect patterns, intercepted signs, emphasis, and a birth-time sensitivity scan (which facts change if the recorded time is off by a few minutes).
3. **Agentic interpretation.**
   - Each section is written by an interpreter agent.
   - Each section is then checked twice: numerically against the canonical data, and by a critic agent for accuracy, depth and plain language.
   - Two quality modes: *Standard* (one critic pass) and *Thorough* (review loops plus a cross-section consistency pass).
4. **Translation on demand.** A separate translator agent works from the locked English with a glossary, and its output is gated by number and structure parity checks.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design and milestones.

## Status

Milestone 1 (core) is complete:
- two-engine calculation with the G1 gate, canonical builder and birth-time sensitivity scan;
- G2/G3 text gates (`astro_app/core/checks.py`);
- interactive SVG wheel with `data-factor` targets (`astro_app/core/wheel.py`);
- deterministic reading sections 1–5 (`astro_app/core/tables.py`).

Milestone 2 (Sign in with ChatGPT + LLM client) is implemented and awaiting a first real-account test:
- `astro_app/auth`: OpenAI's open-source SIWC flow (dynamic registration, loopback callback, verified ID tokens,
  rotating refresh, revocation);
- `astro_app/llm`: Responses API client on the user's ChatGPT plan, plus a fake client for tests.

Try it:

```bash
astro-app login     # Continue with ChatGPT in your browser
astro-app ping      # one tiny request on your ChatGPT plan
astro-app chart --date 2001-05-10 --time 14:30 --lat 51.5074 --lon -0.1278 --tz Europe/London --place London
```

Next: Milestone 3, the local server and reading workflow.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

- **Ephemeris file:** the JPL DE421 kernel (`de421.bsp`, ~17 MB) is downloaded into `~/.astro-app/ephe/` on first use (override the base with `ASTRO_HOME`). Set `ASTRO_EPHE_DIR` to use an existing copy.
- **Golden tests:** these compare the pipeline against a privately held, hand-verified reference chart. They are skipped unless `ASTRO_REF_DIR` points at it. No personal birth data is stored in this repository.

## License

AGPL-3.0-or-later, because the Swiss Ephemeris (via `pyswisseph`) is AGPL-licensed.
