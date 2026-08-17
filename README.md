# model-chat-completions-params

對 OpenAI-compatible 閘道做**單模型上架檢測**：確認 `POST /v1/chat/completions` 的 reasoning、JSON、tools、stream 等參數是**真支援**還是**只回 200 沒生效**。

| 檔案 | 用途 |
|------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 測什麼（A→F 流程與判定） |
| [PARAMS.md](./PARAMS.md) | Gemma 4 / Kimi K3 官方參數（說明，不進腳本） |
| [RESULTS.md](./RESULTS.md) | inner-medusa 實測摘要 |
| `env.example` | 複製成 `.env` 後填變數 |
| `scripts/probe_flowchart.py` | 探測腳本（`curl`） |
| `configs/profiles/generic.json` | 考卷（三句 prompt） |
| `results/` | 當次輸出 JSON |

## 跑一次

```bash
cp env.example .env
# 必填：OPENAI_BASE_URL、OPENAI_API_KEY、MODEL
# 可選：REASONING_EFFORT、ENABLE_THINKING（僅 Gemma 4，見 .env 註解）
python3 -u scripts/probe_flowchart.py
```

已 `export` 的同名變數會蓋過 `.env`：

```bash
unset MODEL OPENAI_BASE_URL OPENAI_API_KEY REASONING_EFFORT ENABLE_THINKING
```

## 環境變數

| 變數 | 必填 | 說明 |
|------|------|------|
| `OPENAI_BASE_URL` | ✅ | 閘道 `/v1` 根網址 |
| `OPENAI_API_KEY` | ✅ | API 金鑰 |
| `MODEL` | ✅ | `GET /models` 上的 id |
| `REASONING_EFFORT` | — | 逗號分隔；空白＝掃 `low,medium,high,max,none` |
| `ENABLE_THINKING` | — | `true`＝每槍帶 `chat_template_kwargs.enable_thinking=true`（**僅 Gemma 4**） |

## 判定

| 終端 | 意思 |
|------|------|
| OK | 預期成立 |
| NOOP | 200 但參數／欄位未生效 |
| SKIP | 該參數閘道不接受（4xx） |
| FAIL | 應成功卻失敗 |

## 架構概念

客戶端 → **LiteLLM 閘道** → 上游（vLLM 自架模型、Moonshot Kimi 等）。  
腳本測的是**整條鏈的交集**，不是單看 vLLM 或 Kimi 文件。詳見 `PARAMS.md`「LiteLLM / 閘道注意」。
