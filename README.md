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

步驟順序只寫在 [FLOWCHART.md](./FLOWCHART.md)。`PROBE_SCOPE` 預設 `official`。

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

命令列若已 `export` 同名變數，會蓋過 `.env`。要改回檔案裡的值：

```bash
unset MODEL OPENAI_BASE_URL OPENAI_API_KEY REASONING_EFFORT STREAM_STEPS PROBE_MODE PROBE_SCOPE
```

## 環境變數

兩種用法，不要混在一張表裡看。

**命令列** `python3 -u scripts/probe_flowchart.py` 讀 `.env` 的這三個必填，加上面的選填。

| 變數 | 命令列 | 說明 |
|------|--------|------|
| `OPENAI_BASE_URL` | 必填 | 閘道網址，已含 `/v1` |
| `OPENAI_API_KEY` | 必填 | 這次命令用的金鑰 |
| `MODEL` | 必填 | 閘道 `GET /models` 上的 id |
| `PROBE_SCOPE` | 選填 | `official`（預設）或 `full` |
| `REASONING_EFFORT` | 選填 | 逗號分隔；空白＝該模型契約裡的官方枚舉 |
| `STREAM_STEPS` | 選填 | `true`＝全部串流；`false`＝全關；不設＝只有 D、F 串流。official 契約槍一律非串流 |
| `PROBE_MODE` | 選填 | `probe`（預設）打閘道；`enforce`＝官方不允許就在本機回 400，不送出 |
| `VLLM_BASE_URL` | 選填 | 直連 vLLM 的網址，不是閘道 `/v1`。對方開了 `VLLM_SERVER_DEV_MODE=1` 才讀得到 parser 旗標 |
| `LITELLM_REQUEST_DEBUG` | 選填 | `true`＝每槍多送這個欄位。轉發內容在 LiteLLM 日誌，用回應的 `x-litellm-call-id` 對。不會寫進結果檔 |

`ENABLE_THINKING` 是舊變數，命令列看到會略過。Gemma 的開關來自契約，不是這個變數。

**網頁**不讀上表的金鑰，也不讀 `MODEL`。畫面上是兩列，名稱與下面的網址變數相同：

| 畫面上的名稱 | 網址 | 金鑰 |
|--------------|------|------|
| INNER | `INNER_BASE_URL`；沒寫就用內建的 inner 網址 | 只在頁面填 |
| PORTAL | `PORTAL_BASE_URL`；沒寫就用內建的 portal 網址 | 只在頁面填 |

頁面只跑勾選的 official 槍。`PROBE_SCOPE`、`REASONING_EFFORT`、`STREAM_STEPS`、`VLLM_BASE_URL`、`LITELLM_REQUEST_DEBUG` 對頁面沒有作用。

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

沒有契約時 `official` 是空表。跑哪些步驟見 [FLOWCHART.md](./FLOWCHART.md)。

### `.md` 三欄

表頭：命中的 family、`PROBE_SCOPE`、閘道網址、可用／不可用筆數。

| 欄 | 內容 |
|----|------|
| **官方輸出** | 這槍應該怎樣：題號、送了哪個參數、文件要求的 HTTP／欄位（content、reasoning 有或沒有） |
| **endpoint 輸出** | 閘道實際怎樣：HTTP、reasoning／content 有或沒有、錯誤訊息、這題參數有沒有送出 |
| **輸出錯誤** | 兩欄對不上才寫；通過是 `—` |

第三欄的 OK／FAIL／NOOP／SKIP 只寫在 [ARCHITECTURE.md](./ARCHITECTURE.md)「判定」。不比範例作文全文，也不用思考量排名當官方證明。

### `.json` 結構

| 路徑 | 內容 |
|------|------|
| `meta.base_url` / `model_profile` / `probe_scope` | 閘道、命中的 family、official 或 full |
| `meta.contract` | 這家契約摘要（`source` 官網連結、長度欄、能否關 thinking） |
| `meta.steps` | 實際跑了哪些步驟。順序見 [FLOWCHART.md](./FLOWCHART.md) |
| `meta.flow` | 四段流程與結論：對上模型、vLLM 旗標、探測、歸類。`conclusion` 是這次能指名的部署問題 |
| `meta.counts` | 可用／不可用／接受但未生效／跳過 |
| `meta.read` | 每一槍欄位、`official`、`compare`、`fault` 的定義。讀檔不必再翻文件 |
| `models.<MODEL>.results[]` | 每一槍一筆 |

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
