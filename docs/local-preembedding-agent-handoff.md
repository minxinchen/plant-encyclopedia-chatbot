# 本地 agent：Köhler embedding 前置分工

author: Codex (GPT-5)  
date: 2026-08-13

## 目的

在不呼叫外部 API、不寫正式 record／chunk／SQLite 的前提下，把四冊 1,774 頁整理成可驗證的 embedding-ready staging。所有 plant facts 仍只能來自凍結的書頁文字；台灣名稱未知時必須保持 `unresolved`。

## 八個互斥 shard

| Shard | 唯一寫入範圍 | 主要工作 |
|---|---|---|
| S01 | Volume 1 PDF 1–120 | text + 少量 OCR |
| S02 | Volume 1 PDF 121–410 | text + OCR-heavy |
| S03 | Volume 2 PDF 1–280 | text + 少量 OCR |
| S04 | Volume 2 PDF 281–504 | text + 少量 OCR |
| S05 | Volume 2 PDF 505–738 | OCR-heavy／遺漏 heading discovery |
| S06 | Volume 3 PDF 1–204 | text + 少量 OCR |
| S07 | Volume 3 PDF 205–536 | text + OCR-heavy |
| S08 | Volume 4 PDF 1–90 | text + plate/OCR review |

每個 agent 只可寫：

```text
data/candidates/preembedding-v1/shards/<SHARD>/maker/
```

不得修改：來源 PDF、`data/fulltext/*.sqlite*`、`data/index/*.sqlite*`、正式 `data/records/`、`data/contracts/`、`data/chunks/`、`data/tests/`、`loop/`、`reports/`、`demo-site/`。

## 執行順序

1. Coordinator 產生與驗證 frozen shards：

   ```bash
   cd /Users/user/AI_WORKSTATION/labs/plant-encyclopedia-chatbot
   python3 scripts/prepare-local-preembedding-shards.py
   python3 scripts/validate-local-preembedding-shards.py
   ```

2. 本地模型由 coordinator 單一啟動；worker 不得切換模型。模型 request 有全域 file lock，同時間最多一個。

3. 每個 text agent 執行自己的 shard；先用小量 smoke test：

   ```bash
   python3 scripts/run-local-preembedding-worker.py S01 --limit 1
   ```

   已驗證後可由單一可續跑 coordinator 自動輪詢全部 shard，不需要人工逐次輸入「繼續」：

   ```bash
   python3 scripts/run-local-preembedding-batch.py
   ```

   即時狀態寫在 `data/candidates/preembedding-v1/batch-status.json`；每輪只處理各 shard 一筆，避免單一 shard 長時間壟斷。完成後預設自動關閉 Qwen。

4. deterministic status 只允許 `pass` 或 `needs_review`。本地模型一律是 maker，不能把自己升成 approved。

5. OCR worker 另處理 `inputs/ocr-pages.jsonl`，輸出至 `maker/ocr-candidates/`；不得覆寫 frozen page text。

   Apple Vision OCR 的單一 slot 可續跑全部 shard：

   ```bash
   python3 scripts/run-local-ocr-batch.py
   ```

   即時狀態寫在 `data/candidates/preembedding-v1/ocr-batch-status.json`；只要頁面 render 或 OCR 失敗，也會留下 `ocr_error` terminal candidate，避免整批卡住或把失敗誤當成功。

   使用者指定的 Chandra OCR 2 已排隊接棒。它必須等待 Qwen 結構批次完成並釋放 unified memory，然後先過 Köhler volume 2 PDF p509 golden gate；通過才停止 Apple Vision，接續輸出至 `maker/chandra-ocr-candidates/`：

   ```bash
   /Users/user/AI_WORKSTATION/services/chandra-ocr/.venv/bin/python \
     scripts/run-chandra-ocr-batch.py
   ```

   狀態在 `data/candidates/preembedding-v1/chandra-ocr-batch-status.json`。Chandra 與 Qwen 不可同時跑；實測並行會造成約 4.8 GB swap。

6. 最後由單一 integrator 依 `source_id + pdf_page + exact quote` 驗證後，才決定是否升 canonical、查台灣名稱、產生 512/100 chunks、呼叫 embedding 與重建主索引。

## 完成門檻

- 1,774 頁都有且只有一個 shard owner。
- 631 個 OCR queue 頁都有 terminal candidate disposition。
- selector 找到的每個 heading 都有 eligible／long-span／terminal／quality／approved disposition。
- 每個模型 section 的 exact quote 必須是同頁 frozen text 的 substring。
- `display_name=null`、`name_resolution.status=unresolved`，除非後續另有台灣公開來源。
- 不能產生 image claim、現代醫療建議或 `sample_reviewed`／`human_verified`。
- candidate 512/100 chunks 不能含 vector；embedding 由 integrator 另建 vector space。
