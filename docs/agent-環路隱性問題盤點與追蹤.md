# Agent 請求↔回應↔再請求 環路隱性問題盤點與追蹤

## 概述

本文件記錄對 CW（coworker）agent 請求 → 回應 → 工具 → 再請求 **全環路** 的隱性問題盤點。
審計方法：完整閱讀 CW 環路源碼（`backend/coworker/agent/*`、`backend/main.py`、`backend/coworker/memory|skills|workspace|context.py`），
並以成熟參考實現 **codex**（`codex-rs/core/src`）與 **opencode-dev**（`packages/opencode/src`）的行為作為主流對照基準。

每個問題以 `ID` 標記，附出處（`file:line`）、現況、隱性問題、主流對照、建議方向、嚴重度與追蹤狀態。
用於後續分批修復的跟踪與驗收。

> 追蹤狀態標記：☐ 待修 / ◐ 進行中 / ☑ 已修 / ➖ 決定不修（記錄原因）

---

## 對照基準（主流做法摘要）

| 維度 | codex | opencode-dev | CW 現況 |
|---|---|---|---|
| 會話狀態 | 常駐 Session + ContextManager，rollout 增量持久化 | 常駐 DB（part 級），每 step 從 DB 增量讀 | 每 turn 重建 graph + 刪 checkpoint + 全量重發歷史 |
| 壓縮/摘要 | token 精確判定，4 種策略，摘要持久在 rollout | overflow→compaction marker 持久在 DB，auto-continue | 摘要只在 checkpoint state，跨 turn 失效 |
| 工具輸出 | normalize_history 截斷（token 級） | 全文落盤 + 轉模型時截斷（TOOL_OUTPUT_MAX_CHARS=2000） | 持久化即截斷至 2000 chars，原始輸出丟失 |
| 迴圈控制 | 顯式 `needs_follow_up` / stop hooks | 顯式 `"continue"/"stop"/"compact"` + finish reason | 隱含 langgraph superstep，middleware 攔截 |
| 重試 | 指數退避 + fallback | `SessionRetry.policy`（分類） | stall 1 次 + overflow 1 次 |
| 記憶 | citation on-demand | 不注入核心迴圈 | 每 model call 注入 ~4000 chars |
| 工具清單 | 就是 schema，不在 prompt 重複 | 就是 schema，不在 prompt 重複 | schema + system prompt 工具目錄 雙份 |
| 權限 | approval policy + sandbox | 預設 ask，ruleset | guarded 對工作區內自動放行 |

---

## 問題清單

### 一、記憶注入（不完整／過大／重複）

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 | 狀態 |
|---|---|---|---|---|---|---|
| M1 | memory/memory_prompt.py:36-55 | **截斷優先級本末倒置**：`format_memory_prompt` 依 precedence 順序（system→BASE→PROJECT→SOUL→AGENT→MEMORY→team）**先到先吃預算**，預算耗盡即 `break`。系統檔偏大時，**agent 自身最相關的 MEMORY.md（排最後）整個被擠掉**，模型完全看不到自己的長期記憶。 | codex 用 citation on-demand；opencode 不注入 | 截斷改成「每檔保底 N 字 + 優先保留 agent 核心檔（SOUL/AGENT/MEMORY）」；或按相關性而非固定順序分配預算 | 高 | ☑ |
| M2 | memory/memory_middleware.py:42-49、memory/memory_manager.py:154、memory/memory_discovery.py:247 | **每次 model call 全庫掃描**：`render_for→scanner.scan()` 遞迴讀取整個 memory root **所有專案/agent/檔案的完整內容**，只為過濾目前 scope。一個 turn 內每個 model call 重複；goal 多輪模式更頻繁。純 IO 浪費。 | opencode 不注入；codex on-demand | 掃描改 scope 級（只讀本 project+agent 路徑）；同 turn 內快取掃描結果 | 高(效能) | ☑ |
| M3 | memory/memory_manager.py:58 | **固定 4000 chars 每請求注入**（≈1000–1300 tokens 固定每 call 開銷）。長 workturn 每 step 都付；內容遠小於一個窗口時也是淨損。 | opencode 核心迴圈無長期記憶 | 考慮改為「按需引用」（工具讀取）或只在 turn 首 step 注入 | 中 | ☑ |
| M4 | memory/memory_manager.py:58 vs memory_middleware.py:33 | **雙重上限矛盾**：真正生效的是 `inject_char_limit=4000`（render 時裁），`MemoryMiddleware.MEMORY_SECTION_MAX_CHARS=30_000` 是死代碼（永不觸發）。 | — | 上限來源單一化，刪除死代碼或改為共同常數 | 低 | ☑ |
| M5 | memory/memory_prompt.py:67-82 | **boilerplate 計入預算**：`<file kind=… name=… source=…>`、`(empty)`、XML 逃逸後的字元都算進 4000 chars，實際可用內容更少。 | — | 檔頭/標籤不計入內容預算，或計入後放寬 | 低 | ☑ |

### 二、Prompt 注入（過大／重複）

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 | 狀態 |
|---|---|---|---|---|---|---|
| P1 | agent/phase_gate.py:117-119、agent/system_prompt.py:182-253 | **工具 schema 與 system prompt 工具目錄重複付費**：工具 schema 本就隨每請求送給 provider；PhaseGate 又把一份「Available tools」目錄（上限 4000 chars）塞進 system prompt。同一份信息每請求付兩次 token。 | codex/opencode 工具清單就是 schema，不在 prompt 重複 | 移除或大幅縮小工具目錄；只保留「不可由 schema 表達」的指引 | 中 | ☑ |
| P2 | agent/graph.py:337、557、240 | **工具 description 過長**：`memory`（2099 chars）、`update_goal`（951）、`install_skill`（827）、`run_command`（640）、`memory_read`（607）。description 每字進 schema token 成本，且每回合重付。 | 主流 description ~100–400 chars，長指引移入 skill/memory 檔 | 瘦身 description；長指引改放文件，工具描述只留「什麼時候用 + 關鍵參數」 | 中 | ☑ |
| P3 | agent/phase_gate.py:113-116、agent/system_prompt.py:50-120 | **workspace 目錄樹每 model call 重新 walk FS**：`build_workspace_context→build_workspace_tree` 遞迴 `iterdir`。同 turn 內目錄不變卻每 step 重建重發。 | opencode 每 turn 組裝；codex world-state diff | turn 級快取目錄樹；工具執行導致變更後才刷新 | 中(效能) | ☑ |
| P4 | skills/skill_middleware.py:61、64-85 | **Activated skill 正文上限 80k chars**（≈25k–60k tokens）：`[skill:…]` 啟用後每個 model call 都注入 system prompt，單一技能可佔大半窗口；skills catalog 亦無 token cap。 | opencode Agent Skills 正文注入要小得多 | 上限降到主流量級（如 ~8–15k chars）；超過則只注入摘要+提示 load_skill | 中 | ☑ |
| P5 | agent/graph.py:851-898 | **每 step 重複注入記憶+技能+workspace+工具**：四者皆由 wrap_model_call 在每個 model call 重算重發。 | opencode 每 turn；codex 每 step 但 diff | 同 turn 內結果快取；system 組裝改為一次 | 中 | ☑ |

### 三、檔案讀取過大

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| R1 | workspace.py:42、agent/core.py:82-93、graph.py:144 | **`read_file` 整檔 `read_text` 載入記憶體**再切窗，輸出上限 50k chars（code-heavy ≈ 38k tokens），且該工具結果進 history。 | opencode 按行/位元組有界串流 + offset 分頁（read.ts:137-180）；codex 插入前 token 截斷 | `read_preview` 改**有界串流**（不整檔載入）+ binary 4KB 偵測 + 插入前 token 截斷 | 中 | ☑ |
| R2 | agent/core.py:38、1108-1133、main.py:1957-1958 | **附件 120k chars 內聯且永久重放**：`MAX_ATTACHMENT_CHARS=120_000`（≈90k+ tokens）；文字附件內聯進 user 訊息 content 並**連同附件持久化**，之後**每一輪請求都重新內聯重發**。 | opencode 附件=part 引用 + stripMedia/compaction 降級為 `[Attached <mime>: <filename>]` | inline-once + 歷史 stub 重放（`format_user_message(inline_attachments=False)`） | 高 | ☑ |
| R3 | workspace.py:34-35、1277 | **搜尋每檔讀 1MB 掃描、不 respect ignore**：`DEFAULT_SEARCH_MAX_FILE_BYTES=1_000_000`，Python 逐行掃描無 `.gitignore` 感知。 | opencode grep 走 ripgrep + ignore + limit 100（grep.ts）；codex 走 shell grep | rg 快路徑（有 rg 時）+ ignore-aware fallback + 上限降到 256KB + 串流掃描 | 低 | ☑ |

### 四、摘取／抽取過大

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| E1 | memory/memory_manager.py:388-407、auto_extract.py:29-44 | **dream 每輪最多 4 次主模型 LLM call**：extract → stage → consolidate → `_verify_preservation`(可能第 3 次) → session-summary(第 4 次)。`extract_model` 設定已存在但未節省 call 數。 | hermes：背景 review 每 10 turn **一次** call（background_review.py）；opencode：唯一 LLM 摘要在 compaction（背景 fork） | Dream 改 **單一合併 call**（`run_extract_and_merge`：抽取+就地合併一次完成）；`extract_model` 經 `_memory_extract_llm()`(main.py:1104) 已生效 | 中 | ☑ |
| E2 | memory/auto_extract.py:304-389 | **consolidation 輸入過大且多 call**：`{existing}` 整個 MEMORY.md（~4000 chars）+ 全部候選內嵌，`_verify_preservation` 相似度 miss 時又來一次 LLM call（最壞 1 dream = 4 次）。 | hermes：consolidation 是 char-budget 強制 + 模型就地 merge，**無 verify LLM**（memory_tool.py:428-441） | 合併 prompt 一次回傳 blocks；guardrail 全改**規則**（覆蓋率相似度 + 大小預算），移除 `_verify_preservation` LLM call | 中 | ☑ |

