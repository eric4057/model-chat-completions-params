# Family 契約

機器可讀契約。`parse`／探測引擎只載入**不以 `_` 開頭、且不是 `*.schema.json`** 的檔。

| 檔 | 角色 |
|----|------|
| `<id>.json` | 一個 family；`match` 對閘道 model id |
| `_nchc_catalog.json` | NCHC `GET /models` 分類清冊（78 chat + 27 非 chat） |
| `profile.schema.json` | Profile JSON schema |

**官方 ≠ 閘道。** `source` 是廠商／NIM 文件。閘道 200 或枚舉較寬，不代表官方有該檔。

NCHC 自有模型沒有外部官網時，`source` 可空；不要捏造出處。`official` scope 若 `params` 為空，就是空結果。

## 有特殊參數

`MODEL` 必須是閘道 id。怎麼跑見 [README.md](../../README.md)。

| id | family | 閘道 id 例子 | 測什麼 | 官方文件 |
|----|--------|----------------|--------|----------|
| `gemma-4` | Google Gemma 4 | `gemma-4-31B-it` | `enable_thinking` 開／關（不是強度檔） | [Thinking](https://ai.google.dev/gemma/docs/capabilities/thinking)、[Model card](https://ai.google.dev/gemma/docs/core/model_card_4)、[Prompt formatting](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4) |
| `kimi-k3` | Moonshot Kimi K3 | `Kimi-K3` | `reasoning_effort`（low／high／max）；不要傳 `thinking`；temperature／top_p 不能改 | [Reasoning effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort)、[Models overview](https://platform.kimi.ai/docs/api/models-overview)、[K3 quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart) |
| `nemotron-3-ultra` | NVIDIA Nemotron 3 Ultra | `NVIDIA-Nemotron-3-Ultra-550B-A55B` | `reasoning_effort`（none／medium／high）、`reasoning_budget` | [NIM Ultra infer](https://docs.api.nvidia.com/nim/re/reference/nvidia-nemotron-3-ultra-550b-a55b-infer) |
| `nemotron-3-super` | NVIDIA Nemotron 3 Super | `NVIDIA-Nemotron-3-Super-120B-A12B` | `reasoning_effort`（none／low／high）、`reasoning_budget` | [NIM Super infer](https://docs.api.nvidia.com/nim/re/reference/nvidia-nemotron-3-super-120b-a12b-infer) |
| `gpt-oss-120b` | OpenAI gpt-oss | `gpt-oss-120b` | `reasoning_effort`（low／medium／high；無 none／max） | [Harmony](https://developers.openai.com/cookbook/articles/openai-harmony)、[GitHub](https://github.com/openai/gpt-oss)、[Model card PDF](https://cdn.openai.com/pdf/419b6906-9da6-406c-a19d-1bb078ac7637/oai_gpt-oss_model_card.pdf) |
| `glm-5` | Zhipu GLM-5 | `GLM-5.2` | `thinking.type` 開／關、`reasoning_effort`（含 none／minimal） | [Thinking](https://docs.z.ai/guides/capabilities/thinking) |
| `minimax-m2` | MiniMax M2.x | `MiniMax-M2.7` | `thinking.type`（enabled／adaptive／disabled；官方：關了仍會想） | [Chat Completions](https://platform.minimax.io/docs/api-reference/text-chat-openai) |
| `minimax-m3` | MiniMax M3 | `MiniMax-M3` | `thinking.type` 開／關、`reasoning_split` | [Chat Completions](https://platform.minimax.io/docs/api-reference/text-chat-openai) |

連結與各檔 JSON 的 `source` 相同。Gemma 4 × Kimi K3 另有人讀對照：[PARAMS.md](../../PARAMS.md)。

## 無特殊參數（只當 match；full 才跑 A–F baseline）

`gemma-3`、`llama-3`、`llama-4-maverick`、`llama-nemotron-super`、`mistral-small`、`mistral-large-3`、`devstral`、`granite`、`foundation-sec`、`phi-instruct`、`phi-4-reasoning`、以及 NCHC／TAIDE：`argus-coder`、`ilinda`、`mene-m3`、`muse-glimmer`、`rainbell`、`sciexplore`、`taide`、`thanos`、`valkyrja-chat`、`voldemort`。
