---
keywords: [crew, analog_clone, igloo, analog-surface, goclone, gsap, fallback, openvault]
main_idea: Crew analog_clone fetches a public URL, searches free clone/design tools, writes space jail index.html (tokens/layout DNA, not their assets), and opens it even when OpenVault is down.
models: [cursor-grok-4.6]
workflow: 2026-08-28_crew-analog-clone-igloo
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-08-28_crew-skill-ingest-search.md
repo: Cortex
date: 2026-08-28
---

# Crew analog_clone of igloo.inc (2026-08-28)

PREFLIGHT: HIT - crew-skill-ingest-search, stolen-ui-six-clones, crew-analog-prompt-wire.

## Main idea

Chat `clone https://www.igloo.inc/` starts a crew run with no model hop. `analog_clone` fetches the analog, searches goclone/GSAP/design-md, writes `index.html` + `ANALOG.md` in the space jail, and opens the local file. Impeccable (design-rules) already on disk is noted, not re-guessed. webbrowser.open must be a daemon thread or the run hangs after the write.

## Golden rule

> Analog = tokens and layout DNA. Search free tools first. Ask the operator for OpenVault/Meshy. Do not dump their images or brand as ours. Do not open a new paid account.

## Verify

```
GET detect clone https://www.igloo.inc/ -> Surface + analog-surface
pytest tests/test_crew/test_analog.py tests/test_crew/test_harness.py::test_analog_fallback_writes_when_model_hop_dies
# live space 6ad572a0bff1 run 6d7c2dadfdea status=done
# files data/crew/spaces/6ad572a0bff1/ws/index.html and ANALOG.md
# transcript: Ran analog_clone. opened=True. Ask operator for Meshy/OpenVault.
```

Not done: Manager tool-calling analog_clone (OpenVault :5000 down), MengTo/motion/Frontend-Design stack live, 3D/Meshy, pixel-close analog.
