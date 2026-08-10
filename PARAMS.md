# `/v1/chat/completions` 參數啟用對照（Gemma 4 / Kimi K3）

整理日期：2026-08-10。以**官方文件**為準；實測端點 `https://inner-medusa.genai.nchc.org.tw/v1`，見 [RESULTS.md](./RESULTS.md)。

## 1. 總表（請求端怎麼開）

| 能力 | Gemma 4（官方 / vLLM） | Kimi K3（Moonshot 官方） |
|------|------------------------|--------------------------|
| 開啟 reasoning / thinking | `chat_template_kwargs.enable_thinking=true`（預設關） | **永遠開啟**，無法關閉 |
| effort 控制 | vLLM：頂層 `reasoning_effort`=`low`/`medium`/`high`/`none`（會自動注入 `enable_thinking`） | 頂層 `reasoning_effort`=`low`/`high`/`max`（預設 `max`） |
| 舊版 thinking 物件 | 不適用 | **不要用** K2.x 的 `thinking`；改用 `reasoning_effort` |
| 回傳欄位 | vLLM：`message.reasoning`（舊名 `reasoning_content`）；Google MaaS：`reasoning_content` | `message.reasoning_content`（串流為 delta） |
| `response_format` | vLLM structured output：`json` / `regex`（搭配 reasoning parser） | `json_object` 或 `json_schema`（建議 `strict:true`）；只 parse `content` |
| sampling | 一般可調 | `temperature`/`top_p`/`n`/penalties **固定**，勿傳 |

---

## 2. Gemma 4

### 2.1 Model card / HF（模型本體）

來源：