### 五、壓縮精簡不正確

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| C1 | main.py:1949、2216 vs runtime.py:687-698 | **壓縮摘要跨輪/跨 goal round 失效（架構性 bug）**：`context_summary` / `context_summarized_fingerprints` 只在 LangGraph checkpoint state；main.py 在**每 turn 開頭**（1949）與**每個 goal round**（2216）都 `forget_runtime_checkpoint`，且 session messages 不持久化摘要 HumanMessage。→ **每輪都從全量歷史重新壓縮、摘要永遠不累積**，長 goal 任務上下文無界增長、壓縮成本每輪重付。 | codex rollout 常駐、摘要持久化；opencode compaction marker 持久在 DB；LangGraph 官方設計需 persistent thread | **摘要狀態持久化到 session**（`context_summary`/fingerprints/`update_compaction`）＋ turn 開始重注入（`先前对话摘要` HumanMessage + `context_summary` state）→ anchored update 跨輪累積 | 高 | ☑ |
| C2 | context_compaction.py:41、414-433 | **摘要輸入被雙重截斷**：工具輸出先被持久化截到 2000 chars（O1），`_serialize_for_summary` 又截到 2000，摘要資訊量偏低。 | opencode 全文落盤後截斷 | 摘要讀原始全文（見 O1 改造）；`SUMMARY_INPUT_MAX_TOKENS 32k→20k`（對齊 codex） | 低 | ☑ |
| C3 | context_compaction.py:50-96、424-433 | **摘要模型 fallback 鏈首選「使用者預設模型」**（可能最貴/最大），輸入 32k tokens。 | opencode compaction agent 預設 session model；LangChain 官方 `model` 才獨立指定 | **摘要直接用使用者預設模型**（不另設壓縮模型，依決策）；預設模型不可用 → `context_compact_failed` → SSE/notice 提示更換模型 | 中 | ☑ |
| C4 | context_compaction.py:261-273、base.py:50 | **`_trim` 截斷倍率一刀切**：`TRUNCATE_CHARS_PER_TOKEN=1.5` 對拉丁文遠低於真實 3.8（砍過頭、損失內容），對 CJK 較安全。 | — | 改用 `truncate_to_token_budget`（token 精確）；移除 `TRUNCATE_CHARS_PER_TOKEN`/`_truncate_message`（孤兒清理） | 低 | ☑ |

### 六、Token 計數不正確／不一致

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| T1 | agent/core.py:1212 vs context.py:63 | **多套 chars/token 常數互相矛盾**：`CHARS_PER_TOKEN=3.5`（字元預算）vs `LATIN_CHARS_PER_TOKEN=3.8`（估算）。C4 後決策已全走 token；char 僅顯示。 | codex `bytes/4`、opencode `chars/4`、LangChain `token_counter`（自訂）皆**單一來源** | 移除 `CHARS_PER_TOKEN`；`context_budget_chars` 改用 `LATIN_CHARS_PER_TOKEN`（單一常數，char 鏡像由估算器同比率導出） | 中 | ☑ |
| T2 | context.py:110-111 | **CJK 偵測範圍過窄**：`"一" <= ch <= "鿿"`（U+4E00–U+9FFF）不認**日文假名、全形標點（，。！？）、韓文** → 被當 Latin 以 3.8 chars/token 低估 token 數，可能超窗。 | — | 擴充 CJK 判定（含全形標點/假名/韓文/ExtA/相容表意，排除全形拉丁字母） | 中 | ☑ |
| T3 | context.py:401-459 | **校準受 prompt-cache 干擾**：`usage_metadata.input_tokens` 在啟用 prefix cache 的 provider 上可能不含 cache hit，比值長期被拉低，進而低估非 cache 請求。 | codex/opencode 以 provider 實際 usage（含 cache 佔窗）為權威 | 校準折入**含 cache 的總 input**（`_normalize_usage_total`：`input + cache_read`，Anthropic/OpenAI 兩種 key 形式） | 低 | ☑ |
| T4 | context.py:79、165-204 | **short base64（<256 chars）以 prose 計**：`BASE64_MIN_RUN=256` 以下真實 base64 被當文字低估且不 scrub，仍送模型。 | — | `BASE64_MIN_RUN 256→128`（仍須過熵檢查） | 低 | ☑ |

### 七、請求 prompt build 不合理

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 | 狀態 |
|---|---|---|---|---|---|---|
| B1 | phase_gate.py:120-123、skill_middleware.py:155-164、memory_middleware.py:61-69 | **系統訊息被 3+ middleware 各自整個覆寫**：PhaseGate→Skill→Memory 每個都 `SystemMessage(f"{section}\n\n{base_text}")`，把不斷增長的 system 反覆整段複製拼接。`base.py` 只讀 `.text`（content 若為 list 拿到空串）。 | opencode 一次組裝；codex base instructions 單源 | 改成「一次組裝 + middleware 只提供片段」；統一 content 讀取 | 中 | ☑ |
| B2 | agent/graph.py:851-898、system_prompt.py:279-325 | **行為 prompt 被排最尾**：最終 system 順序 = memory→skills→phase+workspace+tools→behavior。最該被模型遵守的工具紀律/不空轉指引被長目錄稀釋。 | codex/opencode 行為放前、動態內容放後 | 調整組合順序：行為核心 → memory → skills → workspace → tools | 低 | ☑ |
| B3 | agent/system_prompt.py:229-249 | **工具目錄對 MCP/未知工具退回完整 description**（可能上千字），與 P1 的雙份成本疊加。 | — | MCP 工具用短摘要；未註冊工具可不列 | 中 | ☑ |

### 八、重複／多餘步驟

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| D1 | runtime.py:474-522、main.py:1949 | **每 turn 重建 graph + 重建全部工具 + 刪 checkpoint + 全量重發歷史**：`build_coworker_agent_graph`（每次 new）+ `build_workspace_tools`（每次 new 全部 tool 物件與閉包）+ `forget_runtime_checkpoint`。 | codex 常駐 Session/ContextManager；opencode 從 DB 增量讀 | 設計常駐/可複用的 graph 與工具集；或至少 turn 級快取構建結果 | 高 |
| D2 | main.py:2216-2220 | **goal 多輪 = 每輪重複 D1 全部**（重建圖、刪 checkpoint、重掃記憶、重 walk 目錄、重壓縮）。 | — | 同 C1：goal round 間保留 checkpoint/摘要；構建結果緩存 | 高 |
| D3 | agent/core.py:856-862 | **`_merge_event_parts` O(n²)**：每個 `tool_delta` chunk 線性掃描 merged 找既有 tool part；長工具 input 分塊多時成本累積。 | — | 用 id→index 字典維護 tool part 位置 | 中(效能) |
| D4 | middleware/loop_guard.py:109-136 | **stall 重試會把整條 compaction/guard 鏈重跑**：StallRetry 包最外層，重試時 Summarization→ContextGuard 全部重算（非確定，可能跑出不同壓縮）。 | codex/opencode 重試僅重試採樣 | 重試只重跑採樣層，不重跑 before_model 鏈 | 低 |
| D5 | core.py:537-567、middleware/base.py:248-263 | **`_strip_plan_leak` + `_strip_compaction_echo` 兩次整串掃描**，plan leak 比對只取第一個 plan 片段。 | — | 合併清理；比對全部 plan 片段 | 低 |

### 九、過度嚴格

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| S1 | middleware/loop_guard.py:286-299 | **死迴圈防護硬停剝光所有工具**：`stop_after=4` 連續相同 call → 剝離全部工具、逼文字收尾（不可逆，本輪無法再叫工具）。對「同一指令客觀上需重試 5 次」的合理任務（如不穩的 MCP）會誤傷。 | opencode doom-loop（連續 3 次）是**問權限**（可選續跑）；codex 限流/提醒 | 硬停前改「詢問使用者」或僅對該工具停用；保留剝離作為最後手段 | 中 |
| S2 | agent/core.py:477-480、phase_gate.py:59-77 | **discuss 階段 `update_goal`/`get_goal` 不可用**（屬 `_EXEC_TOOLS`）：規劃/研究階段無法對持久目標發起 blocked/complete 審計。 | — | 評估是否在 discuss 開放 `get_goal`（只讀） | 低 |
| S3 | middleware/context_guard.py:269-296 | **ContextGuard S4 靜默丟全部 MCP 工具 schema**：超窗時一次性移除所有 MCP 工具定義，模型該 step 無法呼叫任何 MCP 工具且無告知。 | — | 按「最近使用優先保留」而非全丟；或移除前給模型提示 | 中 |

### 十、過度寬鬆

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| L1 | middleware/hitl.py:132-165 | **guarded 模式對工作區內寫入/命令全自動放行**：`_needs_write_approval` guarded 只查外部路徑、`_needs_command_approval` guarded/autonomous 全放行。CW 的 guarded 實質接近 autonomous。 | opencode 預設 ask；codex approval policy | 若為產品決策可保留，建議增加「寫入後 diff 摘要回饋」對沖；或對高風險命令（rm/clean 等）回升需審批 | 低(設計) |
| L2 | agent/core.py:38、974、1105-1106 | **圖片 data URL 原樣內聯**：每張 ~1200+ tokens 估算（實際 qwen 720p 可達 1.1–1.6k），5 張 ≈ 6k+ tokens 固定注入，還隨 history 重放。 | — | 圖片進 history 時外置/降檔，或僅當輪保留 | 中 | ☑ |
| L3 | agent/core.py:149、workspace.py:37 | **`run_command` timeout 上限 60s、預設 20s**：對 `npm install`/`build`/`test` 常不足，模型被迫反覆重跑，反而製造更多重複步驟。 | — | 放寬上限（如 120–300s）或對長任務支援非同步/後台執行 | 低 |

