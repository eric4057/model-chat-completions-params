# model-chat-completions-params

對 OpenAI-compatible 閘道做**單模型官方參數檢測**：該模型官網列出的特殊參數在 `POST /v1/chat/completions` 是否真能用，回覆是否符合**官方文件寫死的欄位／行為**（不是只回 200，也不是範例作文全文）。

```text
.
├── README.md                 怎麼跑
├── FLOWCHART.md              流程圖
├── ARCHITECTURE.md           A–F 逐步判定
├── PARAMS.md                 Gemma 4 × Kimi K3 人讀出處
├── env.example               複製成 .env
├── configs/models/           family 契約 JSON（加檔即可測）
├── configs/profiles/         full 考卷三句 prompt
├── probe/                    match / 出題 / 判定 / 對照表
├── scripts/probe_flowchart.py
└── results/                  本機 JSON + .md（不進版控）
```

官方契約在 `configs/models/*.json`（引擎只認 `params[].kind`）。有模型商／NIM 文件就以官方為準，連結寫在各檔 `source`。完整 family 清單見 [`configs/models/README.md`](./configs/models/README.md)。

**有官方特殊參數、預設 `official` 會開槍的 8 家**（`MODEL` 用閘道 `GET /models` 的 id）：

| family | 閘道 id 例子 | 測什麼 | 官方文件 |
|--------|----------------|--------|----------|
| gemma-4 | `gemma-4-31B-it` | `enable_thinking` 開／關 | [Thinking](https://ai.google.dev/gemma/docs/capabilities/thinking) |
| kimi-k3 | `Kimi-K3` | `reasoning_effort`；禁 `thinking`；temperature／top_p 不能改 | [Reasoning effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort) |
| minimax-m3 | `MiniMax-M3` | `thinking.type`、`reasoning_split` | [Chat Completions](https://platform.minimax.io/docs/api-reference/text-chat-openai) |
| minimax-m2 | `MiniMax-M2.7` | `thinking.type`（官方：關了仍會想） | [Chat Completions](https://platform.minimax.io/docs/api-reference/text-chat-openai) |
| nemotron-3-ultra | `NVIDIA-Nemotron-3-Ultra-550B-A55B` | `reasoning_effort`、`reasoning_budget` | [NIM Ultra](https://docs.api.nvidia.com/nim/re/reference/nvidia-nemotron-3-ultra-550b-a55b-infer) |
| nemotron-3-super | `NVIDIA-Nemotron-3-Super-120B-A12B` | 同上（合法檔不同） | [NIM Super](https://docs.api.nvidia.com/nim/re/reference/nvidia-nemotron-3-super-120b-a12b-infer) |
| gpt-oss-120b | `gpt-oss-120b`、`gpt-oss-20b` | `reasoning_effort` | [Harmony](https://developers.openai.com/cookbook/articles/openai-harmony) |
| glm-5 | `GLM-5.2` | `thinking.type`、`reasoning_effort` | [Thinking](https://docs.z.ai/guides/capabilities/thinking) |

`.env` 設好 `MODEL` 後直接跑（不要設 `PROBE_SCOPE`，或設成 `official`）：

```bash
python3 -u scripts/probe_flowchart.py
```

其餘 family 只有 match、沒有特殊參數：`official` 是空結果。人讀出處目前只有 Gemma 4 × Kimi K3，見 [PARAMS.md](./PARAMS.md)。

`PROBE_SCOPE=official`（預設）：只測特殊參數＋文件寫死的回覆欄位。  
`PROBE_SCOPE=full`：再加 A–F 通用考卷（JSON／tools／stream／思考×串流組合）。

NCHC 閘道 `GET /models` 分類：`configs/models/_nchc_catalog.json`（`_` 開頭，不會當 Profile 載入）。78 個 chat id 對到 29 個 family；27 個 embedding／ASR／TTS／圖像 id 不建 chat 探測檔。

## 跑一次

```bash
cp env.example .env
# 必填：OPENAI_BASE_URL、OPENAI_API_KEY、MODEL（閘道 GET /models 的 id）
python3 -u scripts/probe_flowchart.py
```

full 考卷：

```bash
PROBE_SCOPE=full python3 -u scripts/probe_flowchart.py
```

已 `export` 的同名變數會蓋過 `.env`：

```bash
unset MODEL OPENAI_BASE_URL OPENAI_API_KEY REASONING_EFFORT ENABLE_THINKING STREAM_STEPS PROBE_MODE PROBE_SCOPE
```

## 環境變數

| 變數 | 必填 | 說明 |
|------|------|------|
| `OPENAI_BASE_URL` | ✅ | 閘道 `/v1` 根網址 |
| `OPENAI_API_KEY` | ✅ | API 金鑰 |
| `MODEL` | ✅ | `GET /models` 上的 id |
| `PROBE_SCOPE` | — | `official`（預設）或 `full` |
| `REASONING_EFFORT` | — | 逗號分隔；空白＝該 Profile 官方枚舉 |
| `STREAM_STEPS` | — | `true`＝全部串流；`false`＝全關；不設＝僅 D/F 串流。official 契約槍一律非串流 |
| `PROBE_MODE` | — | `probe`（預設）打閘道；`enforce`＝官方不允許就本機 400 |
| `ENABLE_THINKING` | — | **已忽略**。Gemma 由 Profile `flag` 產生獨立對照槍 |

## 結果

跑完寫一對同名檔（`results/` 不進版控；沒有 API key、也沒有官方範例全文）：

```text
results/flowchart_<閘道MODEL>_<時間>.md
results/flowchart_<閘道MODEL>_<時間>.json
```

終端會即時印 `[OK]/[FAIL] case_id HTTP …`，全部打完才寫檔。已有 JSON 可重編表：

```bash
PYTHONPATH=. python3 -m probe.report_table
```

`official` 只有這家特殊參數（A）＋鎖定值（E）；沒有契約就是空表。`full` 再加 B–F（含 `F.think_*` 思考×串流）。

### `.md` 三欄

表頭：命中的 family、`PROBE_SCOPE`、閘道網址、可用／不可用筆數。

| 欄 | 內容 |
|----|------|
| **官方輸出** | 這槍應該怎樣：題號、送了哪個參數、文件要求的 HTTP／欄位（content、reasoning 有或沒有） |
| **endpoint 輸出** | 閘道實際怎樣：HTTP、有無 reasoning、content／reasoning 預覽、實際送出的 extra |
| **輸出錯誤** | 兩欄對不上才寫；通過是 `—` |

第三欄對應判定：`—`＝OK；`FAIL`＝缺欄、不該出現的欄出現了、該 4xx 卻 200、或 5xx；`NOOP`＝接受但證明不了生效；`SKIP`＝閘道 4xx 不收。

不比範例作文全文，也不用思考量排名當官方證明。

### `.json` 結構

| 路徑 | 內容 |
|------|------|
| `meta.base_url` / `model_profile` / `probe_scope` | 閘道、命中的 family、official 或 full |
| `meta.contract` | 這家契約摘要（`source` 官網連結、長度欄、能否關 thinking） |
| `meta.steps` | 實際跑了哪些步驟（official＝`A,E`；full＝`A`–`F`） |
| `meta.counts` | 可用／不可用／接受但未生效／跳過 |
| `meta.read` | 與下表相同的欄位說明（讀檔不必再翻文件） |
| `models.<MODEL>.results[]` | 每一槍一筆 |

### 每一槍（`results[]`）

| 欄位 | 內容 |
|------|------|
| `step` / `case_id` / `desc` | 步驟、題號、說明（例如 `A.type_disabled`） |
| `verdict` | `pass`／`fail`／`noop`／`skip`（表上對應 OK／FAIL／NOOP／SKIP） |
| `http_status` / `elapsed_ms` | HTTP 與耗時 |
| `has_reasoning` / `has_tool_calls` / `json_ok` | 有沒有思考欄、工具呼叫、content 能否當 JSON |
| `content_preview` / `reasoning_preview` | 答案／思考**節錄**，不是全文 |
| `error_preview` | 非 200 時的錯誤訊息 |
| `notes` | 判定補充（例如「不應出現 reasoning_content」） |
| `request` | 實際 POST（不含完整 messages）。有 `reasoning_effort` 只證明有送到閘道 |
| `local_reject` | `true`＝enforce 本機 400，沒打閘道 |
| `contract` | 用了哪個參數。`in_official=false`＝官方沒有此值。`finding=gateway_wider_than_official`＝閘道比官方寬 |
| `official` | 文件寫死的回覆欄位（見下） |
| `compare` | 僅 `full` 的 `A.effort_*` 強度槍；**不是**官方格式證明 |
| `usage` / `finish_reason` | token 用量、結束原因 |

### `official`（文件寫死的那層）

出現在 official 的 effect 槍、`A.official_*`、以及 `F.think_*`。

| 欄位 | 內容 |
|------|------|
| `source` / `docs_quote` | 官網 URL、文件一句話 |
| `request_extra` | 這槍送的特殊參數 |
| `shape` | 各欄 `expected`（非空／不該出現）與 `ok`；閘道若回 `reasoning` 可當 `reasoning_content` 別名 |
| `matches_example` | `true`＝欄位有／無符合文件（不是全文標準答案） |
| `proof` | `response_shape`＝只證明欄位；`unexpected_field`＝不該出現卻出現；`http_mismatch`／`shape_mismatch` |
| `not_proven` | 模型輸出全文；也不是 `reasoning_tokens` 檔位排名 |

### `compare`（思考量，僅 full）

| 欄位 | 內容 |
|------|------|
| `metric` | 優先 `usage.completion_tokens_details.reasoning_tokens`，沒有才用字數 |
| `vs_default` | `like_default`＝像不帶參數；`not_default`＝不是預設（不能證明就是官方獨立檔）；`unknown`＝無法比 |
| `vs_max` | 像不像本次 `max` 槍；沒掃 max 則為空 |

### 不會出現在結果裡

- API key
- 完整 messages
- 官方範例作文全文
- LiteLLM 轉去後端的 body、vLLM `--reasoning-parser`

## 加一個 family

1. 在 `configs/models/` 新增 `<id>.json`（對照 `profile.schema.json` 與現有檔）。
2. 填 `match`（`id_equals`／`id_prefix`／`id_regex`，不要寬鬆 substring）。
3. 用 `params[].kind`：`enum`／`flag`／`int_range`／`locked`／`forbidden`／`echo`。沒有特殊參數就留空 `params`，`official` scope 會是空結果。
4. 不必改探測步驟（STEPS）。用 `MODEL` 對上閘道 id 後跑 `scripts/probe_flowchart.py` 即為實測。

## 判定

| 終端 | 意思 |
|------|------|
| OK | 預期成立 |
| NOOP | 200 但欄位／行為沒對上，或只證明閘道接受 |
| SKIP | 該參數閘道 **4xx** 不接受 |
| FAIL | 應成功卻失敗；非法參數 **5xx**；或官方應拒卻 200 |

流程見 [FLOWCHART.md](./FLOWCHART.md)；逐步判定見 [ARCHITECTURE.md](./ARCHITECTURE.md)。腳本測的是 **LiteLLM 閘道整條鏈**，不是單看 vLLM 或廠商雲端 API。
