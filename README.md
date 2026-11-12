# model-chat-completions-params

對 OpenAI-compatible 閘道做**單模型官方參數檢測**：該模型官網列出的特殊參數在 `{OPENAI_BASE_URL}/chat/completions` 是否真能用，回覆是否符合**官方文件寫死的欄位／行為**（不是只回 200，也不是範例作文全文）。`OPENAI_BASE_URL` 已含 `/v1`。

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
├── web/                      本機頁面：自己填金鑰後選模型比對
├── tests/unit/               不打網路的單元測試
└── results/                  本機 JSON + .md（不進版控）
```

官方契約在 `configs/models/*.json`（引擎只認 `params[].kind`）。有模型商／NIM 文件就以官方為準，連結寫在各檔 `source`。有哪些 family、各測什麼，只維護在 [`configs/models/README.md`](./configs/models/README.md)。`MODEL` 用閘道 `GET /models` 的 id。

`.env` 設好 `MODEL` 後直接跑（不要設 `PROBE_SCOPE`，或設成 `official`）：

```bash
python3 -u scripts/probe_flowchart.py
```

其餘 family 只有 match、沒有特殊參數：`official` 是空結果。人讀出處目前只有 Gemma 4 × Kimi K3，見 [PARAMS.md](./PARAMS.md)。

`PROBE_SCOPE=official`（預設）：A 契約槍與官網範例，然後 E（只有 `kind=locked`）。  
`PROBE_SCOPE=full`：A（含 `A.enable`、契約槍、官網範例、`A.multiturn`）→ B → C → D → E → F。沒有 locked 時，E 改打 `temperature=0.2`，而且排在 D 之後。

NCHC 閘道 `GET /models` 分類：`configs/models/_nchc_catalog.json`（`_` 開頭，不會當 Profile 載入）。78 個 chat id 對到 29 個 family；27 個非 chat id（embedding、reranker、ASR、TTS、圖像、影音）不建 chat 探測檔。

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

本機頁面只跑 `official`，由使用者勾選契約槍。金鑰不從 `.env` 帶入，連線時自己填。不跑 B–F，也不讀 `VLLM_BASE_URL`：

```bash
python3 web/server.py
# http://127.0.0.1:8787
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
| `VLLM_BASE_URL` | — | 直連 vLLM 根網址。`VLLM_SERVER_DEV_MODE=1` 時讀 `/server_info` 的 parser 旗標，寫進 `meta.vllm`。不存原始 body |
| `LITELLM_REQUEST_DEBUG` | — | `true`＝每槍加 `litellm_request_debug`。轉發 body 在 LiteLLM 日誌，用回應的 `x-litellm-call-id` 對。不會回到 JSON |

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

`official` 是 A 契約槍與官網範例，加上 locked 的 E；沒有契約就是空表。`full` 是 A → B → C → D → E → F（含 `F.think_*`）。

### `.md` 三欄

表頭：命中的 family、`PROBE_SCOPE`、閘道網址、可用／不可用筆數。

| 欄 | 內容 |
|----|------|
| **官方輸出** | 這槍應該怎樣：題號、送了哪個參數、文件要求的 HTTP／欄位（content、reasoning 有或沒有） |
| **endpoint 輸出** | 閘道實際怎樣：HTTP、reasoning／content 有或沒有、錯誤訊息、這題參數有沒有送出 |
| **輸出錯誤** | 兩欄對不上才寫；通過是 `—` |

第三欄對應判定：`—`＝OK；`FAIL`＝缺欄、不該出現的欄出現了、思考寫在 `content`、推理只在 `provider_specific_fields`、工具呼叫寫在 `content`、該 4xx 卻 200、5xx、401／403／429，或串流中途 error；`NOOP`＝接受但證明不了生效，或 `finish_reason=length` 看不到思考；`SKIP`＝閘道 4xx 不收該參數。401／403／429 不算參數拒絕。

不比範例作文全文，也不用思考量排名當官方證明。

### `.json` 結構

| 路徑 | 內容 |
|------|------|
| `meta.base_url` / `model_profile` / `probe_scope` | 閘道、命中的 family、official 或 full |
| `meta.contract` | 這家契約摘要（`source` 官網連結、長度欄、能否關 thinking） |
| `meta.steps` | 實際跑了哪些步驟（official＝`A,E`；full＝`A`–`F`） |
| `meta.flow` | 四段流程與結論：對上模型、vLLM 旗標、探測、歸類。`conclusion` 是這次能指名的部署問題 |
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
| `fault` | 錯誤來源：`transport_timeout`、`upstream_closed`、`auth`、`rate_limit`、`gateway_param`、`gateway_bug`、`vllm_engine`、`client_reject`、`server_error`、`stream_error`、`embedded_error`、`malformed_response` |
| `reasoning_obs.kind` | `field`／`content_leak`／`provider_only`／`truncated`／`absent` |
| `response_headers` | `x-litellm-call-id` 等診斷 header |
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

流程見 [FLOWCHART.md](./FLOWCHART.md)；逐步判定見 [ARCHITECTURE.md](./ARCHITECTURE.md)。腳本測的是 **LiteLLM 閘道整條鏈**，不是單看 vLLM 或廠商雲端 API。