### 十一、不合理值

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| V1 | core.py:1167、1192-1208 | `CONTEXT_SAFETY_FACTOR=0.75`：trim 在 effective window 的 75% 就觸發，疊加 calibration≥1.0 偏保守（過早壓縮）。 | opencode 用 `window − reserved` 精確判定 | 評估調高 safety factor 或改精確判定 | 低 |
| V2 | base.py:36 | `SUMMARY_OUTPUT_TOKENS=4096`：與 opencode 一致，合理。 | 一致 | 保留 | - |
| V3 | context.py:69 | `PER_MESSAGE_OVERHEAD_TOKENS=4`：qwen 模板測過 ~4，合理。 | 一致 | 保留 | - |
| V4 | base.py:33 | `KEEP_RECENT_TOKENS=8000`：壓縮後 resident≈8k+摘要，與 opencode DEFAULT_KEEP_TOKENS=8000 對齊，合理。 | 一致 | 保留 | - |
| V5 | system_prompt.py:24-29、memory、skill_middleware | **固定注入總量無預算**：記憶 4000 + skills 目錄 + workspace 樹 6000 + 工具目錄 4000 ≈ 15k chars（~5k tokens）每次請求；疊加後可能爆窗。 | opencode 對 system 有隱含預算 | 為「固定注入」設總預算與優先級，防止疊加 | 中 | ☑ |

### 十二、不合理順序

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| O1 | core.py:769-815 | **工具輸出持久化即截斷 2000 chars**：`_message_chunk_events` 在 `tool_end` 就 `output[:2000]` 存進 parts → 原始工具輸出在 session 就丟失，後續摘要/重放/回滾都只有 2000 chars。 | opencode **全文落盤**、只在轉模型時截斷 | 全文存 session（或落盤檔案），截斷只在模型轉換時 | 中 | ☑ |
| O2 | graph.py:851-867 | **記憶 middleware 掛在 Skills 之後**，注入在 phase 之後，導致行為 prompt 墊底（B2）；記憶與技能的組合順序靠隱式鏈，不易維護。 | — | 顯式定義 system 組合順序（見 B2） | 低 | ☑ |

### 十三、不符合主流做法

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| N1 | runtime.py:550、loop_guard.py | **顯式 step 迴圈/終止判定缺失**：依賴 langgraph 內部 superstep（`recursion_limit=9999` 隱含），無「finish reason」「needs_follow_up」「step 上限注入」等判據；終止/溢出分流全靠 middleware 攔截。 | opencode `"continue"/"stop"/"compact"`；codex `needs_follow_up` | 將迴圈決策顯式化（每 step 回傳結果），逐步替換 middleware 攔截 | 中 |
| N2 | sessions.py、main.py:3306-3360 | **訊息級 JSON 儲存 + parts 重建**（vs part 級 DB）：中斷存活、增量同步、精確回滾較弱；`_parts_to_conversation` 重放受 2000 chars 限制。 | opencode part 級 SQLite | 評估 part 級持久化；短期先把 tool 全文落盤（O1） | 中 |
| N3 | loop_guard.py:61-136、model_defaults.py:114 | **請求層重試策略單薄**：僅 stall-retry(1 次)+overflow(1 次)+langchain max_retries=2；無分類退避、無 transport fallback、無 auth refresh 重試。 | opencode `SessionRetry.policy`；codex 指數退避 + websocket→http fallback | 建分類重試策略（可重試/不可重試/rate-limit action） | 中 |
| N4 | agent/core.py:941-965、main.py:3036-3041 | **標題生成用主模型完整 chat call**（同步 `to_thread`）：高成本低價值。 | 主流用規則/小模型 | 用規則或小模型；失敗靜默 fallback（已有） | 低 |
| N5 | 全局 | **每 turn 冷啟動重建一切**（D1/D2 匯總）：無 prompt cache 增量、無跨 turn 上下文增量、壓縮狀態無法存活。 | codex 常駐 session；opencode DB 增量 | 作為中期重構主線（常駐 session 化），見「執行建議」 | 高 |

---

## 優先級與執行建議

### P0 — 修架構性缺陷（正確性／成本）

1. **C1**：壓縮摘要跨輪存活（持久化 `context_summary` 到 session，或 goal round 間保留 checkpoint）。 ~~→ ☑ 已修（2026-08-27：session 持久化 + turn 重注入）~~
2. **R2**：大附件不再永久內聯重放（改摘要 + 路徑，模型按需 `read_file`）。
3. **M1**：記憶注入截斷改「agent 核心檔優先 + 每檔保底」。 ~~→ ☑ 已修（2026-08-27）~~

### P1 — 消除重複／過大（每請求成本）

4. **D1/D2**：每 turn 固定開銷控制——graph／工具／記憶掃描／目錄樹做 turn 級快取或增量。
5. **P1/P2**：工具目錄去重 + description 瘦身。
6. **M2**：記憶注入改 scope 級掃描（不掃非本 scope 內容）。 ~~→ ☑ 已修（2026-08-27）~~
7. **P4**：activated skill 上限降到主流量級。
8. **O1**：工具輸出全文落盤、僅轉模型時截斷（連動 C2/N2）。

### P2 — 計數／數值一致性

9. **T1/T2**：收斂 chars/token 常數 + 擴充 CJK 偵測。
10. **S1**：死迴圈防護改「先問/先提醒」而非直接剝光工具。
11. **B1/B2**：system 一次組裝 + 行為 prompt 前置。
12. **N1/N3**：迴圈決策顯式化 + 分類重試策略（中期）。

---

## 追蹤狀態總表

| ID | 類別 | 標題 | 嚴重度 | 優先級 | 狀態 | 備註／修復 |
|---|---|---|---|---|---|---|
| M1 | 記憶 | 記憶截斷優先級本末倒置 | 高 | P0 | ☑ | 記憶索引化：agent 核心檔優先 |
| M2 | 記憶 | 記憶注入每次 model call 全庫掃描 | 高(效能) | P1 | ☑ | scope 級掃描 + render 快取 |
| M3 | 記憶 | 記憶固定 4000 chars 每請求注入 | 中 | P2 | ☑ | token 預算索引（預設 2500）|
| M4 | 記憶 | 記憶雙重上限矛盾（死代碼 30k） | 低 | P2 | ☑ | 移除死碼 |
| M5 | 記憶 | 記憶區塊 boilerplate 計入預算 | 低 | P2 | ☑ | 結構標記不計內容預算 |
| P1 | Prompt | 工具 schema 與工具目錄重複 | 中 | P1 | ☑ | 移除目錄，schema 即工具清單 |
| P2 | Prompt | 工具 description 過長 | 中 | P1 | ☑ | 六工具瘦身 5518→2520 chars + 上限 650 |
| P3 | Prompt | workspace 目錄樹每 call 重 walk | 中(效能) | P1 | ☑ | turn 快取 + 60 entries/4000 chars |
| P4 | Prompt | activated skill 上限 80k 過大 | 中 | P1 | ☑ | 12k + catalog 1500 token cap |
| P5 | Prompt | 每 step 重複注入記憶+技能+工具 | 中 | P2 | ☑ | SystemAssembler 單點組裝 |
| R1 | 讀取 | read_file 整檔載入 + 輸出過大 | 中 | P2 | ☑ | 有界串流 + binary 4KB 偵測 + 插入前 token 截斷 |
| R2 | 讀取 | 附件 120k chars 永久重放 | 高 | P0 | ☑ | inline-once + 歷史 stub（重放 -99.7%）|
| R3 | 讀取 | 搜尋 1MB/檔掃描、無 ignore | 低 | P2 | ☑ | rg 快路徑 + ignore-aware fallback + 256KB |
| E1 | 摘取 | dream 每輪最多 4 次主模型 LLM call | 中 | P2 | ☑ | 單一合併 call（4→1）|
| E2 | 摘取 | consolidation 輸入過大且多 call | 中 | P2 | ☑ | 移除 verify LLM；規則 guardrail |
| C1 | 壓縮 | 壓縮摘要跨輪失效（架構 bug） | 高 | P0 | ☑ | session 持久化 + turn 重注入 |
| C2 | 壓縮 | 摘要輸入雙重截斷 | 低 | P2 | ☑ | O1 已根治；輸入 32k→20k |
| C3 | 壓縮 | 摘要模型首選主模型 | 中 | P2 | ☑ | 用預設模型 + 不可用提示（依決策）|
| C4 | 壓縮 | trim 截斷倍率一刀切 | 低 | P2 | ☑ | token 精確截斷 + 孤兒清理 |
| T1 | 計數 | chars/token 常數多套矛盾 | 中 | P2 | ☑ | 單一常數（移除 CHARS_PER_TOKEN）|
| T2 | 計數 | CJK 偵測範圍過窄 | 中 | P2 | ☑ | 擴充全形/假名/韓文/ExtA |
| T3 | 計數 | 校準受 prompt-cache 干擾 | 低 | P2 | ☑ | 校準用 cache-inclusive actual |
| T4 | 計數 | short base64 低估 | 低 | P2 | ☑ | 門檻 256→128 |
| B1 | Prompt build | system 被多 middleware 整段覆寫 | 中 | P2 | ☑ | SystemAssembler 單一組裝點 |
| B2 | Prompt build | 行為 prompt 排最尾 | 低 | P2 | ☑ | behaviour→phase→capabilities→MCP→workspace→memory→skills |
| B3 | Prompt build | 工具目錄 MCP 退回全 description | 中 | P2 | ☑ | 目錄整體移除（P1） |
| D1 | 重複 | 每 turn 重建一切 | 高 | P1 | ☑ | 編譯快取（W1） |
| D2 | 重複 | goal 多輪每輪重複 | 高 | P1 | ☑ | goal 多輪共享快取 |
| D3 | 重複 | `_merge_event_parts` O(n²) | 中(效能) | P2 | ☑ | id→index O(1) |
| D4 | 重複 | stall 重試重跑 compaction 鏈 | 低 | P2 | ☑ | 摘要 memoize + 分類重試 |
| D5 | 重複 | plan leak / compaction echo 清理重複 | 低 | P2 | ☑ | _clean_final_content 合併 |
| S1 | 過度嚴格 | 死迴圈硬停剝光工具 | 中 | P2 | ☑ | 權限門（autonomy-aware） |
| S2 | 過度嚴格 | discuss 不可用 goal 查詢 | 低 | P2 | ☑ | get_goal 已讀唯讀工具 |
| S3 | 過度嚴格 | guard S4 靜默丟全部 MCP schema | 中 | P2 | ☑ | 保留最近使用 MCP |
| L1 | 過度寬鬆 | guarded 對工作區內全放行 | 低(設計) | P2 | ☑ | tool_end.files diff 回饋（既有） |
| L2 | 過度寬鬆 | 圖片 data URL 永久內聯 | 中 | P2 | ☑ | 歷史重放 stub（R2）|
| L3 | 過度寬鬆 | run_command timeout 上限偏小 | 低 | P2 | ☑ | timeout 300 + 後台 run_command_status |
| V1 | 數值 | safety factor 0.75 偏保守 | 低 | P2 | ☑ | 0.75→0.9 |
| V5 | 數值 | 固定注入總量無預算 | 中 | P2 | ☑ | SystemAssembler 16k token 總預算 |
| O1 | 順序 | 工具輸出持久化即截斷 2000 | 中 | P1 | ☑ | 全文持久化 + 重放 token 截斷 |
| O2 | 順序 | 記憶/技能組合順序隱式 | 低 | P2 | ☑ | SystemAssembler 顯式順序 |
| N1 | 主流 | 迴圈決策顯式化缺失 | 中 | P2 | ☑ | loop_reason 顯式化 + MAX_STEPS |
| N2 | 主流 | 訊息級儲存 vs part 級 | 中 | P2 | ☑ | 增量落盤 P1 |
| N3 | 主流 | 重試策略單薄 | 中 | P2 | ☑ | 分類重試 policy |
| N4 | 主流 | 標題生成用主模型 | 低 | P2 | ☑ | 規則標題（無模型 call） |
| N5 | 主流 | 每 turn 冷啟動（重構主線） | 高 | P1 | ☑ | 編譯快取 + reset_per_turn（W1） |

