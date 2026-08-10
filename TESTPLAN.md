# 測試項目清單

端點一律：`https://inner-medusa.genai.nchc.org.tw/v1`

目標：驗證 `POST /v1/chat/completions` 上 `response_format` / reasoning 是否被接受、是否真的生效。

## A. response_format

| ID | 請求 | 通過條件 |
|----|------|----------|
| rf_none_json_prompt | 無 format，prompt 要求 JSON | HTTP 200；可選 `json.loads` |
| rf_json_object | `{"type":"json_object"}` | HTTP 200 + 合法 JSON |
| rf_json_schema | `{"type":"json_schema", strict schema}` | HTTP 200 + 合法 JSON 且欄位符合 |
| rf_string_json | `"response_format":"json"`（非標準） | 預期失敗或被拒 |

## B. Gemma reasoning

| ID | 請求 | 通過條件 |
|----|------|----------|
| gemma_enable_thinking_true | `chat_template_kwargs.enable_thinking=true` | HTTP 200；有 `reasoning` 或 `reasoning_content` |
| gemma_enable_thinking_false | `enable_thinking=false` | HTTP 200；無 reasoning 欄位 |
| gemma_enable_thinking_skip_false | + `skip_special_tokens=false` | thought/content 分離正確 |
| gemma_reasoning_effort_high | `reasoning_effort=high` | HTTP 200；有 reasoning |
| gemma_reasoning_effort_none | `reasoning_effort=none` | HTTP 200；無 reasoning |
| gemma_include_reasoning_false | thinking on + `include_reasoning=false` | content 有答案、無 reasoning 欄位 |

## C. Kimi reasoning

| ID | 請求 | 通過條件 |
|----|------|----------|
| kimi_reasoning_effort_low | `reasoning_effort=low` | 200；有 `reasoning_content` |
| kimi_reasoning_effort_high / max | `high` / `max` | 200；有 `reasoning_content` |
| kimi_alias_low_high_max | 模型名 `Kimi-K3-low/high/max` | 200；有 `reasoning_content` |
| kimi_thinking_enabled | `thinking={"type":"enabled"}` | K3 不應依賴；此端點預期 400 |

## D. 組合 / Gateway

| ID | 請求 | 觀察點 |
|----|------|--------|
| combo_json_schema_thinking | schema + thinking/effort | content 合法 JSON；reasoning 另欄（若支援） |
| allowed_openai_params | 強制透傳 `chat_template_kwargs` | 是否 500 |

## 執行

```bash
export OPENAI_BASE_URL=https://inner-medusa.genai.nchc.org.tw/v1
export OPENAI_API_KEY=...
export MODELS=Kimi-K3-max,Kimi-K3-low,gemma-4-31B-it,gpt-oss-120b
export MAX_TOKENS=2048
python3 scripts/probe_chat_completions.py
```
