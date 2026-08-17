# `/v1/chat/completions` 參數啟用對照（Gemma 4 / Kimi K3）

整理日期：2026-08-12。以 **model card / LiteLLM / vLLM / Moonshot** 官方文件為準。怎麼測見 [ARCHITECTURE.md](./ARCHITECTURE.md)、[README.md](./README.md)；實測見 [RESULTS.md](./RESULTS.md)。

## 對照總表（Gemma 4 × Kimi K3）

| 能力 / 參數 | Gemma 4 | Kimi K3 | 主要來源 |
|-------------|---------|---------|----------|
| Thinking 預設 | **關**；需顯式開啟 | **永遠開**；無法關閉 | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| 開啟 / 調整 thinking | `chat_template_kwargs.enable_thinking=true`；或頂層 `reasoning_effort`（vLLM 會自動注入 enable） | 頂層 `reasoning_effort`=`low`/`high`/`max`（預設 `max`） | 同上；[vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| `reasoning_effort` 取值 | vLLM：`low` / `medium` / `high` / `none`（`none`=關） | `low` / `high` / `max`（**無** `none`） | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |
| 舊版 `thinking` 物件 | 不適用 | **不要傳**（K2.x）；改用 `reasoning_effort` | [Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |
| Token / template 層 | system 加 `<\|think\|>`；輸出 `<\|channel>thought`… | Preserved Thinking；多輪須回傳完整 assistant | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[Kimi thinking](https://platform.kimi.ai/docs/guide/use-thinking-models) |
| 回傳欄位 | vLLM：`reasoning`（或 `reasoning_content`）；閘道常統一成 `reasoning_content` | `reasoning_content`（與 `content` / `tool_calls` 並存） | [vLLM](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content)、[Kimi](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| 隱藏 reasoning 欄位 | vLLM：`include_reasoning=false`（仍可生成） | 無對應「關閉欄位」官方開關 | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |
| `response_format` | vLLM structured：`json` / `regex`（約束最終答案） | `json_object` 或 `json_schema`（建議 `strict:true`）；只 parse `content` | [vLLM](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Kimi response_format](https://platform.kimi.ai/docs/guide/response_format) |
| Tools | `tools` / `tool_choice`；需 `--tool-call-parser gemma4` | `auto` / `none` / **`required`**；支援 dynamic `system.tools` | [vLLM Gemma4](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html)、[Kimi tool_choice](https://platform.kimi.ai/docs/guide/use-tool-choice) |
| Partial 續寫 | — | 最後一則 assistant `partial: true` | [Kimi Partial Mode](https://platform.kimi.ai/docs/guide/use-partial-mode-feature-of-kimi-api) |
| Multimodal | `image_url` / audio 等（原生） | content 用物件陣列；**base64 或 `ms://`**，不支援公開 URL | [HF gemma-4](https://huggingface.co/google/gemma-4-31B-it)、[Kimi Vision](https://platform.kimi.ai/docs/guide/use-kimi-vision-model) |
| Sampling | 一般可調（`temperature` 等） | `temperature`/`top_p`/`n`/penalties **固定**，勿改 | [Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |
| 長度參數 | 一般 `max_tokens` | 建議 `max_completion_tokens`（預設 131072） | [Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| vLLM 伺服器旗標 | `--reasoning-parser gemma4`、`--tool-call-parser gemma4`、`--enable-auto-tool-choice`、可選 `--default-chat-template-kwargs` | 以 Moonshot Chat Completions 契約為準（開源部署細節另查） | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| LiteLLM / 閘道注意 | `chat_template_kwargs` 可能被丟棄或需 `allowed_openai_params`；強制透傳可能 500 | `moonshot/kimi-k3` 透傳 `reasoning_effort`（含 `max`）；多輪回填 `reasoning_content` | [LiteLLM VLLM](https://docs.litellm.ai/docs/providers/vllm)、[LiteLLM PR #33675](https://github.com/BerriAI/litellm/pull/33675)、[LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content) |

> 細節分表見下方 §0；閘道實測見 [RESULTS.md](./RESULTS.md)。

---

## 0. 特殊參數一覽（完整：model card / LiteLLM / vLLM）

> 範圍：Gemma 4、Kimi K3。含 reasoning、`response_format`、tool、multimodal、sampling 約束與伺服器旗標。  
> 實測結論見 [RESULTS.md](./RESULTS.md)；測項見 [ARCHITECTURE.md](./ARCHITECTURE.md)。

### 0.1 Gemma 4 — 請求參數（Chat Completions）

| 參數 | 來源 | 值 / 用法 | 說明 |
|------|------|-----------|------|
| `chat_template_kwargs.enable_thinking` | [model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[HF](https://huggingface.co/google/gemma-4-31B-it)、[Google MaaS](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/capabilities/thinking)、[vLLM](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) | `true` / `false`（預設 `false`） | 開/關 thinking 的主開關 |
| `reasoning_effort` | [vLLM Reasoning Outputs](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) | `low` / `medium` / `high` / `none` | `low\|medium\|high` 自動注入 `enable_thinking=true`；`none` 關閉 |
| `include_reasoning` | [vLLM Reasoning Outputs](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) | `true` / `false` | 仍可生成 thought，但不回傳 reasoning 欄位 |
| `skip_special_tokens` | [HF discussion](https://huggingface.co/google/gemma-4-31B-it/discussions/28)、[vLLM PR #39027](https://github.com/vllm-project/vllm/pull/39027) | 搭配 thinking 時建議 `false` | 舊版 workaround：避免 thought 分隔 token 被剝掉 |
| `tools` / `tool_choice` | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html)、[model card](https://ai.google.dev/gemma/docs/core/model_card_4) | OpenAI tools 格式；`auto` 等 | 需伺服器掛 gemma4 tool parser |
| `response_format` / structured output | [vLLM Reasoning Outputs](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)（Gemma4 支援 `json`/`regex`） | guided decoding / structured | 與 reasoning parser 併用時，結構化約束作用在最終答案 |
| 多模態 `messages[].content` | [HF](https://huggingface.co/google/gemma-4-31B-it)、[vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) | `image_url` / audio 等 | 原生多模態輸入 |
| 回傳：`reasoning` / `reasoning_content` | [vLLM](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Google MaaS](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/capabilities/thinking) | 與 `content` 分開 | vLLM 新欄位名偏 `reasoning`；MaaS 常見 `reasoning_content` |
| 回傳：`tool_calls` | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) | OpenAI tool_calls | 從 content 解析，不從 reasoning 解析 |

### 0.2 Gemma 4 — Model card / Token 層

| 項目 | 來源 | 值 / 用法 | 說明 |
|------|------|-----------|------|
| `<|think|>` | [model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[HF](https://huggingface.co/google/gemma-4-31B-it) | 放在 leading system turn | Token 層觸發 thinking；移除則關閉 |
| Thought 輸出結構 | [model card](https://ai.google.dev/gemma/docs/core/model_card_4) | `<|channel>thought\n` … `<channel|>` | 內部推理包在 channel 裡，之後才是最終答案 |
| 關閉 thinking 時的空 thought | [model card](https://ai.google.dev/gemma/docs/core/model_card_4) | 多數尺寸仍可能輸出空 thought block | E2B/E4B 行為可能不同 |
| 多輪歷史 | [HF](https://huggingface.co/google/gemma-4-31B-it) | 一般只保留最終回答 | tool-call 回合例外，需保留 thinking |
| native `system` role | [model card](https://ai.google.dev/gemma/docs/core/model_card_4) | `system` / `user` / `assistant` | Gemma 4 原生支援 system |
| Function calling 特殊 token | [model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) | `<|tool_call>` 等 | 需對應 tool parser |

### 0.3 Gemma 4 — vLLM 伺服器旗標

| 參數 | 來源 | 值 / 用法 | 說明 |
|------|------|-----------|------|
| `--reasoning-parser gemma4` | [vLLM](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) | 啟動必填（若要 thinking） | 解析 thought → `reasoning` |
| `--tool-call-parser gemma4` | [recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) | 啟動必填（若要 tools） | 解析 function calls |
| `--enable-auto-tool-choice` | [recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) | 與 tool parser 併用 | 自動偵測 tool calls |
| `--chat-template examples/tool_chat_template_gemma4.jinja` | [recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) | 覆蓋預設 template | reasoning + tool 優化模板 |
| `--default-chat-template-kwargs '{"enable_thinking": true}'` | [vLLM](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) | 伺服器預設 | 全站預設開 thinking；請求層可覆寫 |
| `--mm-processor-kwargs '{"max_soft_tokens": N}'` | [recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) | 預設約 280，最高約 1120 | 視覺 token budget |
| `--limit-mm-per-prompt` | [recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) | 如 `image=N,audio=M` | 每請求多模態上限 |

### 0.4 Kimi K3 — 請求參數（Chat Completions）

| 參數 | 來源 | 值 / 用法 | 說明 |
|------|------|-----------|------|
| `reasoning_effort` | [Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort)、[K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)、[LiteLLM PR](https://github.com/BerriAI/litellm/pull/33675) | `low` / `high` / `max`（預設 `max`） | **永遠 thinking**；只能調強度，不能關 |
| ~~`thinking`~~ | [Model Parameter Reference](https://platform.kimi.ai/docs/api/models-overview) | K2.x 專用 | **K3 不要傳**；改用 `reasoning_effort` |
| `response_format` | [response_format](https://platform.kimi.ai/docs/guide/response_format) | `json_object` 或 `json_schema`（建議 `strict: true`） | 只 parse `content`，勿 parse `reasoning_content` |
| `tools` / `tool_choice` | [Tool Choice](https://platform.kimi.ai/docs/guide/use-tool-choice)、[K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) | `auto` / `none` / **`required`** | K3 支援 `required`；K2.x 多不支援 |
| `max_completion_tokens` | [K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) | 預設 131072；最高 1048576 | 與 rate limit / 長推理相關 |
| `temperature` / `top_p` / `n` / `presence_penalty` / `frequency_penalty` | [Model Parameter Reference](https://platform.kimi.ai/docs/api/models-overview)、[K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) | 固定 `1.0` / `0.95` / `1` / `0` / `0` | **勿傳其他值**（會錯） |
| `partial`（assistant message） | [Partial Mode](https://platform.kimi.ai/docs/guide/use-partial-mode-feature-of-kimi-api)、[K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) | 最後一則 assistant 設 `partial: true` | 前綴續寫 |
| Dynamic tool loading | [Dynamic Tool Loading](https://platform.kimi.ai/docs/guide/use-dynamic-tool-loading)、[K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) | `system` message 帶完整 `tools`、可無 `content` | 從該訊息起工具生效；歷史需保留 |
| Vision / Video `content` | [Vision](https://platform.kimi.ai/docs/guide/use-kimi-vision-model)、[K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) | `content` 必須是物件陣列；影像用 base64 或 `ms://` | **不支援**公開 image URL |
| 串流 | [K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) | `stream: true` | `reasoning_content` 與 `content` 分開的 delta |
| 回傳：`reasoning_content` | [Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort)、[LiteLLM](https://docs.litellm.ai/docs/reasoning_content) | 與 `content` / `tool_calls` 並存 | 多輪與 tool **必須原樣回傳完整 assistant message** |
| 模型別名（部署側） | 實測 | 如 `Kimi-K3` / `Kimi-K3-low` / `-high` / `-max` | 別名對應不同 effort；id 依閘道 `/models` |

### 0.5 LiteLLM / Gateway 相關

| 參數 / 行為 | 來源 | 值 / 用法 | 說明 |
|-------------|------|-----------|------|
| `reasoning_content` 標準化 | [LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content) | 讀 `message.reasoning_content` | 跨 provider 統一欄位 |
| `thinking_blocks` | [LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content) | Anthropic 為主 | OpenAI-compatible client 常丟失，多輪 tool 需注意 |
| `litellm.supports_reasoning(model)` | [LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content) | `True`/`False` | 查模型是否支援 reasoning |
| `drop_params` | [LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content)、[VLLM provider](https://docs.litellm.ai/docs/providers/vllm) | `true` | 丟棄上游不支援參數 |
| `allowed_openai_params` | 實測 / LiteLLM proxy | 如 `["chat_template_kwargs"]` | 強制透傳；對 hosted_vllm 可能 500（unexpected keyword） |
| `hosted_vllm/` provider | [LiteLLM VLLM](https://docs.litellm.ai/docs/providers/vllm) | OpenAI-compatible server | 閘道常見路徑；能否轉發 `chat_template_kwargs` 依版本 |
| Moonshot `moonshot/kimi-k3` | [LiteLLM PR #33675](https://github.com/BerriAI/litellm/pull/33675) | 透傳 `reasoning_effort`（含 `max`） | 排除固定 sampling；多輪回填 `reasoning_content` |

---

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
  `unexpected keyword argument 'chat_template_kwargs'`（見部分閘道實測）。

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

## 4. 建議呼叫模板

端點不綁定 FQDN；先設環境變數，再依閘道 `/models` 的實際 id 呼叫：

```bash
export BASE=https://<your-gateway>/v1   # e.g. inner-medusa 或 RAP portal
export KEY=...   # API Key
```

模型名依閘道而異（示例：`Kimi-K3` / `Kimi-K3-low` / `-high` / `-max`、`gemma-4-31B-it`）。

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

> 官方寫法如上；部分閘道上 thinking 可能未生效（見 RESULTS.md）。結構化輸出請用 `response_format`。

---

## 5. 怎麼測

見 [ARCHITECTURE.md](./ARCHITECTURE.md)、[README.md](./README.md)。腳本：`scripts/probe_flowchart.py`。