---

## 已修復紀錄

### 2026-08-27 — 記憶注入全套對齊 codex（M1–M5，方案 A+B）

改動檔案：`memory_prompt.py`、`memory_discovery.py`、`memory_manager.py`、`memory_middleware.py`、`agent/graph.py`（memory_read 說明）。

| 項目 | 實作內容 |
|---|---|
| **B + M3** | 常駐區塊改為**緊湊索引**：每個記憶檔只保留 `kind/name/rel/source` 標頭 + **每 block 首行預覽**（`PREVIEW_LINE_CHARS=160`）；全文一律 `memory_read` on-demand。預算改為 **token 制**（`MemoryConfig.inject_token_limit`，預設 2500，對齊 codex `MEMORY_TOOL_DEVELOPER_INSTRUCTIONS_SUMMARY_TOKEN_LIMIT=2500`）。 |
| **M1** | 注入依**相關性排序**：agent 核心檔（SOUL/AGENT/MEMORY）→ agent base → project（BASE/PROJECT）→ system → team。agent 自身記憶不再被 system 檔擠掉；即使預算耗盡仍保留全部檔案的檔頭清單。 |
| **M2** | 新增 `MemoryScanner.scan_scoped()` / `scoped_paths()`：只讀本 scope 檔案，不再 rglob 全庫。`MemoryManager` 增加 **render 快取**（mtime/size fingerprint，變化才重渲染），長 turn 內多個 model call 不重複掃描/渲染。 |
| **M4** | 移除 `MEMORY_SECTION_MAX_CHARS=30_000` 死代碼，上限來源單一化（token 預算）。 |
| **M5** | 結構標記（`<file>`/`<memory>`/warning）**不計入內容預算**；`format_memory_prompt(char_limit)` 保留為向後相容包裝（`char_limit//4 → token`）。 |
| **B/citation** | 索引區塊標頭指示模型「用 `memory_read` 讀全文，於最終回答引用 rel path」；`memory_read` docstring 同步更新。 |

**驗證**：`stress_test.py` 新增 5 組測試（優先級+預算 / 結構不計 / scope 隔離（含 DREAMS.md 排除）/ render 快取失效）；`120 passed`。`selftest.py` 僅餘 2 項既有失敗（context_usage telemetry、summary cap，均與本改動無關）。`tests/` pytest `183 passed`。

---

## 已修復紀錄

### 2026-08-27 — 記憶注入全套對齊 codex（M1–M5，方案 A+B）

改動檔案：`memory_prompt.py`、`memory_discovery.py`、`memory_manager.py`、`memory_middleware.py`、`agent/graph.py`（memory_read 說明）。

| 項目 | 實作內容 |
|---|---|
| **B + M3** | 常駐區塊改為**緊湊索引**：每個記憶檔只保留 `kind/name/rel/source` 標頭 + **每 block 首行預覽**（`PREVIEW_LINE_CHARS=160`）；全文一律 `memory_read` on-demand。預算改為 **token 制**（`MemoryConfig.inject_token_limit`，預設 2500，對齊 codex `MEMORY_TOOL_DEVELOPER_INSTRUCTIONS_SUMMARY_TOKEN_LIMIT=2500`）。 |
| **M1** | 注入依**相關性排序**：agent 核心檔（SOUL/AGENT/MEMORY）→ agent base → project（BASE/PROJECT）→ system → team。agent 自身記憶不再被 system 檔擠掉；即使預算耗盡仍保留全部檔案的檔頭清單。 |
| **M2** | 新增 `MemoryScanner.scan_scoped()` / `scoped_paths()`：只讀本 scope 檔案，不再 rglob 全庫。`MemoryManager` 增加 **render 快取**（mtime/size fingerprint，變化才重渲染），長 turn 內多個 model call 不重複掃描/渲染。 |
| **M4** | 移除 `MEMORY_SECTION_MAX_CHARS=30_000` 死代碼，上限來源單一化（token 預算）。 |
| **M5** | 結構標記（`<file>`/`<memory>`/warning）**不計入內容預算**；`format_memory_prompt(char_limit)` 保留為向後相容包裝（`char_limit//4 → token`）。 |
| **B/citation** | 索引區塊標頭指示模型「用 `memory_read` 讀全文，於最終回答引用 rel path」；`memory_read` docstring 同步更新。 |

**驗證**：`stress_test.py` 新增 5 組測試（優先級+預算 / 結構不計 / scope 隔離（含 DREAMS.md 排除）/ render 快取失效）；`120 passed`。`selftest.py` 僅餘 2 項既有失敗（context_usage telemetry、summary cap，均與本改動無關）。`tests/` pytest `183 passed`。

### 2026-08-27 — Prompt 注入 + PhaseToolGate 拆分（P1–P5 / B1–B3 / V5 / O2，檔次 1+2）

改動檔案：`agent/middleware/phase_gate.py`、`agent/middleware/system_assembler.py`（新）、`agent/graph.py`、`agent/core.py`、`agent/system_prompt.py`、`skills/skill_middleware.py`、`skills/skills.py`。

| 項目 | 實作內容 |
|---|---|
| **P1** | 移除 `## Available tools` 工具目錄注入（`phase_gate.py`）；工具清單即 phase 過濾後的 schema（主流：codex `model_visible_specs` / opencode `SessionTools.resolve`）。`build_tool_context` 標 deprecated；行為 prompt 的「Use ONLY the tools provided to you」同步更新。`_blocked_tool_message` 保留作 hallucination 安全網。 |
| **P2** | 六工具 description 瘦身：`memory 2099→617`、`update_goal 951→427`、`install_skill 827→393`、`run_command 640→370`、`memory_read 607→319`、`delegate_task 394`（合計 **5518→2520 chars，-54%**）。新增 `MAX_TOOL_DESCRIPTION_CHARS=650` + 靜態 guard 測試。 |
| **P3** | workspace 樹 **turn 級快取**（SystemAssembler 依 `(phase, root mtime)` 快取）；`MAX_TREE_ENTRIES 120→60`、`MAX_TREE_CHARS 6000→4000`。 |
| **P4** | `SKILL_BODY_MAX_CHARS 80k→12k`；skills catalog 新增 `format_skills_prompt_bounded`（`SKILLS_CATALOG_MAX_TOKENS=1500`，先縮 desc 再逐檔捨棄，並附「還有更多技能可 load_skill」提示）。 |
| **P5/B1/B2/O2** | 新增 **`SystemAssembler`**（codex fragment 模型）：behaviour→phase→capabilities→workspace→memory→skills **單一組裝點**，替換 PhaseToolGate prompt 組裝 + SkillMiddleware + MemoryMiddleware 各自的 system_message 覆寫；順序顯式化。Skills 於 discuss 隱藏、memory 每 phase 注入語義保留。 |
| **V5** | `SYSTEM_FIXED_BUDGET_TOKENS=16_000` 總預算：超限依優先級自低而高捨棄（skills→memory→workspace→capabilities），behaviour/phase 永不捨棄。 |
| **PhaseToolGate 拆分** | 三分職責 → 二分：可見性（`tools=` override）+ 守門（`wrap_tool_call`）留在 gate；prompt 組裝移入 SystemAssembler。 |

**量化**：實測（真實 scoped memory + workspace + behavior + phase + capabilities）組裝後 system ≈ **842 tokens**；改前同場景含工具目錄（≤4000 chars ≈ 1000+ tokens）＋全文記憶，固定注入成本估 >50% 下降。

**驗證**：`tests/` pytest **188 passed**（+5：phase-gate 可見性測試、desc guard、SystemAssembler 4 組）。`stress_test.py` **120 passed**。`selftest.py` 僅餘 2 項既有失敗（與本改動無關）。

### 2026-08-27 — 檔案讀取過大（R1–R3 / O1 / L2，源頭優化）

改動檔案：`workspace.py`、`context.py`、`agent/core.py`、`main.py`、`tests/test_read_streaming.py`、`tests/test_attachments.py`、`tests/test_search_ripgrep.py`（新）。

