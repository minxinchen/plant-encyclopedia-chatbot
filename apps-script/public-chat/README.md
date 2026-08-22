# Google Apps Script 公開聊天介面

author: Codex (GPT-5)  
date: 2026-08-22

公開 beta：<https://script.google.com/macros/s/AKfycbyXlLIc2tzTqhXMRGxRoNVPLkrgrCm3MosSBT9SBo_8OPmgqdTLBD91Qxley9fgEnYIhg/exec>

介面沿用 `demo-site/` 的深綠植物圖鑑視覺與聊天泡泡版型。Apps Script
負責密碼、流量限制與公開 UI，回答則經 Bearer-protected HTTPS gateway 呼叫本機
hybrid RAG 與 Qwen API。Qwen 關閉時介面會明確回覆本機模型離線，不回退到
Gemini 生成。

這個資料夾是可移植的 Apps Script web app。瀏覽器端不會取得 gateway token、
本機端點或任何模型 API key。

必要的 Script Properties：

- `LOCAL_QWEN_GATEWAY_URL`：HTTPS gateway base URL，不含 `/v1/chat`。
- `LOCAL_QWEN_GATEWAY_TOKEN`：至少 32 字元的 Bearer token。
- `APP_PASSWORD_SHA256`：網站密碼的 SHA-256 URL-safe Base64 雜湊；不可把明文密碼寫入程式碼。

部署設定：Execute as `Me`，Who has access 選 `Anyone`。不要把任何 Script Property、原始 PDF、SQLite 或向量檔提交到 Git。

本機回答入口是 `scripts/fullbook-chat-api.py`，公開縮限 gateway 是
`scripts/public-chat-gateway.py`。gateway 只接受 `/health` 與 `/v1/chat`，限制
8 KiB payload、500 字問題與明列 request fields，且不會 proxy 任意 URL。主索引
chunking 固定為 512 tokens / 100 overlap。

公開 beta 的保護：伺服器端密碼驗證（每 10 分鐘最多 5 次、登入 token 六小時有效）、500 字輸入上限、每個匿名瀏覽器 token 每 10 分鐘最多 10 次、醫療主題只允許書中歷史記載、不補充現代診斷／劑量／療效／安全結論，現代藥物與星座問題在本機模型前拒答，每個植物事實行須通過 `[kohler-volume-N PDF p.N]` 句尾 gate。

AI_WORKSTATION 操作：

```bash
./service qwen on 35b-a3b
./service plant-chat on
./service plant-gateway on
```

也可以在 Dashboard 的 Services 區塊選擇 Qwen 模型並按 Start／Stop。Quick
Tunnel 的公開 URL 會寫入
`services/plant-encyclopedia-public-gateway/runtime/public-url`；它是測試用臨時
hostname，重啟 tunnel 後必須同步更新 `LOCAL_QWEN_GATEWAY_URL`。
