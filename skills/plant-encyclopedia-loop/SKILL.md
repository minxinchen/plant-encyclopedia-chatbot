---
name: plant-encyclopedia-loop
description: Run and govern the autonomous evidence loop for the Köhler plant encyclopedia chatbot. Use when Codex must discover the next bounded PDF batch, coordinate local extraction, Taiwan public-name resolution, Gemini multimodal indexing, Qwen drafting, independent evidence review, persistent state, budgets, retry decisions, or safe stopping for this lab.
---

# Plant Encyclopedia Loop

Operate the agent-driven outer loop. Treat n8n and deterministic scripts as the inner harness, not as the loop itself.

## Start from durable state

1. Read `/Users/user/AI_WORKSTATION/labs/plant-encyclopedia-chatbot/LOOP.md` completely.
2. Read `loop/state.json` and `loop/budget.json`.
3. Read `config/tool-routing.json` before selecting a model or connector.
4. Inspect the last five records in `loop/run-log.jsonl` when it exists.
5. Read `references/review-contract.md` when judging evidence or promotion.

Do not rely on the current chat as the project spine.

## Execute one outer-loop iteration

1. Discover the highest-priority eligible work item from state. Select exactly one bounded batch.
2. State a testable hypothesis and proof-of-done before acting.
3. Choose the maker tool from the routing config. Prefer local deterministic extraction, then local Qwen; use Gemini only where multimodal or cross-language value is material.
4. Run the maker and capture source pages, tool/model version, inputs, outputs, elapsed time and zero-cost status.
5. Send raw evidence and maker output to an independent checker. A checker must differ by model family or be deterministic; a model may not approve its own output.
6. Decide one verdict: `promote`, `retry_changed_strategy`, `hold_for_evidence`, `escalate`, or `stop_complete`.
7. Update state and append one compact run record. Never rewrite prior run-log entries.
8. Stop after one batch. Let the scheduler start another fresh iteration only if the state remains eligible.

## Preserve evidence boundaries

- Supply plant facts only from the book evidence.
- Use TaiCOL, Tai2 or another authority only for names, aliases and occurrence metadata.
- Keep `book_scientific_name` even when an accepted name differs.
- Cite source ID and PDF page for each factual answer sentence.
- Link image claims to a plate page and require Gemini multimodal review.
- Return `本書未記載` when retrieval has no supporting book evidence.
- Label historical medicinal claims and never convert them into medical advice.

## Apply circuit breakers

Stop and escalate when any condition is true:

- The same failure class occurs three consecutive times.
- A tool requests billing or a paid fallback.
- The next action requires changing or deleting source files.
- A Taiwan name decision lacks a public source or conflicts across authorities.
- Maker and checker disagree after one changed-strategy retry.
- The iteration exceeds the limits in `loop/budget.json`.

Do not expand batch size merely because a run passes. Promote from L1 only after the state records enough sampled successes and Nio explicitly approves the next autonomy level.