| 項目 | 實作內容 |
|---|---|
| **R1** | `read_preview` 改**有界串流**：`_read_window`（`for line in fh` 逐行 + `max_chars` 位元組累積封頂）＋ `_count_lines`（串流計行），**不再 `read_text` 整檔載入**；`_is_binary_bytes`（NUL / 30% 非可列印）讀前 4KB 偵測（opencode isBinaryFile）；`READ_FILE_MAX_CHARS 50k→40k`。 |
| **R1/codex 對齊** | `context.truncate_to_token_budget(text, budget)`（`TruncationPolicy::Tokens` 等價）：跨輪重放的工具結果在 `_parts_to_conversation` 以 `TOOL_REPLAY_MAX_TOKENS=4000` **token 預算**截斷，取代固定 char 切片。 |
| **O1** | 工具輸出**全文持久化**（`TOOL_OUTPUT_PERSIST_MAX_CHARS=128k` fuse），`tool_end output[:2000]` 移除——摘要/回滾/續跑都有全文；重放時才 token 截斷。 |
| **R2** | `format_user_message(inline_attachments=…)`：`True`（當前輪）全文/圖片內聯；`False`（歷史重放）渲染**緊湊 stub**（`[Attachment]/[Image]/[Binary attachment]` + 重附指引，opencode stripMedia/compaction 模式）。三處重放接線（main.py:1922/2108/3433）改 `False`；當前請求（1957）維持 `True`。 |
| **R3** | `search_text` 加 **rg 快路徑**（`rg --json`，respect `.gitignore`，`--max-count 1`，有 `rg` 才走）；fallback 改 **ignore-aware**（`.gitignore`/`.ignore`/`.rgignore` 解析，`walk_files` 依祖先規則跳過）＋**串流逐行掃描**（不 `read_text` 整檔）；`DEFAULT_SEARCH_MAX_FILE_BYTES 1MB→256KB`。 |
| **L2** | 圖片 data URL 隨 R2 歷史 stub 不再每輪重放。 |

**量化**：120k 附件重放 **31,635 → 99 tokens/輪（-99.7%）**；40k 讀取重放 token 封頂 ~4000。

**驗證**：`tests/` pytest **203 passed**（+15 新測試：read 串流/binary/分頁、附件 stub、rg+fallback/ignore、token 截斷）。`stress_test.py` **120 passed**。`selftest.py` 僅餘 2 項既有失敗（與本改動無關）。

### 2026-08-27 — 摘取／抽取過大（E1 / E2，一步到位）

改動檔案：`memory/auto_extract.py`、`memory/memory_manager.py`、`tests/test_memory_extract.py`（新）。

| 項目 | 實作內容 |
|---|---|
| **E1/E2** | Dream 改 **單一合併 LLM call**：新增 `run_extract_and_merge`（`EXTRACT_MERGE_PROMPT` 一個 prompt 同時「抽取新事實 + 就地合併既有 blocks」），回傳最終 blocks。**4 次主模型 call → 1 次**（外加每日一次的 session note）。 |
| **E2** | **移除 `_verify_preservation` 的 LLM verify**：guardrail 全改**規則**——覆蓋率文字相似度（`_is_covered_by`，`max_prior_loss`）+ 大小預算（`inject_char_limit`）；被拒則 append-only fallback（`write_auto_facts` 新事實保底，無遺失）。 |
| **E1** | `extract_model` 沿用既有 `_memory_extract_llm()`（main.py:1104，provider id → `build_extract_llm` 建小模型）；transcript 維持 12k chars 有界。 |
| **清理** | 刪除孤兒/廢棄代碼：`run_auto_extract`、`run_consolidation`、`_verify_preservation`、`_parse_index_array`、`_parse_candidates`、`_parse_array_slice`、`EXTRACT_PROMPT`、`CONSOLIDATE_PROMPT`、`_parse_consolidation`、`_stage_candidates`、`_consolidate_now`、`_pending_candidates`。`_parse_blocks_and_new` 強化為唯一 parser（dict/裸陣列/單引號皆容）。`selftest` 改測新路徑（`run_extract_and_merge` + 真實 `_dream_async`）。記憶模組淨刪 **~408 行**。 |

**量化**：dream LLM call 數 **4 → 1**；guardrail 不再有第二個 LLM verify call（測試以 counting-LLM 斷言 `calls == 1`）。

**驗證**：`tests/test_memory_extract.py` 新增 5 組測試（單 call 合併 / 覆蓋率拒回 + 無第二 call / 預算拒回 / unparseable 降級 / 無 transcript 跳過）；`tests/` pytest **208 passed**。`selftest.py` **247 PASS**（僅餘 2 項既有失敗）。`stress_test.py` **120 passed**。

### 2026-08-27 — 壓縮精簡不正確（C1–C4，依確認決策）

改動檔案：`sessions.py`、`agent/runtime.py`、`main.py`、`agent/middleware/context_compaction.py`、`agent/middleware/base.py`、`agent/core.py`、`agent/middleware/__init__.py`、`tests/test_compaction_persistence.py`（新）。

| 項目 | 實作內容 |
|---|---|
| **C1** | 摘要狀態**持久化到 session**：`Session` 新增 `context_summary` / `context_compact_count` / `context_summarized_fingerprints` + `update_compaction()`。runtime 在 `updates` stream mode 捕捉壓縮狀態 → `done` event 附 `compaction{summary,count,fingerprints,failed}` → main.py `session_store.update_compaction()`。turn 開始 `_compaction_state()` 讀回 → `runtime.stream(compaction_state=…)` → 初始 inputs 設 `context_summary`/fingerprints **並 prepend `先前对话摘要` HumanMessage**（`_prepend_compaction_summary`，對齊 codex replacement-history / opencode filterCompacted）。**anchored update 跨輪累積、fingerprint 跨輪去重、goal 續跑不再每輪全量重壓縮**。 |
| **C3** | `_summarizer_candidates` **簡化為只用主模型（= 使用者預設模型）**，移除多 provider fallback 鏈（依決策，不另設壓縮模型；codex/opencode 同）。摘要失敗 → `_compact_sync/_compact_async` 回傳 `context_compact_failed=True`（同時仍 trim 保底）→ main.py 於 `done.compaction.failed` 記 log + `done.compaction_notice`；**前端**：`App.tsx` 主 done handler 收到 `event.compaction_notice` → `window.alert` 提示更換模型（`types.ts` done 事件型別新增 `compaction_notice?`）。 |
| **C4** | `_trim` 改用 `_truncate_message_to_tokens`（`truncate_to_token_budget` token 精確、CJK/拉丁/base64 皆準），移除 `TRUNCATE_CHARS_PER_TOKEN` 常數與 `_truncate_message` 函式（孤兒清理）。 |
| **C2** | `SUMMARY_INPUT_MAX_TOKENS 32_000 → 20_000`（對齊 codex `COMPACT_USER_MESSAGE_MAX_TOKENS`）；O1 已根治 persist 雙重截斷。 |

**驗證**：`tests/test_compaction_persistence.py` 6 組（session 持久化 round-trip / runtime 重注入 / 摘要用預設模型 / 摘要失敗 flag + trim / token 截斷有界 / C2 常數對齊）。`tests/` pytest **214 passed**。`selftest.py` **245 PASS**（僅餘 2 項既有失敗）。`stress_test.py` **120 passed**。`pyflakes`：改動檔案無新增孤兒。

### 2026-08-27 — Token 計數不正確／不一致（T1–T4，依確認決策）

改動檔案：`agent/core.py`、`context.py`、`agent/runtime.py`、`tests/test_token_counting.py`（新）、`memory/selftest.py`（預算斷言同步）。

| 項目 | 實作內容 |
|---|---|
| **T1** | **移除 `CHARS_PER_TOKEN`（3.5）**；`context_budget_chars` 改用 `LATIN_CHARS_PER_TOKEN`（3.8，與估算器單一常數）。char 預算僅作顯示鏡像（決策已全走 `budget_tokens`）；`selftest` 預算斷言改為由 `LATIN_CHARS_PER_TOKEN` 動態推導。 |
| **T2** | `_cjk_count` 擴充到 **8 個 CJK 區間**（Hangul Jamo、CJK 符號標點、假名、CJK Ext A、基本、韓文、相容表意、全形）＋**排除全形拉丁字母/數字**；密集文字不再被當 Latin 低估。 |
| **T3** | 新增 `_normalize_usage_total`（`input + cache_read`，支援 Anthropic `input_token_details.cache_read` 與 OpenAI `prompt_tokens_details.cached_tokens`）；runtime `_fold_calibration` 改用 cache-inclusive actual——校準 EMA 不再被 prompt-cache 拉低（對齊 codex/opencode「provider usage 為權威」）。 |
| **T4** | `BASE64_MIN_RUN 256→128`（仍須過熵檢查）；短 base64/小型 data blob 不再以 prose 計。 |

**驗證**：`tests/test_token_counting.py` 7 組（無獨立 3.5 常數 / char 預算同比率導出 / CJK 全形·假名·韓文·ExtA 計入 / 全形拉丁排除 / 密集文字成本更高 / cache-inclusive 兩種 key / 128 門檻）。`tests/` pytest **221 passed**。`selftest.py` **245 PASS**（僅餘 2 項既有失敗）。`stress_test.py` **120 passed**。`pyflakes`：無新增孤兒。

### 2026-08-27 — Prompt build 殘留收尾（A–D，依確認決策）

改動檔案：`agent/middleware/system_assembler.py`、`mcp/mcp_middleware.py`、`agent/system_prompt.py`、`agent/graph.py`、`tests/test_prompt_build.py`（新）。**刪除孤兒 `agent/tool_registry.py`**（`build_tool_context` 移除後全模組無引用）。

