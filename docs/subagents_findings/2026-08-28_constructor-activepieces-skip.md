---
keywords: [constructor, activepieces, skip, distill, piece-category, dag_runner]
main_idea: Do not clone Activepieces. Distill CORE primitives + trigger strategies + domain categories into missing Cortex action types only. SKIP server/engine/UI and all 665 community pieces.
models: [cursor-grok-4.6]
workflow: 2026-08-28_constructor-activepieces-skip
reuse: golden_rule
status: verified
cite: distill: E:\Netie\TAS\TAS-CONSTRUCTOR.md
repo: Cortex
date: 2026-08-28
---

# Constructor vs Activepieces (2026-08-28)

PREFLIGHT: HIT - TAS-CONSTRUCTOR + both READMEs. Constructor is Cortex canvas skin.

## Main idea

SKIP clone. Distill taxonomy only if a Cortex action type is missing. No second DAG.

## DISTILL worth (ideas, not SDKs)

Source: `packages/shared/.../pieces/piece.ts` `PieceCategory` + CORE pieces + `TriggerStrategy`.

1. CORE primitives if missing: http, webhook, schedule, delay, store, data-mapper, text-helper, file-helper, csv, smtp, sftp, forms, manual-trigger, subflows, approval
2. Trigger strategies (ideas): POLLING, WEBHOOK, APP_WEBHOOK, MANUAL (+ cron)
3. Domain categories for Netie-owned connectors: COMMUNICATION, CONTENT_AND_FILES, DEVELOPER_TOOLS, PRODUCTIVITY, SALES_AND_CRM, CUSTOMER_SUPPORT, BUSINESS_INTELLIGENCE, ARTIFICIAL_INTELLIGENCE / UNIVERSAL_AI
4. Lower priority unless PRD gap: COMMERCE, ACCOUNTING, PAYMENT_PROCESSING, MARKETING, HUMAN_RESOURCES, FORMS_AND_SURVEYS, FLOW_CONTROL

Constructor kinds stay: connector -> ontology -> insight -> foundry -> app. Do not import AP piece names as kinds.

## Hard SKIP

packages/server, packages/engine, flow runner/worker/queue, react-ui/web, cli, ee, deploy, verdaccio, e2e, 665 community pieces, AP auth vault, piece registry, template marketplace, SCIM, flow-control as a second DAG.
