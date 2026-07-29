# Capture template

```yaml
id: YYYY-MM-DD_<source>_<topic>
source: claude-code|cursor|claude-app|cowork|manual
date: YYYY-MM-DD
operator: <who ran the ask>
prompt_used: skill_distill/prompts/<FILE>.md
distill_trace: skill_distill/DISTILL.md
status: raw|normalized|promoted
```

## Raw answer

<!-- Paste the full model response below -->

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| | observed/docs/inferred | high/med/low | rule/skill/subagent/parking/none |

## Action YAML

```yaml
# paste PART 7 blocks here
```

## Netie implications

- Build now:
- Park (condition):
- Tests required:

## Citations

- distill: skill_distill/captures/<this-file>.md