| 項目 | 實作內容 |
|---|---|
| **A** | **MCP 段不再被 prepend 到 system 最前**（mcp_middleware 移除 `system_message` 覆寫）；改由 `SystemAssembler` 以 fragment（priority 75，capabilities 後/workspace 前）組裝，**discuss 隱藏**。組裝順序回歸 `[behaviour, phase, capabilities, MCP, workspace, memory, skills]`——行為核心第一（對齊 codex developer-instructions 在前 / opencode instructions→mcp）。 |
| **B** | 行為 prompt 過時「use only the listed ones」（P1 移除目錄後的殘留）→ 改寫（docstring＋實際文案「the tools provided to you」）。 |
| **C** | `build_cw_system_prompt` 收斂為**行為-only**（移除 `tools/workspace/include_workspace/include_tools` 參數）；刪 `build_tool_context`（孤兒，P1 deprecated）＋ `MAX_TOOL_CHARS`；**整檔刪除孤兒 `tool_registry.py`**；graph.py 呼叫同步。 |
| **D** | base 提取支援 **list-content**（`_system_text`：`[{"type":"text",...}]` 併成文字；`.text` 為空串的行為核心遺失邊緣收尾）。 |

**驗證**：`tests/test_prompt_build.py` 7 組（MCP 在行為/capabilities 後、discuss 隱藏、MCP middleware 不再覆寫 system_message、無 stale「listed ones」、行為-only、`build_tool_context` 已移除、list-content base）。`tests/` pytest **227 passed**。`selftest.py` **245 PASS**（僅餘 2 項既有失敗）。`stress_test.py` **120 passed**。`pyflakes`：無新增孤兒。

### 2026-08-27 — 八–十三 分階段開發（P0-A → P2-B，全量）

改動檔案：`agent/runtime.py`、`agent/graph.py`、`agent/core.py`、`agent/middleware/{context_compaction,loop_guard,context_guard,message_processor,system_assembler}.py`、`workspace.py`、`workspace_controller.py`、`sessions.py`、`agent/prompts.py`、`main.py`、`tests/test_runtime_cache.py`、`tests/test_phase8_13.py`（新）。

| 階段 | 內容 |
|---|---|
| **P0-A W1 編譯快取** | `WorkspaceRegistry`（path→穩定物件 + `begin_turn()` 重置）；middleware `reset_per_turn()`（Summarization 恢復砍半預算 / Steer 清注入 + 每 turn 注入 emit / SystemAssembler 清快取）；`steer_emit` 移出 build 參數；`AgentRuntimeRegistry` 每 session LRU 快取（含 referenced_sessions，`delete_session` evict）；`_compiled_graph` 按 (work_mode/language/autonomy/references/web/browser) 快取；`turn_index` 移入穩定 audit-context 於呼叫期讀取。D1/D2/N5 消除。 |
| **P0-B W4** | 摘要 **memoize**（segment fingerprint+previous_summary → 摘要，stall 重試不再重跑摘要 LLM，D4）；**分類重試**（`_classify_retry_error`：overflow / rate_limit / transient / fatal + 指數退避，N3）。 |
| **P1-A W3** | 死迴圈改 **autonomy-aware 權限門**：只封鎖「重複的那支工具」，其餘工具保留；guarded/supervised 引導 `ask_user`，autonomous 自我改策略；不再剝光全部工具。 |
| **P1-B W5** | D3 `_merge_event_parts` id→index O(1)；D5 `_clean_final_content` 合併 plan-leak+echo 且比對全部 plan 片段；S2 確認 `get_goal` 已在 read-only；S3 guard S4 保留最近使用的 MCP 工具；V1 `CONTEXT_SAFETY_FACTOR 0.75→0.9`；L3 `MAX_COMMAND_TIMEOUT 60→300`、預設 60 + `run_command(background=true)` + `run_command_status` 工具；N4 標題改**純規則**（移除主模型 chat call）；L1 由既有 `tool_end.files` diff 回饋涵蓋。 |
| **P2-A W2** | `loop_reason` state 欄位（repeated/degenerate/overflow/hitl/final）+ done 事件帶出；`MAX_STEPS_PROMPT=200` 顯式 recursion_limit。 |
| **P2-B W6-P1** | `SessionStore.replace_assistant_parts` + main.py 於 `tool_end` 邊界以累積 merged parts 增量落盤（中斷可回放 partial reply）。 |

**驗證**：`tests/test_runtime_cache.py`（6：快取 key/預算恢復/steer 重置/assembler 清快取/workspace 穩定/controller 穩定）、`tests/test_phase8_13.py`（4：merge O(1)/規則標題/後台命令/status）。`tests/` pytest **237 passed**。`selftest.py` **245 PASS**（僅餘 2 項既有失敗）。`stress_test.py` **120 passed**。`pyflakes`：無新增孤兒（清理 `_strip_compaction_echo`/`_title_system_prompt`/`_default_title_from_message` 未用 import）。

### 2026-08-27 — 完整驗收（45 項全量核對 + 2 項既有失敗根治）

**逐項驗收**：以自動化腳本核對全部 45 項的程式碼實作（66 個檢查點，全過）——含 M1 優先級、M2 `scan_scoped`/render 快取、P1 目錄移除、P2 desc≤650、P3 `_ws_cache`、P4 12k/catalog cap、R1 串流/binary/40k、R2 `inline_attachments`、R3 rg+ignore/256KB、E1 `run_extract_and_merge`、E2 verify 移除、C1 session `context_summary`/`update_compaction`/prepend、C2 20k、C3 預設模型+`context_compact_failed`、C4 token 截斷、T1 無 3.5、T2 ≥8 區間、T3 `_normalize_usage_total`、T4 128、A MCP 組裝、B/C 行為-only、D `_system_text`、D1 編譯快取/registry/`reset_per_turn`、D3 `tool_index`、D4 memoize、D5 `_clean_final_content`、S1 權限門、S2 `get_goal`、S3 保留最近 MCP、L3 300/60+後台+`run_command_status`、V1 0.9、V5 16k、N1 `loop_reason`+MAX_STEPS=200+done 帶出、N2 `replace_assistant_parts`、N3 分類重試、N4 規則標題。

**驗收期間修復**：
1. **`_cap_summary` off-by-one**（既有失敗「summary capped to budget: 4097」）：估算器 `int()` 截斷使 `est(a)+est(b) ≠ est(a+b)`；二分解改為對**最終形式（body+marker）**直接評估 → 精確 ≤4096。
2. **selftest telemetry 錯誤預期**（既有失敗「context_usage telemetry emitted」）：`context_usage` 由 **ContextGuardMiddleware**（`request.runtime.stream_writer`）發出、非摘要 middleware；修正 selftest 期望 + 新增 `tests/test_context_guard.py::test_guard_emits_context_usage_telemetry` 補覆蓋。
3. **P2-B 重複寫入防護**：`SessionStore.append_message` 改為**按 id 冪等**（已存在的 assistant 訊息更新而非追加），`replace_assistant_parts`(tool_end) + `_persist_assistant`(done) 不再產生同 id 重複訊息。

**驗收結果**：`tests/` pytest **238 passed**。`selftest.py` **247 PASS、0 FAIL**（2 項既有失敗根治）。`stress_test.py` **120 passed**。前端 `App.tsx`/`types.ts`（C3 `compaction_notice`）`tsc --noEmit` **0 錯誤**。`pyflakes`：無新增孤兒（餘項皆既有）。

### 2026-08-27 — 前端↔後端合約審計與源頭改造（13 區開發後斷裂檢查）

**審計**：比對後端 SSE 事件詞彙（runtime.py/main.py）與前端處理清單（App.tsx/chatService.ts）——全數對齊；SSE 解析無 schema 驗證（新欄位安全）；session-detail/API 回應僅新增欄位（向前相容）。

**發現並源頭改造的斷裂**：
1. **`tool_end` 事件輸出從 2000 → 全量（O1 引入）**：O1 把持久化輸出改為全文（128k fuse），但同一個 `part["output"]` 同時用於 live `tool_end` SSE 事件與 `done.parts` → 前端工具泡泡被 40k+ 輸出淹沒。**源頭改造**：拆分顯示/持久化——`output` 維持 2000 chars 顯示上限（前端合約還原），新增 `output_full`（全文，128k fuse）供 `_parts_to_conversation` 重放（token 截斷至 4000）與 session 持久化。`_merge_event_parts` 同步轉載 `output_full`。O1「全文持久化、重放時截斷」目標不變，僅線上/顯示體積還原。
2. **`done` 事件新欄位未同步前端型別**：`loop_reason` / `compaction` 加入 `frontend/src/types.ts` 的 `done` 事件（`compaction_notice` 先前已加）。
3. **`context_usage.budget_chars: 0`**：guard telemetry 硬編 0 → 改為 `limit_tokens × LATIN_CHARS_PER_TOKEN` 衍生值（前端 chars fallback 不再為 0）。

**驗證**：`tests/test_parts_reconstruction.py::test_tool_result_replays_output_full_not_display_cap`（重放全文、非顯示截斷）。`tests/` pytest **239 passed**。`selftest.py` **247 PASS、0 FAIL**。`stress_test.py` **120 passed**。前端 `App.tsx`/`types.ts` `tsc --noEmit` **0 錯誤**。

### 2026-08-27 — 上游/主次關係連路審計與修復（13 區改動後）

**審計**：逐鏈核對 13 區改動的上下游依賴（主 agent graph ↔ worker 子代理 ↔ delegation 委派 ↔ memory dream ↔ compaction 持久化 ↔ 重試 ↔ HITL/resume ↔ 增量落盤 ↔ 前端）——含共享 builder 的呼叫方簽名、跨輪 runtime 狀態、checkpointer 一致性、目標路徑一致性。

**發現並修復的斷裂**：
1. **`delegation.py` 傳已刪除的 `turn_index=1`（W1 上游主鏈斷裂）**：`build_workspace_tools` 的 `turn_index` 參數在 W1 已移除（移入穩定 audit-context），但 `Delegator._run_sub_turn`（delegation.py:374）仍傳 → **任何委派/團隊 spawn 都會 `TypeError`**。移除該參數（工具端已預設 `audit_context.get("turn_index", 1)`）。補回歸測試 `test_delegation_build_workspace_tools_kwargs`。
2. **`_steer_buffer`/`_delegation_buffer` 跨輪殘留（W1 快取 runtime 的連路）**：W1 前 runtime 每 turn 新建（buffer 天然清空）；快取後每 turn 復用 → 上一輪未 drain 的通知幀洩入下一輪。於 `_stream` 起點（`begin_turn`/`reset_per_turn` 旁）清空兩 buffer（steer 已注入對話，通知幀可安全丟棄）。
3. **P2-B 增量緩衝只累積 `tool_end`（crash 恢復不完整）**：`_partial_parts` 改為累積全部流事件（delta/reasoning/plan/tool_start/delta/end），於每次 `tool_end` 以累積 merged view 冪等寫入——中斷可回放的 partial reply 含完整工具 input/output_full。

