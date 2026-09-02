Skill-ingest: named skill not on the roster. Search, fetch, classify, save. Do not guess the GitHub owner.

When: add/install/save a skill, "skill storage", a GitHub skill pack, or a name you cannot load_skill.

Do this turn:
1. If the roster already has that name with a matching label, load_skill it. Stop.
2. web_search "{name} github skill" and "{name} skill.md".
3. github_search "{name}". Prefer a repo whose README or SKILL.md is a playbook, not a random namesake.
4. web_fetch the README (raw.githubusercontent.com or github.com blob). If SKILL.md exists, fetch that too.
5. Classify labels from what it is FOR, not from the operator's wording: design-rules, motion, frontend, clone-website, 3d, outreach, ...
6. save_skill(name, body, labels, source=repo url). Body is a short playbook the crew can follow, not a dump of the whole repo.
7. Stack vs cascade: if two skills share one job, merge into one labeled skill with sections. If they conflict or are huge, keep them separate and load in order.

Ask the operator before any login, paid API, or OpenVault secret. Quota/exhausted: say so. Do not open a new third-party account.
Do not invent a fourth skill store. Teach files under data/crew/skills win.
