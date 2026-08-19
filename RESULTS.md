# 實測結果

**不要在此檔寫 snapshot。** 每次探測的說明在該次 JSON 裡：

`results/flowchart_<MODEL>_<時間>.json` → `meta.read`

| 欄位 | 看什麼 |
|------|--------|
| `request` | 有沒有送到（例如 `"reasoning_effort": "medium"`） |
| `compare.vs_default` | `like_default`＝像預設；`not_default`＝不是預設（仍不能證明就是該檔） |
| `verdict` | pass / noop / skip / fail（中文計數在 `meta.counts`） |

流程見 [ARCHITECTURE.md](./ARCHITECTURE.md)；官方契約見 [PARAMS.md](./PARAMS.md)。
