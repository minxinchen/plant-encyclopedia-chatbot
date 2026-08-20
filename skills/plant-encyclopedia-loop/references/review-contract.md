# Review contract

## Maker/checker separation

| Maker | Eligible checker |
|---|---|
| Poppler or deterministic parser | schema validator plus sampled Codex inspection |
| Qwen 9B extraction | Qwen 35B-A3B only for ambiguity, plus deterministic evidence validator |
| Qwen answer draft | Gemini grounded review or Codex sampled adjudication |
| Gemini image interpretation | book caption/text comparison plus Codex sampled adjudication |
| Gemini embedding retrieval | exact/BM25 regression set plus a different generative reviewer |

NotebookLM or a Gemini Notebook is an independent source-grounded comparison surface, not canonical storage.

## Required proof for promotion

- Source PDF remains unchanged and available.
- Each chunk retains source ID, PDF page, evidence type and plant record ID.
- Display name records authority, URL, checked time and Taiwan occurrence separately.
- Image linkage has plate page, linked taxon and multimodal checker verdict.
- Answer tests include answerable, ambiguous, wrong-name and unanswerable questions.
- Paid fallback is false and incremental API cost is zero.

## Verdict meanings

- `promote`: move only this batch from candidate to approved index.
- `retry_changed_strategy`: change tool, context, OCR method or batch boundary; do not repeat the same prompt.
- `hold_for_evidence`: keep candidate data, exclude it from chat, and record the missing evidence.
- `escalate`: require Nio's judgment or a new authorization.
- `stop_complete`: the declared scope is fully verified; do not invent more work.
