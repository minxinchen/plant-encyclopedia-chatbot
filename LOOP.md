# Plant Encyclopedia Outer Loop

This file defines the system that prompts the agent. The durable machine state is `loop/state.json`; n8n is the scheduler and inner batch harness.

## Goal

Build a traceable text-and-image chatbot for the four-volume book. The chatbot may use Taiwan public sources only to choose display names and aliases; all plant facts must come from cited book pages.

## One iteration

`discover one eligible batch -> declare hypothesis/proof -> maker action -> observe real artifacts -> independent checker -> verdict -> persist state -> stop`

An iteration processes one taxon or at most six adjacent PDF pages. It starts fresh from durable state so context compaction or model changes cannot erase progress.

## Current autonomy

L1 report-and-candidate mode. The loop may read sources, produce candidate structured data, call free/local tools, and write inside this lab. It may not promote a full-volume index, activate a recurring schedule, change billing, delete source files, or publish a public chatbot without Nio's approval.

## Work discovery order

1. Resolve blockers on the current sample.
2. Complete one end-to-end sample from each volume.
3. Add held-out retrieval and refusal questions.
4. Only after sample gates pass, propose the first bounded production batch.

## Human ownership

Nio owns changes to autonomy level, paid services, public release and disputed Taiwan names. The agent owns tool routing, bounded retries, evidence capture and concise escalation packets.

## Stop conditions

Stop on proof-of-done, budget exhaustion, three repeated failures, maker/checker deadlock, missing authority, paid-only capability, unavailable source, or any requested action outside L1 permissions.