**核對無斷裂的鏈**：dream `_memory_target_rel` = memory 工具 `_resolve_memory_target` = `write_auto_facts` = 記憶索引路徑（一致）；W1 cached graph 的 checkpointer = 共享 JSON saver（一致）；中斷輪 checkpoint 保留（resume 不受快取影響）；`_force_compact` 預算砍半由 `reset_per_turn` 每輪還原；worker/委派工具集與新簽名相容；無被移除符號殘留（`build_tool_context`/`run_consolidation`/`_verify_preservation`/`TRUNCATE_CHARS_PER_TOKEN`/`CHARS_PER_TOKEN` 等 0 程式碼引用）。

**驗證**：`tests/` pytest **240 passed**。`selftest.py` **247 PASS、0 FAIL**。`stress_test.py` **120 passed**。前端 `tsc` **0 錯誤**。

### 2026-08-27 — 變量衡量/定義/引用審計（重複定義、多處定義、語意不一 → 唯一真源）

**審計**：掃描 13 區改動引入/觸及的常數與符號——檢查重複定義、多處定義（同值不同源）、重複/錯誤/缺少引用、語意混亂。逐項收斂為**唯一真源**並補全引用。

**修復**：
1. **`DEFAULT_AGENT` / `DEFAULT_AGENT_NAME` 三處定義**（memory_manager / memory_discovery / agent.core 各寫 `"default_agent"`）→ 唯一真源移至 `memory/layout.py`（agent 資料夾名的佈局常數）；memory_discovery/memory_manager（alias `DEFAULT_AGENT`）與 runtime 直接引用；core 移除重複定義（避免 re-export 未用）。
2. **命令 timeout 三處定義**（workspace.py `DEFAULT/MAX_COMMAND_TIMEOUT_SECONDS` vs core.py `RunCommandArgs Field(default=60, le=300)` vs graph.py run_command 預設 60）→ 唯一真源在 workspace.py（執行器強制 `safe_timeout`）；core.py `Field(default=DEFAULT_COMMAND_TIMEOUT_SECONDS, le=MAX_COMMAND_TIMEOUT_SECONDS)`、graph.py 預設皆引用之（不再魔數）。
3. **`loop_reason` 值集散落 3 處字串**（loop_guard/runtime/core 註解）→ 集中為 core.py `LOOP_REASON_*` 常數；loop_guard（repeated/degenerate）、runtime（overflow/hitl/final）引用；前端 types.ts union 同步（值集一致）。
4. **標題規則重複定義**（sessions `_title_from_message` 簡單截斷 vs prompts `_default_title_from_message` N4 規則，行為不一）→ 唯一真源 = prompts 的 N4 規則；sessions 的 `_default_title_from_message` 改為委託（auto-title 與 `/sessions` 端點標題一致）。
5. **前端 `CHARS_PER_TOKEN=3.5` vs 後端 `LATIN_CHARS_PER_TOKEN=3.8`**（語意不一）→ 前端改 3.8（chars fallback 與後端估算一致）。
6. **`MAX_TREE_ENTRIES`(總量) vs `MAX_DIR_ENTRIES`(單目錄兄弟)** 語意混淆 → 註解明確兩者不同。

**核對無問題**：`SUMMARY_*`/`TOOL_OUTPUT_*`/`SYSTEM_FIXED_BUDGET_TOKENS`/`MAX_TOOL_DESCRIPTION_CHARS`/`MAX_STEPS_PROMPT`/`RUNTIME_CACHE_MAX`/`MAX_IMAGES_PER_PROMPT`/`SKILL_BODY_MAX_CHARS`/`SKILLS_CATALOG_MAX_TOKENS` 皆單一定義+正確引用；`context_usage`/`done.compaction`/`loop_reason` 前端型別與後端 shape 一致。

**驗證**：`tests/` pytest **240 passed**。`selftest.py` **247 PASS、0 FAIL**。`stress_test.py` **120 passed**。前端 `tsc` **0 錯誤**。`pyflakes` 乾淨。

### 2026-08-27 — 前端回報兩 bug 修復

**Bug 1 — 發送請求時 `cannot assign to field 'description'`**：P4 的 `format_skills_prompt_bounded`（skill 目錄超 1500-token 預算的裁剪路徑）對 `SkillEntry`（`@dataclass(frozen=True)`）用 `copy.copy` + 賦值 → 凍結 dataclass 不可賦值。**修復**：改用 `dataclasses.replace(s, description=_clip_description(...))`。

**Bug 2 — 重試時 `Provider qwen3.6-35b is not enabled or not found`**：W1 把 `AgentRuntimeRegistry.get_stream_runtime` 簽名改為 `(mode, session_id, provider_id, model, workspace, …)`（session_id 第 2 參數），但 `resume_interrupt`（runtime.py:1257）與 `_stream_runtime_from_context`（:1273）仍以舊位置順序 `(mode, provider_id, model, workspace)` 呼叫 → `provider_id` 收到 **model 字串**（qwen3.6-35b）→ `find_enabled(model名)` 失敗。**修復**：兩處補 `session_id`（context 已含 `session_id`）；`rerun_stream` 的 context 也補 `session_id`（讓 retry/regenerate 可命中 runtime 快取）。

**驗證**：`tests/test_phase8_13.py` 新增 `test_skills_catalog_clip_frozen_dataclass` + `test_resume_runtime_positional_mapping`。`tests/` pytest **242 passed**。`selftest.py` **247 PASS、0 FAIL**。`stress_test.py` **120 passed**。前端 `App.tsx`/`types.ts` `tsc` **0 錯誤**（chatService.ts 2 錯誤為既有）。

### 2026-08-27 — Recursion limit 200 撞限修復（N1 過嚴）

**報錯**（session e553ecce…）：`Recursion limit of 200 reached without hitting a stop condition`（GRAPH_RECURSION_LIMIT）。**根因**：N1 把 `MAX_STEPS_PROMPT=200` 作為 `agent_run_config` 的 `recursion_limit`——200 對真實長任務（>200 次不同工具呼叫的 read/edit/run/fix 循環）太低，撞限直接硬錯。

**修復（源頭）**：
1. **`MAX_STEPS_PROMPT 200 → 2000`**：它是**後備保險**（runaway 迴圈由 RepeatedToolCall/退化偵測在遠早於此就攔住），不是主要終止機制；須寬鬆到真實長任務不會誤撞。
2. **撞限優雅收尾**：runtime 主 stream 與 resume 路徑新增 `except GraphRecursionError`——設 `loop_reason="step_cap"`、附使用者提示（「已達單次任務的最大工具步驟數上限，已安全停止」）、落入正常 `done` 發射，**不再把原始 langgraph 錯誤丟給前端**。

**驗證**：`tests/test_phase8_13.py` 新增 `test_step_cap_generous_backstop_and_graceful`（MAX_STEPS≥1000、config 帶 recursion_limit、兩處 GraphRecursionError 捕獲）。`tests/` pytest **243 passed**。`selftest.py` **247 PASS、0 FAIL**。`stress_test.py` **120 passed**。

### 2026-08-27 — IdleLoopMiddleware：無上限 + 進度感知卡死守門（N1 改版）

**設計（依確認）**：**無 step 上限、不設可配置**——移除 `agent_run_config` 的 `recursion_limit`（回歸 create_agent 內建 9999 絕對硬界）；runaway 迴圈由守門層治理。

**IdleLoopMiddleware**（`middleware/loop_guard.py`，RepeatedToolCall 之後、ContextGuard 之前）：
- **卡死判據**（每步）：`outputs_hash`（工具輸出 canonical hash）∈ 最近窗口輸出集合（**無新資訊**）**或** `signature`（工具名+args+輸出）∈ 最近窗口 signature 集合（**規律重複**）。
- **三態**：
  1. **warn**：滑動窗口**尾 10 步** ≥7 步卡住 → 注入「你似乎卡住了…請改變策略」（非終止，一次）。
  2. **硬停**：warn 後 **20 步限值**內尾 10 仍 ≥7（`len(history)>=20` 閘）→ `tools=[]` 剝工具 + `loop_reason="idle_hard"` + 停止/總結訊息。
  3. **恢復**：進展步把卡住旗標滑出尾 10 窗口（<7）→ `warned` 重置、清空窗口、**無上限繼續 + 重新 7/10 監測**。
- `reset_per_turn()` 清窗口（W1 快取相容）；`LOOP_REASON_IDLE="idle"` / `LOOP_REASON_IDLE_HARD="idle_hard"`（前端 types.ts done union 同步）。
- 覆蓋 RepeatedToolCall 的缺口：**變體循環**（不同 args/工具/重新規劃但無新資訊）在 step 計數前被精準攔截；`_cap_summary` 等既有守門不變。

**驗證**：`tests/test_phase8_13.py` 4 組（連續卡死→硬停 / 滑出恢復→無上限 / 中間卡住→warn 後恢復 / 5 步早段卡住→不觸發）+ `test_no_step_cap_and_graceful_recursion_backstop`。`tests/` pytest **247 passed**。`selftest.py` **247 PASS、0 FAIL**。`stress_test.py` **120 passed**。前端 `tsc` **0 錯誤**。

### 2026-08-27 — 真機會話審查 + 工具 input 捕獲修復（session 81d44c5c）

**真機會話審查**（「查找1個bug並修復」）：
- **13 區成效全確認**：N4 規則標題（`查找1個bug，並修復`）、T3 cache-aware 校準（factor 1.135、17900→20313）、O1 顯示/持久化拆分（output≤2000 + output_full 全文）、R1 有界讀取、P2-B 增量 parts（140 parts）、D5 內容清理、沙箱防禦（`Write denied: /tmp/test-preload.ts is outside the workspace sandbox`）。
- 長 turn（140 parts、~60+ 工具呼叫）無 IdleLoopMiddleware 誤觸發、無 Recursion limit——無上限+進度守門設計正確。