- [Gemma 4 model card (Google AI)](https://ai.google.dev/gemma/docs/core/model_card_4)
- [google/gemma-4-31B-it (Hugging Face)](https://huggingface.co/google/gemma-4-31B-it)

重點：

- 內建 Thinking mode；以 chat template 的 `enable_thinking` 控制。
- HF 範例：`processor.apply_chat_template(..., enable_thinking=True/False)`。
- Token 層：system 開頭加 `<|think|>` 觸發；輸出以 `<|channel>thought\n` … `<channel|>` 包住 reasoning。
- 多輪：歷史通常只保留最終回答（tool-call 回合例外，需保留 thinking）。

### 2.2 Google Cloud OpenAI-compatible（MaaS）

來源：[Thinking for open models](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/capabilities/thinking)

```json
{
  "model": "google/gemma-4-26b-a4b-it-maas",
  "messages": [{"role": "user", "content": "Who are you?"}],
  "chat_template_kwargs": {"enable_thinking": true}
}
```

- 預設：**關閉** reasoning。
- 開啟後：`reasoning_content` = thought，`content` = 最終答案。

### 2.3 vLLM

來源：

- [Reasoning Outputs](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)
- [Gemma 4 Usage Guide (vLLM recipes)](https://github.com/vllm-project/recipes/blob/main/Google/Gemma4.md)

**伺服器：**

```bash
vllm serve google/gemma-4-31B-it \
  --reasoning-parser gemma4 \
  --tool-call-parser gemma4 \
  --enable-auto-tool-choice \
  --chat-template examples/tool_chat_template_gemma4.jinja
# 可選：全站預設開 thinking
# --default-chat-template-kwargs '{"enable_thinking": true}'
```

**請求（推薦）：**

```json
{
  "model": "google/gemma-4-31B-it",
  "messages": [{"role": "user", "content": "..."}],
  "max_tokens": 4096,
  "chat_template_kwargs": {"enable_thinking": true}
}
```

或：

```json
{
  "reasoning_effort": "high"
}
```

vLLM 行為：

| `reasoning_effort` | 自動效果 |
|--------------------|----------|
| `low` / `medium` / `high` | `enable_thinking=true` |
| `none` | `enable_thinking=false` |
| 未設定 | 不注入（維持預設關閉） |

其他：

- `include_reasoning: false`：仍生成 thinking，但不回傳 reasoning 欄位。
- 舊版若 parser 吃不到 delimiters，社群曾需 `skip_special_tokens: false`（新版 parser 應自動處理）。
- 回應欄位名：新版文件用 `reasoning`；部分部署仍回 `reasoning_content`。

### 2.4 LiteLLM

來源：[Thinking / Reasoning Content](https://docs.litellm.ai/docs/reasoning_content)、[VLLM provider](https://docs.litellm.ai/docs/providers/vllm)

- 統一讀取：`response.choices[0].message.reasoning_content`。
- 對 `hosted_vllm`：**能否把 `chat_template_kwargs` / `reasoning_effort` 轉發到上游，取決於 proxy 版本與參數允許清單**。
- 若強制 `allowed_openai_params` 卻用 OpenAI Python client 打上游，可能出現  
  `unexpected keyword argument 'chat_template_kwargs'`（見 inner-medusa 實測）。

---

## 3. Kimi K3

### 3.1 Moonshot 官方（參數契約來源）

來源：

- [Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)
- [Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort)
- [Model Parameter Reference](https://platform.kimi.ai/docs/api/models-overview)
- [response_format / Structured Output](https://platform.kimi.ai/docs/guide/response_format)

**Reasoning：**

```json
{
  "model": "kimi-k3",
  "messages": [{"role": "user", "content": "..."}],
  "reasoning_effort": "high"
}
```

| 參數 | 值 | 說明 |
|------|----|------|
| `reasoning_effort` | `low` / `high` / `max` | 預設 `max`；K3 **永遠 thinking** |
| `thinking` | — | K2.x 專用；遷到 K3 應移除 |

固定參數（勿傳其他值）：`temperature=1.0`、`top_p=0.95`、`n=1`、`presence_penalty=0`、`frequency_penalty=0`。

多輪 / tool：必須把完整 assistant message（含 `reasoning_content`、`tool_calls`）原樣回傳。

**response_format：**

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "person",
      "strict": true,
      "schema": { "...": "..." }
    }
  }
}
```

- 支援 `json_object` 與 `json_schema`。
- **只**對 `message.content` 做 `json.loads`，不要 parse `reasoning_content`。

### 3.2 LiteLLM（Moonshot）

來源：LiteLLM PR [feat(moonshot): add Kimi K3 support](https://github.com/BerriAI/litellm/pull/33675)

- 模型：`moonshot/kimi-k3`
- 透傳頂層 `reasoning_effort`（含 `"max"`）
- 會依 registry 排除固定 sampling 參數
- `supports_reasoning` 用於多輪回填 `reasoning_content`

### 3.3 vLLM

截至整理時，Moonshot 官方說明仍在與推理夥伴對齊開源部署細節；**K3 參數契約以 Moonshot Chat Completions 為準**（頂層 `reasoning_effort`，非 Gemma 的 `chat_template_kwargs`）。

---

## 4. 建議呼叫模板（inner-medusa）

實測 / 呼叫端點一律：

```bash
export BASE=https://inner-medusa.genai.nchc.org.tw/v1
export KEY=...   # API Key
```

此端點可見模型名：`Kimi-K3`、`Kimi-K3-low`、`Kimi-K3-high`、`Kimi-K3-max`、`gemma-4-31B-it`、`gpt-oss-120b` 等。

### Kimi K3

```bash
curl "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{
    "model": "Kimi-K3",
    "messages": [{"role":"user","content":"15*37?"}],
    "reasoning_effort": "low",
    "response_format": {"type":"json_object"},
    "max_tokens": 4096
  }'
```

或直接用別名：`Kimi-K3-low` / `Kimi-K3-high` / `Kimi-K3-max`。

### Gemma 4

```bash
curl "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-4-31B-it",
    "messages": [{"role":"user","content":"15*37?"}],
    "max_tokens": 4096,
    "chat_template_kwargs": {"enable_thinking": true}
  }'
```

> 官方寫法如上；**inner-medusa 實測目前 thinking 未生效**（見 RESULTS.md）。結構化輸出請用 `response_format`。

### gpt-oss-120b

```bash
curl "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss-120b",
    "messages": [{"role":"user","content":"15*37?"}],
    "reasoning_effort": "high",
    "max_tokens": 2048
  }'
```

---

## 5. 測試項目清單

見 [TESTPLAN.md](./TESTPLAN.md)；腳本：`scripts/probe_chat_completions.py`。
