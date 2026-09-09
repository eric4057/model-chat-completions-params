# `/v1/chat/completions` 參數啟用對照（Gemma 4 / Kimi K3）

人讀出處，範圍**只含 Gemma 4 與 Kimi K3**。其餘 family 的機器可讀契約在 [`configs/models/`](./configs/models/README.md)，不要把本檔當成全閘道清單。

怎麼測見 [README.md](./README.md)、[FLOWCHART.md](./FLOWCHART.md)、[ARCHITECTURE.md](./ARCHITECTURE.md)。閘道實測看本機 `results/*.json`／`*.md`（不進版控）。

**官方 ≠ 閘道。** 下表是廠商契約。經 LiteLLM 轉發時，參數可能被丟棄、枚舉被放寬，或 HTTP 200 但欄位沒生效。

表格欄位順序：**能力／參數 → Gemma 4 → Kimi K3 → 資料來源（最後一欄）**。

---

## 特殊參數總表（Gemma 4 × Kimi K3）

| 能力 / 參數 | Gemma 4 | Kimi K3 | 資料來源 |
|-------------|---------|---------|----------|
| Thinking 預設 | **關**；需顯式開啟 | **永遠開**；無法關閉 | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| 開啟 / 調整 thinking | 官方 **開／關**：`chat_template_kwargs.enable_thinking=true`。vLLM **直連**時頂層 `reasoning_effort` 會**自動注入** enable | 頂層 `reasoning_effort=low`/`high`/`max`（預設 `max`） | [Gemma prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4)、[vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html)、[Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| `reasoning_effort` 取值 | vLLM 注入用：`low`/`medium`/`high`/`none`（`none`＝關；**無官方 `max` 強度檔**） | `low`/`high`/`max`（**無** `none`/`medium`） | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |
| 舊版 `thinking` 物件 | 不適用 | **不要傳**（K2.x）；改用 `reasoning_effort` | [Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |
| Token / template 層 | system 加 `<\|think\|>`；輸出 `<\|channel>thought`…；多輪一般剝掉 thought（tool 回合例外） | Preserved Thinking；多輪須回傳完整 assistant | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[Gemma prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4)、[Kimi thinking](https://platform.kimi.ai/docs/guide/use-thinking-models) |
| 回傳欄位 | vLLM：`reasoning`（舊名 `reasoning_content`）；MaaS／閘道常 `reasoning_content` | `reasoning_content`（與 `content`/`tool_calls` 並存） | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Google MaaS Thinking](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/capabilities/thinking)、[LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content)、[Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| 隱藏 reasoning 欄位 | vLLM：`include_reasoning=false`（仍可生成） | 無「關閉欄位」官方開關 | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |
| `response_format` | vLLM structured：`json`/`regex`。閘道常見 OpenAI 式 `json_object`/`json_schema`（**不同套**） | `json_object` 或 `json_schema`（建議 `strict:true`）；**只 parse `content`** | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Kimi response_format](https://platform.kimi.ai/docs/guide/response_format) |
| Tools | `tools`/`tool_choice`；需 `--tool-call-parser gemma4`、`--enable-auto-tool-choice` | `auto`/`none`/`required`；支援 dynamic `system.tools` | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html)、[Kimi tool_choice](https://platform.kimi.ai/docs/guide/use-tool-choice) |
| Partial 續寫 | — | 最後一則 assistant `partial: true` | [Kimi Partial Mode](https://platform.kimi.ai/docs/guide/use-partial-mode-feature-of-kimi-api) |
| Multimodal | `image_url`/audio 等（原生） | `content` 須為物件陣列；**base64 或 `ms://`**；**不支援公開 URL** | [HF gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it)、[Kimi Vision](https://platform.kimi.ai/docs/guide/use-kimi-vision-model) |
| Sampling | 一般可調（`temperature` 等） | `temperature`/`top_p`/`n`/penalties **固定**；官方**勿傳**其他值（應 4xx） | [Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |
| 長度參數 | 一般 `max_tokens` | 建議 `max_completion_tokens`（預設 131072，上限 1048576） | [Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| 串流 | vLLM 文件：SSE 可有 `reasoning` delta；實際依部署 | `stream:true`；`reasoning_content` 與 `content` 分開 delta | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| vLLM 伺服器旗標 | `--reasoning-parser gemma4`、`--tool-call-parser gemma4`、`--enable-auto-tool-choice`、建議 `--chat-template examples/tool_chat_template_gemma4.jinja`、可選 `--default-chat-template-kwargs` | 以 Moonshot Chat Completions 為準（開源部署另查） | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| LiteLLM / 閘道注意 | `chat_template_kwargs` 可能被丟；effort 自動注入常失效；`allowed_openai_params` 強制透傳可能 500；部分 model group 拒 `reasoning_effort` | `moonshot/kimi-k3` 透傳 `reasoning_effort`（含 `max`）；閘道枚舉可能寬於官方 | [LiteLLM VLLM](https://docs.litellm.ai/docs/providers/vllm)、[LiteLLM PR #33675](https://github.com/BerriAI/litellm/pull/33675)、[LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content) |


---

## 0. 特殊參數一覽

含 reasoning、`response_format`、tool、multimodal、sampling 約束與伺服器旗標。測項見 [ARCHITECTURE.md](./ARCHITECTURE.md)。

### 0.1 Gemma 4 — 請求參數（Chat Completions）

| 參數 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| `chat_template_kwargs.enable_thinking` | `true` / `false`（預設 `false`） | Google 官方 thinking 是 **開／關**，不是 low／high 強度 | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[Gemma 4 prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4)、[HF gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it)、[Google MaaS Thinking](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/capabilities/thinking)、[vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |
| `reasoning_effort` | `low` / `medium` / `high` / `none`（**無官方 `max` 強度檔**） | vLLM **直連**：`low`/`medium`/`high` 自動注入 `enable_thinking=true`；`none` 關閉。這是開關注入，不是可證明的思考深度。經 LiteLLM **不保證**注入 | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |
| `include_reasoning` | `true` / `false` | 仍可生成 thought，但不回傳 reasoning 欄位 | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |
| `skip_special_tokens` | 搭配 thinking 時建議 `false` | 舊版 workaround：避免 thought 分隔 token 被剝掉 | [HF discussion #28](https://huggingface.co/google/gemma-4-31B-it/discussions/28)、[vLLM PR #39027](https://github.com/vllm-project/vllm/pull/39027) |
| `tools` / `tool_choice` | OpenAI tools 格式；`auto` 等 | 需伺服器掛 gemma4 tool parser | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html)、[Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4) |
| `response_format` / structured output | vLLM：`json` / `regex`；閘道常見 `json_object` / `json_schema` | 與 reasoning parser 併用時，結構化約束作用在最終答案 | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |
| 多模態 `messages[].content` | `image_url` / audio 等 | 原生多模態輸入 | [HF gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it)、[vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| 回傳：`reasoning` / `reasoning_content` | 與 `content` 分開 | vLLM 新欄位名偏 `reasoning`；MaaS／閘道常見 `reasoning_content` | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Google MaaS Thinking](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/capabilities/thinking) |
| 回傳：`tool_calls` | OpenAI `tool_calls` | 從 content 解析，不從 reasoning 解析 | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |

### 0.2 Gemma 4 — Model card / Token 層

| 項目 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| `<\|think\|>` | 放在 leading system turn | Token 層觸發 thinking；移除則關閉 | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[Gemma 4 prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4)、[HF gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it) |
| Thought 輸出結構 | `<\|channel>thought` … | 內部推理包在 channel 裡，之後才是最終答案 | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[Gemma 4 prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4) |
| 關閉 thinking 時的空 thought | 除 **E2B／E4B** 外，關閉後仍可能輸出空 thought block | E2B／E4B 行為不同；較大尺寸偶發「關掉仍出 thought channel」 | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[Gemma 4 prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4) |
| 多輪歷史 | 一般只保留最終回答 | tool-call 回合例外，需保留 thinking | [HF gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it) |
| native `system` role | `system` / `user` / `assistant` | Gemma 4 原生支援 system | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4) |
| Function calling 特殊 token | tool_call 等特殊 token | 需對應 tool parser | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |

### 0.3 Gemma 4 — vLLM 伺服器旗標

| 參數 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| `--reasoning-parser gemma4` | 啟動必填（若要 thinking） | 解析 thought → `reasoning` | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| `--tool-call-parser gemma4` | 啟動必填（若要 tools） | 解析 function calls | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| `--enable-auto-tool-choice` | 與 tool parser 併用 | 自動偵測 tool calls | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| `--chat-template examples/tool_chat_template_gemma4.jinja` | 覆蓋預設 template | reasoning + tool 優化模板 | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| `--default-chat-template-kwargs '{"enable_thinking": true}'` | 伺服器預設 | 全站預設開 thinking；請求層可覆寫 | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| `--mm-processor-kwargs '{"max_soft_tokens": N}'` | 預設約 280，最高約 1120 | 視覺 token budget | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| `--limit-mm-per-prompt` | 舊寫 `image=N,audio=M`；新版 recipe 用 JSON `'{"image": N, "audio": M}'` | 每請求多模態上限 | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html)、[vLLM Gemma4 recipe latest](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html) |

### 0.4 Kimi K3 — 請求參數（Chat Completions）

| 參數 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| `reasoning_effort` | `low` / `high` / `max`（預設 `max`） | **永遠 thinking**；只能調強度，不能關 | [Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort)、[Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)、[LiteLLM PR #33675](https://github.com/BerriAI/litellm/pull/33675) |
| ~~`thinking`~~ | K2.x 專用 | **K3 不要傳**；改用 `reasoning_effort` | [Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |
| `response_format` | `json_object` 或 `json_schema`（建議 `strict: true`） | 只 parse `content`，勿 parse `reasoning_content` | [Kimi response_format](https://platform.kimi.ai/docs/guide/response_format) |
| `tools` / `tool_choice` | `auto` / `none` / **`required`** | K3 支援 `required`；K2.x 多不支援 | [Kimi tool_choice](https://platform.kimi.ai/docs/guide/use-tool-choice)、[Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| `max_completion_tokens` | 預設 131072；最高 1048576 | 官方建議用此欄，而非 `max_tokens` | [Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| `temperature` / `top_p` / `n` / `presence_penalty` / `frequency_penalty` | 固定 `1.0` / `0.95` / `1` / `0` / `0` | 官方：**勿傳其他值**（應報錯）。閘道可能仍 200 | [Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview)、[Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| `partial`（assistant message） | 最後一則 assistant 設 `partial: true` | 前綴續寫 | [Kimi Partial Mode](https://platform.kimi.ai/docs/guide/use-partial-mode-feature-of-kimi-api)、[Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| Dynamic tool loading | `system` message 帶完整 `tools`、可無 `content` | 從該訊息起工具生效；歷史需保留 | [Kimi Dynamic Tool Loading](https://platform.kimi.ai/docs/guide/use-dynamic-tool-loading)、[Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| Vision / Video `content` | `content` 必須是物件陣列；影像用 base64 或 `ms://` | **不支援**公開 image URL | [Kimi Vision](https://platform.kimi.ai/docs/guide/use-kimi-vision-model)、[Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| 串流 | `stream: true` | `reasoning_content` 與 `content` 分開的 delta | [Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| 回傳：`reasoning_content` | 與 `content` / `tool_calls` 並存 | 多輪與 tool **必須原樣回傳完整 assistant message** | [Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort)、[LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content) |
| 模型別名（部署側） | 如 `Kimi-K3` / `Kimi-K3-low` / `-high` / `-max` | 別名對應不同 effort；id 依閘道 `GET /models` | 閘道部署，非 Moonshot 官方 id |

### 0.5 LiteLLM / Gateway

| 參數 / 行為 | 值 / 用法 | 說明 | 來源 |
|-------------|-----------|------|------|
| `reasoning_content` 標準化 | 讀 `message.reasoning_content` | 跨 provider 統一欄位 | [LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content) |
| `thinking_blocks` | Anthropic 為主 | OpenAI-compatible client 常丟失，多輪 tool 需注意 | [LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content) |
| `litellm.supports_reasoning(model)` | `True` / `False` | 查模型是否支援 reasoning | [LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content) |
| `drop_params` | `true` | 丟棄上游不支援參數 | [LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content)、[LiteLLM VLLM](https://docs.litellm.ai/docs/providers/vllm) |
| `allowed_openai_params` | 如 `["chat_template_kwargs"]`、`["reasoning_effort"]` | 強制透傳；對 hosted_vllm 可能 500（unexpected keyword）。未允許時可能 `UnsupportedParamsError` | [LiteLLM VLLM](https://docs.litellm.ai/docs/providers/vllm) |
| `hosted_vllm/` provider | OpenAI-compatible server | 閘道常見路徑；能否轉發 `chat_template_kwargs`／`reasoning_effort` 依版本與 model group | [LiteLLM VLLM](https://docs.litellm.ai/docs/providers/vllm) |
| Moonshot `moonshot/kimi-k3` | 透傳 `reasoning_effort`（含 `"max"`） | 排除固定 sampling；多輪回填 `reasoning_content` | [LiteLLM PR #33675](https://github.com/BerriAI/litellm/pull/33675) |

---

## 1. 請求端怎麼開

| 能力 | Gemma 4（官方 / vLLM） | Kimi K3（Moonshot 官方） | 來源 |
|------|------------------------|--------------------------|------|
| 開啟 reasoning / thinking | `chat_template_kwargs.enable_thinking=true`（預設關；官方是開關） | 永遠開啟，無法關閉 | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[Gemma 4 prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4)、[Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| effort 控制 | 頂層 `reasoning_effort=low/medium/high/none`（直連會注入 `enable_thinking`，**不是**可觀測強度） | 頂層 `reasoning_effort=low/high/max`（預設 `max`） | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| 舊版 `thinking` 物件 | 不適用 | 不要用 K2.x 的 `thinking` | [Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |
| 回傳欄位 | vLLM：`message.reasoning`（舊名 `reasoning_content`）；Google MaaS：`reasoning_content` | `message.reasoning_content`（串流為 delta） | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Google MaaS Thinking](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/capabilities/thinking)、[Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| `response_format` | vLLM structured：`json`/`regex`；閘道常見 `json_object`/`json_schema`（不同套） | `json_object` 或 `json_schema`（建議 `strict:true`）；只 parse `content` | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Kimi response_format](https://platform.kimi.ai/docs/guide/response_format) |
| sampling | 一般可調 | 固定；官方**勿傳**其他值（應 4xx）。閘道可能仍 200 | [Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |

---

## 2. Gemma 4

### 2.1 Model card / HF（模型本體）

| 項目 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| Thinking mode | `enable_thinking=True/False` | 內建；官方是 **開／關**（可用 system 指示調深度，但那不是 API 檔位） | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[Gemma 4 prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4)、[Thinking mode in Gemma](https://ai.google.dev/gemma/docs/capabilities/thinking)、[HF gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it) |
| HF 呼叫 | `processor.apply_chat_template(..., enable_thinking=True/False)` | 本體層，不是 HTTP 欄位 | [HF gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it) |
| Token 觸發 | system 開頭 `<\|think\|>` | 輸出以 `<\|channel>thought` 包住 reasoning | [Gemma model card](https://ai.google.dev/gemma/docs/core/model_card_4) |
| 多輪 | 歷史通常只保留最終回答 | tool-call 回合例外，需保留 thinking | [HF gemma-4-31B-it](https://huggingface.co/google/gemma-4-31B-it) |

### 2.2 Google Cloud OpenAI-compatible（MaaS）

| 項目 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| 開啟 thinking | `"chat_template_kwargs": {"enable_thinking": true}` | 預設關閉 | [Google MaaS Thinking](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/capabilities/thinking) |
| 回傳 | `reasoning_content` = thought；`content` = 最終答案 | 與 vLLM 欄位名可能不同 | [Google MaaS Thinking](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/capabilities/thinking) |

```json
{
  "model": "google/gemma-4-26b-a4b-it-maas",
  "messages": [{"role": "user", "content": "Who are you?"}],
  "chat_template_kwargs": {"enable_thinking": true}
}
```

### 2.3 vLLM

| 項目 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| 伺服器旗標 | 見 §0.3 | 要 thinking／tools 時必掛 parser | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| 請求（推薦） | `chat_template_kwargs.enable_thinking=true` | 主開關 | [vLLM Gemma4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| 請求（effort） | `"reasoning_effort": "high"` | 直連才保證自動注入 enable | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |
| `include_reasoning` | `false` | 仍生成 thinking，不回傳欄位 | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |
| 回應欄位名 | `reasoning` 或 `reasoning_content` | 新版文件用 `reasoning` | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |

```bash
vllm serve google/gemma-4-31B-it \
  --reasoning-parser gemma4 \
  --tool-call-parser gemma4 \
  --enable-auto-tool-choice \
  --chat-template examples/tool_chat_template_gemma4.jinja
# 可選：全站預設開 thinking
# --default-chat-template-kwargs '{"enable_thinking": true}'
```

```json
{
  "model": "google/gemma-4-31B-it",
  "messages": [{"role": "user", "content": "..."}],
  "max_tokens": 4096,
  "chat_template_kwargs": {"enable_thinking": true}
}
```

| `reasoning_effort` | 自動效果（vLLM **直連**） | 來源 |
|--------------------|---------------------------|------|
| `low` / `medium` / `high` | 注入 `enable_thinking=true` | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |
| `none` | 注入 `enable_thinking=false` | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |
| 未設定 | 不注入（維持預設關閉） | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/) |

請求裡若已顯式帶 `enable_thinking`，其值優先於自動注入。經 LiteLLM 時上表**不保證**發生。腳本無法用字數證明 Gemma 的 `low`／`medium`／`high` 強度（多半沒有 `reasoning_tokens`）；能判的是會不會想、能不能關。各 id 看 `results/*.json`。

### 2.4 LiteLLM

| 項目 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| 讀取欄位 | `response.choices[0].message.reasoning_content` | 閘道常把 vLLM 的 `reasoning` 改成此名 | [LiteLLM Reasoning](https://docs.litellm.ai/docs/reasoning_content) |
| `hosted_vllm` 轉發 | 依 proxy 版本與允許清單 | `chat_template_kwargs`／`reasoning_effort` 可能被丟或擋 | [LiteLLM VLLM](https://docs.litellm.ai/docs/providers/vllm) |
| OpenAI Python SDK | 可能 `unexpected keyword argument 'chat_template_kwargs'` | 本專案腳本用 `curl`，不踩 SDK 限制 | [LiteLLM VLLM](https://docs.litellm.ai/docs/providers/vllm) |

---

## 3. Kimi K3

### 3.1 Moonshot 官方

| 項目 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| Quickstart | `kimi-k3` | 參數契約來源 | [Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| `reasoning_effort` | `low` / `high` / `max`（預設 `max`） | 永遠 thinking；不能關 | [Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| ~~`thinking`~~ | — | K2.x 專用；K3 應移除 | [Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |
| 固定 sampling | `temperature=1.0`、`top_p=0.95`、`n=1`、penalties `0` | 勿傳其他值 | [Kimi models-overview](https://platform.kimi.ai/docs/api/models-overview) |
| 多輪 / tool | 回傳完整 assistant（含 `reasoning_content`、`tool_calls`） | 必須原樣回填 | [Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| `response_format` | `json_object` 或 `json_schema`（建議 `strict: true`） | 只 parse `content` | [Kimi response_format](https://platform.kimi.ai/docs/guide/response_format) |

```json
{
  "model": "kimi-k3",
  "messages": [{"role": "user", "content": "..."}],
  "reasoning_effort": "high"
}
```

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

### 3.2 LiteLLM（Moonshot）

| 項目 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| 模型 | `moonshot/kimi-k3` | LiteLLM provider id | [LiteLLM PR #33675](https://github.com/BerriAI/litellm/pull/33675) |
| 透傳 | 頂層 `reasoning_effort`（含 `"max"`） | 排除固定 sampling | [LiteLLM PR #33675](https://github.com/BerriAI/litellm/pull/33675) |
| 多輪 | `supports_reasoning` | 回填 `reasoning_content` | [LiteLLM PR #33675](https://github.com/BerriAI/litellm/pull/33675) |

### 3.3 vLLM

截至整理時，Moonshot 官方說明仍在與推理夥伴對齊開源部署細節；**K3 參數契約以 Moonshot Chat Completions 為準**（頂層 `reasoning_effort`，非 Gemma 的 `chat_template_kwargs`）。

---

## 4. 建議呼叫模板

端點不綁定 FQDN；先設環境變數，再依閘道 `GET /models` 的實際 id 呼叫。

```bash
export BASE=https://<your-gateway>/v1
export KEY=...
```

### Kimi K3

```bash
curl "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{
    "model": "Kimi-K3",
    "messages": [{"role":"user","content":"15*37?"}],
    "reasoning_effort": "low",
    "max_completion_tokens": 4096
  }'
```

| 項目 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| 官方檔位 | `low`/`high`/`max` | 預設 `max`；**無** `medium`、**不能** `none` | [Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| 長度 | `max_completion_tokens` | 官方建議；預設 131072，上限 1048576 | [Kimi K3 Quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| 閘道別名 | `Kimi-K3-low` 等 | 依 `/models`，非 Moonshot 官方 id | 閘道部署 |

結構化輸出另加 `response_format`（只 parse `content`）。閘道若接受 `medium`／`none`，不代表官方有該檔。

### Gemma 4

```bash
curl "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-4-31B-it",
    "messages": [{"role":"user","content":"15*37?"}],
    "max_tokens": 4096,
    "chat_template_kwargs": {"enable_thinking": true},
    "reasoning_effort": "low"
  }'
```

| 項目 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| 官方／vLLM effort | `low` / `medium` / `high` / `none` | 用來注入 enable；**無** Kimi 式 `max` 強度檔。官方 thinking 仍是開關 | [vLLM Reasoning](https://docs.vllm.ai/en/latest/features/reasoning_outputs/)、[Gemma 4 prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4) |
| 經閘道 | 建議 `enable_thinking` 與 `reasoning_effort` 都送 | 自動注入不保證 | [LiteLLM VLLM](https://docs.litellm.ai/docs/providers/vllm) |
| 串流 | `stream: true` | 部分部署 SSE 看不到 reasoning 欄位 | 閘道實測：`results/*.json` |

---

## 5. 怎麼測

| 項目 | 值 / 用法 | 說明 | 來源 |
|------|-----------|------|------|
| 流程與判定 | A→F | `probe_flowchart.py` | [ARCHITECTURE.md](./ARCHITECTURE.md)、[README.md](./README.md) |
| 讀結果 | `results/flowchart_*.json` | `meta.read`、`request`、`compare` | 本機 `results/`（不進版控） |
| `request.reasoning_effort` | 實際 POST | 只證明有沒有送到閘道 | 腳本 JSON |
| `compare.vs_default` | `like_default` / `not_default` | 像不像預設；**不能**證明就是官方獨立檔。`intensity_testable=false` 時不要當 Gemma 強度結論 | [ARCHITECTURE.md](./ARCHITECTURE.md) |
| Gemma 矩陣 | `ENABLE_THINKING` × `STREAM_STEPS` | 觀測 reasoning 常需 `enable_thinking=true` 且非串流；E4B 在非串流下只送 effort 也可能開 thinking | 閘道實測：`results/*.json` |
