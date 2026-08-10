# model-chat-completions-params

整理不同模型在 `POST /v1/chat/completions` 啟用 `response_format` / reasoning 的方式。

**統一測試端點**：`https://inner-medusa.genai.nchc.org.tw/v1`

| 檔案 | 內容 |
|------|------|
| [PARAMS.md](./PARAMS.md) | 官方參數對照（Gemma 4 / Kimi K3 / LiteLLM / vLLM）+ 來源連結 |
| [TESTPLAN.md](./TESTPLAN.md) | 測試項目清單 |
| [RESULTS.md](./RESULTS.md) | inner-medusa 實測結果表 |
| [scripts/probe_chat_completions.py](./scripts/probe_chat_completions.py) | 探測腳本 |
| [results/](./results/) | 腳本輸出 JSON |

```bash
export OPENAI_BASE_URL=https://inner-medusa.genai.nchc.org.tw/v1
export OPENAI_API_KEY=...
export MODELS=Kimi-K3-max,gemma-4-31B-it,gpt-oss-120b
python3 scripts/probe_chat_completions.py
```