**Bug 1（既有非本次回歸）修復 — 工具呼叫 `input` 捕獲為空**：
- 會話中 `write_file` part `input=''`（file_path/content 未持久化）；根因：續段工具 args chunk 的 index 未註冊（首 chunk 無 index）時被靜默丟棄（core.py:802 `continue`）→ `tool_state["input"]` 為空。
- 引入 commit `056badb8`（本 session 之前），非 13 區回歸。
- **修復**：`_message_chunk_events` 續段路由加 **name 回落**——index 未知時路由到「最近 running 且同 name」的工具（單工具常見情形），不再丟棄；多工具同 index 註冊路徑不變。
- **驗證**：`tests/test_phase8_13.py` 新增 `test_tool_input_capture_without_index_continuation`（無 index 續段 args 累積）+ `test_tool_input_capture_with_index_routing_still_works`（並行 index 路由不退化）。`tests/` pytest **249 passed**。`selftest.py` **247 PASS、0 FAIL**。`stress_test.py` **120 passed**。

### 2026-08-27 — 插話（steer）功能 bug 修復（session 81d44c5c 真機復現）

**症狀**：任務 2 後段插話失敗——訊息卡在佇列、5 次 `/chat/interject` 全部 409、僅 UI 閃一下，~1 分鐘後才以普通訊息送出。

**根因（log + 前端追蹤確認）**：
1. **插話按鈕未依 stream 活動狀態閘控**（直接原因）：`TodoBlock` 插話按鈕無 disabled、`interjectQueuedMessage` 呼叫 `/chat/interject` 前不檢查該 session 是否真有在飛的流 → 後端任務結束後每次點擊都 409 → 移除再重排（「閃一下」）。
2. **stream 結束但訊息未 settle → `isThinking`/`busy` 卡 true**：`settleAssistantMessage` 只 settle `status==='running'`（`waiting` 永不 settle）；done 幀丟失 / id 不匹配 / SSE 早斷時訊息卡 running → 佇列自動送出被卡、插話按鈕仍可點 → 409 迴圈。
3. **競態**：pendingSteers 在 interject 成功前就加入 → auto-continue effect 可能重複送出。

**修復（全部前端）**：
1. **插話按鈕閘控**：TodoBlock 加 `streamActive` prop，非串流時 disabled（`interject_queued_disabled` i18n 11 語系）。
2. **`interjectQueuedMessage` 預檢**：無活動流（無 controller 且無 running/waiting 訊息）→ 直接普通送出，不呼叫 interject。
3. **409 回退**：interject 失敗不再靜默重排迴圈 → 以普通訊息送出。
4. **settle 覆蓋 waiting**：`settleAssistantMessage` 與 `resolvePendingRequest` 失敗 catch 都 settle 卡住的 waiting → done/error。
5. **卡死 watchdog**：30s 週期檢查——running 訊息無活動流 controller → 強制 settle（reconcile 後端），防 isThinking/busy 永久卡住。
6. **競態修復**：pendingSteers 僅在 interject 成功後加入。

**驗證**：`frontend` `tsc --noEmit` App.tsx/TodoBlock **0 錯誤**（5 個 TS 錯誤皆既有）；i18n 11 語系 JSON 有效；後端 pytest **249 passed**（無後端改動）。

---

## 追蹤約定

- 每修復一項：在總表把狀態改為 `☑`，備註欄填 commit / PR 連結與驗證方式。
- 決定不修：改為 `➖` 並在備註記錄原因（產品決策／成本過高／與現有機制衝突）。
- 文件頭部「追蹤狀態」欄位：`☐ 待修 / ◐ 進行中 / ☑ 已修 / ➖ 決定不修`。

### 修訂紀錄

| 日期 | 變更 |
|---|---|
| 2026-08-27 | 初版：全環路隱性問題盤點（45 項），基於與 codex / opencode-dev 對照 |
| 2026-08-27 | 記憶注入 M1–M5 已修復（方案 A+B 對齊 codex），含實作紀錄與驗證 |
| 2026-08-27 | Prompt 注入 P1–P5 / B1–B3 / V5 / O2 已修復（檔次 1+2：純減法 + SystemAssembler），含實作紀錄與量化對比 |
| 2026-08-27 | 檔案讀取 R1–R3 / O1 / L2 已修復（源頭優化：有界串流 + 附件 inline-once + rg/ignore），含實作紀錄與量化 |
| 2026-08-27 | 摘取 E1/E2 已修復（一步到位：dream 單一合併 call + 規則 guardrail 移除 verify LLM），含實作紀錄 |
| 2026-08-27 | 壓縮 C1–C4 已修復（摘要 session 持久化 + 跨輪重注入 / 預設模型 + 失敗提示 / token 精確截斷 / 輸入預算），含實作紀錄與孤兒清理 |
| 2026-08-27 | Token 計數 T1–T4 已修復（單一常數 / CJK 擴充 / cache-aware 校準 / base64 門檻），含實作紀錄 |
| 2026-08-27 | Prompt build 殘留收尾（A–D：MCP 段組裝位置 / 行為-only / 刪 tool_registry 孤兒 / list-content base），含實作紀錄 |
| 2026-08-27 | 八–十三 全量分階段開發（P0-A 編譯快取 → P2-B 增量落盤），含實作紀錄 |
| 2026-08-27 | 完整驗收：45 項逐項核對通過；根治 2 項既有 selftest 失敗（_cap_summary off-by-one、telemetry 錯誤預期）；append_message 冪等化 |
| 2026-08-27 | 前端↔後端合約審計：tool_end 顯示/持久化拆分（output/output_full）、done 型別同步、context_usage budget_chars 修正 |
| 2026-08-27 | 上游/主次關係連路審計：修復 delegation turn_index 斷裂、steer/delegation buffer 跨輪洩漏、P2-B 增量緩衝強化 |
| 2026-08-27 | 變量衡量/定義/引用審計：DEFAULT_AGENT·timeout·loop_reason·標題規則 收斂唯一真源；前端 CHARS_PER_TOKEN 對齊 3.8 |
| 2026-08-27 | 前端回報兩 bug：skill 凍結 dataclass 裁剪（dataclasses.replace）、get_stream_runtime 位置參數錯位（resume/rerun 補 session_id） |
| 2026-08-27 | Recursion limit 200 撞限修復：MAX_STEPS_PROMPT→2000 後備值 + GraphRecursionError 優雅收尾（step_cap） |
| 2026-08-27 | N1 改版：移除 step 上限（無上限），新增 IdleLoopMiddleware 進度感知卡死守門（warn→20 步限值硬停→滑出恢復無上限） |
| 2026-08-27 | 真機會話審查（13 區成效全確認）+ 修復工具 input 捕獲為空（續段 chunk name 回落路由） |
| 2026-08-27 | 插話（steer）bug 修復：按鈕閘控、stream-active 預檢、409 回退普通送出、settle waiting、卡死 watchdog |
| 2026-08-28 | goal 能力修復（session 41d76f8b 復現樣本）：① idle-stop 首輪純文字不再直接 break → 注入 `_IDLE_NUDGE` 引導模型調 `update_goal(complete/blocked)` 續跑一輪；連續 2 純文字輪才停並置 `paused`（防前端自動續跑無限循環，GoalCard 顯示「繼續」供介入）② 每輪記帳後 `_emit_goal_updated`（前端 token/time 不再 0/0）③ 完成輪改調 `update_goal(status='complete')` 發出 done + 自動撤卡 ④ `update_goal_round(round_index+1)`（round 不再停 0，blocked 審計 ≥3 輪生效）⑤ `tokens_used` 改用該輪 done event 實際 `prompt+completion`（不再膨脹 135959）⑥ 前端 `goal_stream_end` 時 goal 仍 active → 自動 `kickGoalContinuation` 續跑。目標 `_IDLE_NUDGE`/`idle_rounds`/`idle_nudge` 作用域修正（原誤入 `_current_history` 內層）。新增 `tests/test_goal_round_flow.py`（4 項：round 計數 / 帳務累積 / 完成信號模板 / nudge 線路）。
| 2026-08-28 | 降智根因（session 2d8080e8）：`prepare_agent_messages` 剝除 role="tool" 與 assistant 的 `tool_calls` 鍵 → 跨輪歷史只剩純文字敘述，qwen3.6-35b 模仿「只敘述不執行工具」（連續 3 輪「先查看…commit：」即停）。`_parts_to_conversation`（08-27 修復）重建的工具鏈被下游過濾器靜默丟棄。修復：`prepare_agent_messages` 原樣放行 tool 訊息與 tool_calls（純 tool_calls assistant 容許 content=None），LangChain `convert_to_messages` 正確轉 AIMessage(tool_calls)/ToolMessage。新增 `tests/test_prepare_agent_messages.py`（7 項，含 2d8080e8 turn-D 回歸：31 tool_calls/53 工具結果存活）。另修 2 個 HEAD 既存測試 bug：`test_idle_stop_infers_complete` 錯查 main.py（實際在 goal_prompts.py，引號不符）、`test_idle_loop_varying_stuck_hard_stops` 讀錯鍵（loop_reason 在 override.state 非頂層）。
| 2026-08-28 | 「Expecting value: line 1 column 1 (char 0)」修復：`_parts_to_conversation` 對 legacy 空/非法工具 input 產生空 arguments，修復後放行 tool_calls 使 LangChain `convert_to_messages` 對 `arguments:""` 執行 `json.loads("")` 拋 JSONDecodeError（2d8080e8 msg1 殘留 1 筆空 input）。加固：空/非 JSON/非物件的 arguments 統一落為 `{}`（保 dict，避免 args 欄位 pydantic 拒絕）。新增 `test_empty_input_tool_part_does_not_crash_convert` / `test_non_json_string_input_is_reserialized`。
