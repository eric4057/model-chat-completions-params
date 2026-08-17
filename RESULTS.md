# 實測結果

端點：`https://inner-medusa.genai.nchc.org.tw/v1`（LiteLLM）。  
換模型只改 `.env` 的 `MODEL`（與可選 `REASONING_EFFORT`、`ENABLE_THINKING`）。  
原始 JSON 在 `results/flowchart_<MODEL>_<時間>.json`。

圖例：✅ 可用（OK）　⚠️ 接受但未生效（NOOP）　⏭ 跳過（SKIP）　❌ 不可用（FAIL）

現版步驟 **A→F**。舊 JSON 可能仍有 `A.effort_compare`、G／DASH 等已刪項目。

---

## Gemma 4 四 id 對照（2026-08-17）

| id | thinking 要點 | 原始 JSON |
|----|---------------|-----------|
| `gemma-4-E4B-it` | 只送 `reasoning_effort=low/medium` 即有 reasoning；`high` 無 | `flowchart_gemma-4-E4B-it_20260817_133106.json` |
| `gemma-4-26B-A4B-it` | **需** `ENABLE_THINKING=true` + effort 才有 reasoning；無則全 NOOP | `…164703.json`（有）／`…133142.json`（無） |
| `gemma-4-31B-it` | 只送 effort → 200 但 0 字（未生效） | `flowchart_gemma-4-31B-it_20260817_133214.json` |
| `gemma-4-31B-it-with-tool` | 所有 `reasoning_effort` **400 不接受** | `flowchart_gemma-4-31B-it-with-tool_20260817_133241.json` |

---

## Snapshot：gemma-4-26B-A4B-it + ENABLE_THINKING（2026-08-17）

- `REASONING_EFFORT` 空白（掃聯集）、`ENABLE_THINKING=true`
- 原始：`results/flowchart_gemma-4-26B-A4B-it_20260817_164703.json`
- **可用 20／未生效 2／跳過 1**

| 步驟 | 結果 |
|------|------|
| A reasoning | ✅ 預設與 low/medium/high 皆有 reasoning；⏭ `max` 400；⚠️ `none` 仍有 reasoning |
| B response_format | ✅ |
| C tools | ✅ |
| D streaming | ✅；⚠️ 無獨立 reasoning delta |
| E sampling | ✅ |
| F combo | ✅ |

**實務：** 26B 要同時開 `ENABLE_THINKING` 與 `reasoning_effort`；`none` 目前關不掉 thinking。

---

## Snapshot：gemma-4-E4B-it（2026-08-17）

- `REASONING_EFFORT=low,medium,high,none`、未設 `ENABLE_THINKING`
- 原始：`results/flowchart_gemma-4-E4B-it_20260817_133106.json`
- **可用 17／未生效 4／不可用 1**

| 步驟 | 結果 |
|------|------|
| A reasoning | ⚠️ 預設無 reasoning；✅ `low`/`medium` 有；⚠️ `high` 無 |
| B–F | 其餘大多 ✅ |

---

## Snapshot：gemma-4-31B-it（2026-08-17）

- 原始：`results/flowchart_gemma-4-31B-it_20260817_133214.json`
- **可用 17／未生效 5**

A 步驟：預設與各档 effort 皆 200 但 **0 字 reasoning** → 勿當 thinking 已生效。

---

## Snapshot：gemma-4-31B-it-with-tool（2026-08-17）

- 原始：`results/flowchart_gemma-4-31B-it-with-tool_20260817_133241.json`
- **可用 15／未生效 3／跳過 4**

A 步驟：`low`/`medium`/`high`/`none` 全 **400**；此 id 不接受 `reasoning_effort`。

---

## Snapshot：Kimi-K3（2026-08-17）

- `REASONING_EFFORT=low,high,max`（官網三档）
- 原始：`results/flowchart_Kimi-K3_20260817_121800.json`
- **可用 19／未生效 2**

| 步驟 | 結果 |
|------|------|
| A reasoning | ✅ 預設有 reasoning；✅ `low` 明顯短於預設；⚠️ `high`/`max` 與預設字數接近（可能忽略或預設即 max） |
| B–F | ✅ 大多正常 |

**實務：** 用 `reasoning_tokens` 判 effort 是否生效；3+5 題字數拉不開 high/max。閘道枚舉含 `medium`/`none` 不代表 Kimi 官方有。

---

## Snapshot：GLM-5.2（2026-08-14）

- 原始：`results/flowchart_GLM-5.2_20260814_151321.json`
- **可用 19／未生效 5**

A 步驟：僅 `low` 偶發有 reasoning（不穩）；多數 effort NOOP。GLM 原生契約常需 `thinking` 物件，見 `PARAMS.md`。

---

## 舊版：gemma-4-31B-it（2026-08-14）

- 原始：`results/flowchart_gemma-4-31B-it_20260814_102201.json`
- **可用 17／未生效 7**

與 2026-08-17 結論一致：thinking／effort 未生效。B/C/D/E/F 仍可用。
