# 實測結果（inner-medusa）

- **端點**：`https://inner-medusa.genai.nchc.org.tw/v1`
- **時間**：2026-08-10
- **腳本**：`scripts/probe_chat_completions.py`
- **原始 JSON**：`results/probe_inner_medusa_20260810.json`

## 結論

| 模型 | json_object | json_schema | `"json"` 字串 | reasoning | effort |
|------|-------------|-------------|---------------|-----------|--------|
| **Kimi-K3** / `-low` / `-high` / `-max` | ✅ | ✅ | ❌ 500 | ✅ 永遠有 `reasoning_content` | ✅ `reasoning_effort` 或別名 |
| **gemma-4-31B-it** | ✅ | ✅ | ❌ 500 | ❌ thinking 參數未生效 | ❌ 接受但無 effect |
| **gpt-oss-120b** | ✅ | ✅ | ❌ 500 | ✅ | ✅ `low\|medium\|high`；`none`/`max` ❌ |

1. 結構化輸出請用物件：`{"type":"json_object"}` 或 `json_schema`；**不要**傳字串 `"json"`。
2. **Kimi-K3**：永遠 thinking；effort 用頂層 `reasoning_effort=low|high|max`，或選別名 `Kimi-K3-low|high|max`。
3. **Gemma-4**：官方要用 `chat_template_kwargs.enable_thinking`，但此端點目前**打不穿**（HTTP 200、無 reasoning 欄位）。
4. **gpt-oss-120b**：`reasoning_effort` 有效；值僅 `low|medium|high`。

---

## A. Kimi-K3-max / Kimi-K3-low

| case | HTTP | reasoning | 備註 |
|------|------|-----------|------|
| baseline | 200 | 有 | |
| `json_object` / `json_schema` | 200 | 有 | content 合法 JSON |
| `"response_format":"json"` | 500 | — | Unsupported type - json |
| `enable_thinking=false` | 200 | 仍有 | K3 關不掉 |
| `reasoning_effort=none` | 200 | 仍有 | 對 Kimi 無效語意 |
| `thinking={type:enabled}` | 400 | — | hosted_vllm 不支援 |
| `reasoning_effort=low/max` | 200 | 有 | |

### Effort 對照（簡單題 15×37）

| 呼叫 | reasoning_tokens（約） |
|------|------------------------|
| `Kimi-K3-low` | 34 |
| `Kimi-K3-high` | 29 |
| `Kimi-K3-max` | 59 |
| `Kimi-K3` + `reasoning_effort=low` | 16 |
| `Kimi-K3` + `reasoning_effort=high` | 13 |
| `Kimi-K3` + `reasoning_effort=max` | 14 |

---

## B. gemma-4-31B-it

| case | HTTP | verdict | reasoning | 備註 |
|------|------|---------|-----------|------|
| baseline | 200 | OK | 無 | |
| `json_object` / `json_schema` | 200 | OK | 無 | |
| `"response_format":"json"` | 500 | FAIL | — | |
| `enable_thinking=true` | 200 | WARN | 無 | token 數≈baseline |
| `reasoning_effort=high` | 200 | WARN | 無 | 無 effect |
| `thinking={type:enabled}` | 400 | FAIL | — | unsupported |

深挖（同一證明題）：`enable_thinking=true` 與 baseline 的 `completion_tokens` 幾乎相同 → 參數疑似被 LiteLLM 丟棄。  
`allowed_openai_params` 強制透傳 `chat_template_kwargs` → **500** unexpected keyword。

---

## C. gpt-oss-120b

| case | HTTP | 備註 |
|------|------|------|
| `reasoning_effort=high/low` | 200 | 有 `reasoning_content`；high 通常較長 |
| `reasoning_effort=none` | 400 | Harmony 不支援 |
| `reasoning_effort=max` | 400 | 僅 high/medium/low |
| `json_object` / `json_schema` | 200 | OK；可與 effort 並用 |

---

## D. 建議請求範例

**Kimi：**
```bash
curl https://inner-medusa.genai.nchc.org.tw/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{
    "model": "Kimi-K3",
    "messages": [{"role":"user","content":"..."}],
    "reasoning_effort": "low",
    "response_format": {"type":"json_object"},
    "max_tokens": 4096
  }'
```

**Gemma（結構化輸出；thinking 此端點暫勿依賴）：**
```bash
curl https://inner-medusa.genai.nchc.org.tw/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-4-31B-it",
    "messages": [{"role":"user","content":"..."}],
    "response_format": {"type":"json_schema","json_schema":{"name":"probe","strict":true,"schema":{...}}},
    "max_tokens": 1024
  }'
```

**gpt-oss：**
```bash
curl https://inner-medusa.genai.nchc.org.tw/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss-120b",
    "messages": [{"role":"user","content":"..."}],
    "reasoning_effort": "high",
    "max_tokens": 2048
  }'
```
