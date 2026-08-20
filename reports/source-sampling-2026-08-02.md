# 來源抽樣報告 — 2026-08-02

author: Codex GPT-5
date: 2026-08-02

## 結論

四冊共 1,774 個 PDF 頁、2,882,766,086 bytes。適合做 embedding，但必須先以植物條目為單位重建資料，不能直接把固定長度的 OCR 頁面切塊後全部向量化。

## 抽樣觀察

- 四冊都有可擷取文字層，多數正文頁約 3,000–8,000 個字元。
- 第二、三冊可見單字字母被 OCR 拆開；第一、四冊亦有變音符號、舊式德文拼字及辨識錯誤。
- 部分頁只有圖版標題或完全無文字，不能用「文字為空」判定沒有知識內容。
- 正文條目可辨識固定欄位：學名、異名、科屬、描述、分布、名稱與歷史、花期、採集、成分、歷史用途、文獻、圖版說明。
- 彩色圖版與正文分頁存在；抽到的 `Saponaria officinalis L.` 圖版包含整株及局部構造編號，應保留圖像與正文的關聯。
- 抽到的舊名包括 `Quercus sessiliflora Sm.`；名稱解析必須保留書中原名，另建 accepted name，不可直接覆寫原文。

## 臺灣名稱資料策略

TaiCOL 是第一優先，因其明確整合臺灣物種學名、同物異名、中文名及相關屬性，並持續由文獻與分類專家修訂：<https://taicol.tw/zh-hant/about>。

台灣植物資訊整合查詢系統作第二層植物名彙核對；網站目前標示整合 6,550 種植物、27,232 筆名彙及多套植物學文獻：<https://tai2.ntu.edu.tw/>。

本次已將兩筆臺大植物系統的公開名稱保存為 provisional sample：`Cibotium barometz`＝「金狗毛蕨」，在臺灣有紀錄；`Saponaria officinalis`＝「肥皂草」，但資料庫明確標示為非臺灣植物。兩筆仍待 TaiCOL 直接查詢後才升為正式對照，避免把「臺灣資料庫有名稱」誤寫成「臺灣有分布」。

## 架構選擇

- 核心：本機可版本化的抽取、名稱解析、chunk schema、hybrid retrieval 與回答 validator。
- n8n：Phase 4 才包裝批次、重試、斷點續跑與排程；不保存名稱決策真相。
- Gem：可做展示，但不作主要實作。Google Gemini Apps 的現行一般文件上傳限制為每個非影片檔 100 MB；四冊中有兩冊超過限制，分別約 954 MB 與 1.80 GB。來源：<https://support.google.com/gemini/answer/14903178>。

## 下一個 gate

已抽取第四冊 11 個 prototype 頁、54,483 個文字字元：`Cibotium barometz` 正文 PDF 30–32、圖版 PDF 79；`Saponaria officinalis` 正文 PDF 48–53、圖版 PDF 85。下一步是做條目組裝、OCR 清理與第一版 retrieval 測試；通過後再擴到每冊 2–3 個條目，最後才全書 embedding。
