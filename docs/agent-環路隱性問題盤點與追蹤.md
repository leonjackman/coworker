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

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| P1 | agent/phase_gate.py:117-119、agent/system_prompt.py:182-253 | **工具 schema 與 system prompt 工具目錄重複付費**：工具 schema 本就隨每請求送給 provider；PhaseGate 又把一份「Available tools」目錄（上限 4000 chars）塞進 system prompt。同一份信息每請求付兩次 token。 | codex/opencode 工具清單就是 schema，不在 prompt 重複 | 移除或大幅縮小工具目錄；只保留「不可由 schema 表達」的指引 | 中 |
| P2 | agent/graph.py:337、557、240 | **工具 description 過長**：`memory`（~1400 chars）、`update_goal`（~1000）、`install_skill`（~900）、`run_command`（含 platform hint）。description 每字進 schema token 成本，且每回合重付。 | 主流 description ~200–400 chars，長指引移入 skill/memory 檔 | 瘦身 description；長指引改放文件，工具描述只留「什麼時候用 + 關鍵參數」 | 中 |
| P3 | agent/phase_gate.py:113-116、agent/system_prompt.py:50-120 | **workspace 目錄樹每 model call 重新 walk FS**：`build_workspace_context→build_workspace_tree` 遞迴 `iterdir`。同 turn 內目錄不變卻每 step 重建重發。 | opencode 每 turn 組裝；codex world-state diff | turn 級快取目錄樹；工具執行導致變更後才刷新 | 中(效能) |
| P4 | skills/skill_middleware.py:61、64-85 | **Activated skill 正文上限 80k chars**（≈25k–60k tokens）：`[skill:…]` 啟用後每個 model call 都注入 system prompt，單一技能可佔大半窗口。 | opencode Agent Skills 正文注入要小得多 | 上限降到主流量級（如 ~8–15k chars）；超過則只注入摘要+提示 load_skill | 中 |
| P5 | agent/graph.py:851-898 | **每 step 重複注入記憶+技能+workspace+工具**：四者皆由 wrap_model_call 在每個 model call 重算重發。 | opencode 每 turn；codex 每 step 但 diff | 同 turn 內結果快取；system 組裝改為一次 | 中 |

### 三、檔案讀取過大

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| R1 | workspace.py:42、agent/core.py:82-93、graph.py:144 | **`read_file` 單次可注入 50k chars**（`READ_FILE_MAX_CHARS=50_000`），code-heavy ≈ 38k tokens（~15% 的 256k 窗口），且該工具結果進 history 直到被清理。 | opencode 按行讀（預設 ~100–1000 行）+ 輸出截斷 | cap 改以 token 估算為準（如 ≤10k tokens）；或縮小預設 limit | 中 |
| R2 | agent/core.py:38、1108-1133、main.py:1957-1958 | **附件 120k chars 內聯且永久重放**：`MAX_ATTACHMENT_CHARS=120_000`（≈90k+ tokens）；文字附件內聯進 user 訊息 content 並**連同附件持久化**，之後**每一輪請求都重新內聯重發**。 | opencode 文字檔以 Read 工具按需讀 | 大附件只給摘要/路徑，模型用 read_file 按需讀；或限制僅當輪內聯 | 高 |
| R3 | workspace.py:34-35、1277 | **搜尋讀檔上限 1MB/檔**：`DEFAULT_SEARCH_MAX_FILE_BYTES=1_000_000`，逐行掃描 1MB 檔成本高（輸出僅 240 chars/行、80 結果）。 | codex/opencode 檔案搜尋有索引或更嚴的上限 | 上限降到 ~256KB；或對無命中行提前跳過 | 低 |

### 四、摘取／抽取過大

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| E1 | memory/memory_manager.py:388-407、auto_extract.py:29-44 | **自動記憶抽取用主模型跑**：`_llm_factory()` 預設建**主 provider 大模型**（`extract_model=""` fallback），每 dream 可觸發 extract + consolidate + session-summary 三次完整 LLM call。 | codex 記憶用輕量模型 | 預設用小型/便宜模型（`extract_model` 改為預設啟用）；限制 consolidate 既有檔大小 | 中 |
| E2 | memory/auto_extract.py:304-389 | **consolidation 輸入過大且多 call**：`{existing}` 整個 MEMORY.md（~4000 chars）+ 全部候選內嵌，每 dream 還可能跑 `_verify_preservation`（再一次 LLM call）——最壞 1 dream = 4 次 LLM call。 | — | 限制既有檔輸入長度；合併/簡化 preservation 檢查（僅在相似度全 miss 時才問模型） | 中 |

### 五、壓縮精簡不正確

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| C1 | main.py:1949、2216 vs runtime.py:687-698 | **壓縮摘要跨輪/跨 goal round 失效（架構性 bug）**：`context_summary` / `context_summarized_fingerprints` 只在 LangGraph checkpoint state；main.py 在**每 turn 開頭**（1949）與**每個 goal round**（2216）都 `forget_runtime_checkpoint`，且 session messages 不持久化摘要 HumanMessage。→ **每輪都從全量歷史重新壓縮、摘要永遠不累積**，長 goal 任務上下文無界增長、壓縮成本每輪重付。 | codex rollout 常駐、摘要持久化；opencode compaction marker 持久在 DB | 把摘要狀態持久化到 session（或新增可重放機制）；或 goal round 之間保留 checkpoint | 高 |
| C2 | context_compaction.py:41、414-433 | **摘要輸入被雙重截斷**：工具輸出先被持久化截到 2000 chars（O1），`_serialize_for_summary` 又截到 2000，摘要資訊量偏低。 | opencode 全文落盤後截斷 | 摘要讀原始全文（見 O1 改造） | 低 |
| C3 | context_compaction.py:50-96、424-433 | **摘要模型 fallback 鏈首選「使用者預設模型」**（可能最貴/最大），輸入 32k tokens。 | opencode compaction 用獨立小 agent/model | 摘要優先用小模型；限制輸入 | 中 |
| C4 | context_compaction.py:261-273、base.py:50 | **`_trim` 截斷倍率一刀切**：`TRUNCATE_CHARS_PER_TOKEN=1.5` 對拉丁文遠低於真實 3.8（砍過頭、損失內容），對 CJK 較安全。 | — | 截斷倍率按內容類別（CJK/Latin/base64） | 低 |

### 六、Token 計數不正確／不一致

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| T1 | agent/core.py:1168 vs context.py:63 vs base.py:50 | **多套 chars/token 常數互相矛盾**：`CHARS_PER_TOKEN=3.5`（字元預算）vs `LATIN_CHARS_PER_TOKEN=3.8`（估算）vs `TRUNCATE_CHARS_PER_TOKEN=1.5`（截斷）。同一內容「字元預算」與「token 估算」差 ~8%，meter（tokens）與 trim（chars）可能對是否超預算判斷不一致。 | 單一估算器 | 全部收斂到 context.py 單一估算器；字元預算僅作 telemetry | 中 |
| T2 | context.py:110-111 | **CJK 偵測範圍過窄**：`"一" <= ch <= "鿿"`（U+4E00–U+9FFF）不認**日文假名、全形標點（，。！？）、韓文** → 被當 Latin 以 3.8 chars/token 低估 token 數，可能超窗。 | — | 擴充 CJK 判定（含全形標點/假名/韓文區間） | 中 |
| T3 | context.py:422-469 | **校準受 prompt-cache 干擾**：`usage_metadata.input_tokens` 在啟用 prefix cache 的 provider 上可能不含 cache hit，比值長期被拉向 1.0，進而低估非 cache 請求。 | — | 校準只在非 cache 呼叫採樣（或用含 cache 的總 input 數） | 低 |
| T4 | context.py:79、165-204 | **short base64（<256 chars）以 prose 計**：`BASE64_MIN_RUN=256` 以下真實 base64 被當文字低估且不 scrub，仍送模型。 | — | 對 data URL header 一定以 base64 計（已做）；對長純 base64 降低門檻或加熵判定 | 低 |

### 七、請求 prompt build 不合理

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| B1 | phase_gate.py:120-123、skill_middleware.py:155-164、memory_middleware.py:61-69 | **系統訊息被 3+ middleware 各自整個覆寫**：PhaseGate→Skill→Memory 每個都 `SystemMessage(f"{section}\n\n{base_text}")`，把不斷增長的 system 反覆整段複製拼接。`base.py` 只讀 `.text`（content 若為 list 拿到空串）。 | opencode 一次組裝；codex base instructions 單源 | 改成「一次組裝 + middleware 只提供片段」；統一 content 讀取 | 中 |
| B2 | agent/graph.py:851-898、system_prompt.py:279-325 | **行為 prompt 被排最尾**：最終 system 順序 = memory→skills→phase+workspace+tools→behavior。最該被模型遵守的工具紀律/不空轉指引被長目錄稀釋。 | codex/opencode 行為放前、動態內容放後 | 調整組合順序：行為核心 → memory → skills → workspace → tools | 低 |
| B3 | agent/system_prompt.py:229-249 | **工具目錄對 MCP/未知工具退回完整 description**（可能上千字），與 P1 的雙份成本疊加。 | — | MCP 工具用短摘要；未註冊工具可不列 | 中 |

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
| L2 | agent/core.py:38、974、1105-1106 | **圖片 data URL 原樣內聯**：每張 ~1200+ tokens 估算（實際 qwen 720p 可達 1.1–1.6k），5 張 ≈ 6k+ tokens 固定注入，還隨 history 重放。 | — | 圖片進 history 時外置/降檔，或僅當輪保留 | 中 |
| L3 | agent/core.py:149、workspace.py:37 | **`run_command` timeout 上限 60s、預設 20s**：對 `npm install`/`build`/`test` 常不足，模型被迫反覆重跑，反而製造更多重複步驟。 | — | 放寬上限（如 120–300s）或對長任務支援非同步/後台執行 | 低 |

### 十一、不合理值

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| V1 | core.py:1167、1192-1208 | `CONTEXT_SAFETY_FACTOR=0.75`：trim 在 effective window 的 75% 就觸發，疊加 calibration≥1.0 偏保守（過早壓縮）。 | opencode 用 `window − reserved` 精確判定 | 評估調高 safety factor 或改精確判定 | 低 |
| V2 | base.py:36 | `SUMMARY_OUTPUT_TOKENS=4096`：與 opencode 一致，合理。 | 一致 | 保留 | - |
| V3 | context.py:69 | `PER_MESSAGE_OVERHEAD_TOKENS=4`：qwen 模板測過 ~4，合理。 | 一致 | 保留 | - |
| V4 | base.py:33 | `KEEP_RECENT_TOKENS=8000`：壓縮後 resident≈8k+摘要，與 opencode DEFAULT_KEEP_TOKENS=8000 對齊，合理。 | 一致 | 保留 | - |
| V5 | system_prompt.py:24-29、memory、skill_middleware | **固定注入總量無預算**：記憶 4000 + skills 目錄 + workspace 樹 6000 + 工具目錄 4000 ≈ 15k chars（~5k tokens）每次請求；疊加後可能爆窗。 | opencode 對 system 有隱含預算 | 為「固定注入」設總預算與優先級，防止疊加 | 中 |

### 十二、不合理順序

| ID | 位置 | 現況／隱性問題 | 對比主流 | 建議方向 | 嚴重度 |
|---|---|---|---|---|---|
| O1 | core.py:769-815 | **工具輸出持久化即截斷 2000 chars**：`_message_chunk_events` 在 `tool_end` 就 `output[:2000]` 存進 parts → 原始工具輸出在 session 就丟失，後續摘要/重放/回滾都只有 2000 chars。 | opencode **全文落盤**、只在轉模型時截斷 | 全文存 session（或落盤檔案），截斷只在模型轉換時 | 中 |
| O2 | graph.py:851-867 | **記憶 middleware 掛在 Skills 之後**，注入在 phase 之後，導致行為 prompt 墊底（B2）；記憶與技能的組合順序靠隱式鏈，不易維護。 | — | 顯式定義 system 組合順序（見 B2） | 低 |

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

1. **C1**：壓縮摘要跨輪存活（持久化 `context_summary` 到 session，或 goal round 間保留 checkpoint）。
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
| P1 | Prompt | 工具 schema 與工具目錄重複 | 中 | P1 | ☐ | |
| P2 | Prompt | 工具 description 過長 | 中 | P1 | ☐ | |
| P3 | Prompt | workspace 目錄樹每 call 重 walk | 中(效能) | P1 | ☐ | |
| P4 | Prompt | activated skill 上限 80k 過大 | 中 | P1 | ☐ | |
| P5 | Prompt | 每 step 重複注入記憶+技能+工具 | 中 | P2 | ☐ | |
| R1 | 讀取 | read_file 單次 50k chars 過大 | 中 | P2 | ☐ | |
| R2 | 讀取 | 附件 120k chars 永久重放 | 高 | P0 | ☐ | |
| R3 | 讀取 | 搜尋讀檔上限 1MB | 低 | P2 | ☐ | |
| E1 | 摘取 | 自動記憶抽取用主模型 | 中 | P2 | ☐ | |
| E2 | 摘取 | consolidation 輸入過大且多 call | 中 | P2 | ☐ | |
| C1 | 壓縮 | 壓縮摘要跨輪失效（架構 bug） | 高 | P0 | ☐ | |
| C2 | 壓縮 | 摘要輸入雙重截斷 | 低 | P2 | ☐ | |
| C3 | 壓縮 | 摘要模型首選主模型 | 中 | P2 | ☐ | |
| C4 | 壓縮 | trim 截斷倍率一刀切 | 低 | P2 | ☐ | |
| T1 | 計數 | chars/token 常數多套矛盾 | 中 | P2 | ☐ | |
| T2 | 計數 | CJK 偵測範圍過窄 | 中 | P2 | ☐ | |
| T3 | 計數 | 校準受 prompt-cache 干擾 | 低 | P2 | ☐ | |
| T4 | 計數 | short base64 低估 | 低 | P2 | ☐ | |
| B1 | Prompt build | system 被多 middleware 整段覆寫 | 中 | P2 | ☐ | |
| B2 | Prompt build | 行為 prompt 排最尾 | 低 | P2 | ☐ | |
| B3 | Prompt build | 工具目錄 MCP 退回全 description | 中 | P2 | ☐ | |
| D1 | 重複 | 每 turn 重建一切 | 高 | P1 | ☐ | |
| D2 | 重複 | goal 多輪每輪重複 | 高 | P1 | ☐ | |
| D3 | 重複 | `_merge_event_parts` O(n²) | 中(效能) | P2 | ☐ | |
| D4 | 重複 | stall 重試重跑 compaction 鏈 | 低 | P2 | ☐ | |
| D5 | 重複 | plan leak / compaction echo 清理重複 | 低 | P2 | ☐ | |
| S1 | 過度嚴格 | 死迴圈硬停剝光工具 | 中 | P2 | ☐ | |
| S2 | 過度嚴格 | discuss 不可用 goal 查詢 | 低 | P2 | ☐ | |
| S3 | 過度嚴格 | guard S4 靜默丟全部 MCP schema | 中 | P2 | ☐ | |
| L1 | 過度寬鬆 | guarded 對工作區內全放行 | 低(設計) | P2 | ☐ | |
| L2 | 過度寬鬆 | 圖片 data URL 永久內聯 | 中 | P2 | ☐ | |
| L3 | 過度寬鬆 | run_command timeout 上限偏小 | 低 | P2 | ☐ | |
| V1 | 數值 | safety factor 0.75 偏保守 | 低 | P2 | ☐ | |
| V5 | 數值 | 固定注入總量無預算 | 中 | P2 | ☐ | |
| O1 | 順序 | 工具輸出持久化即截斷 2000 | 中 | P1 | ☐ | |
| O2 | 順序 | 記憶/技能組合順序隱式 | 低 | P2 | ☐ | |
| N1 | 主流 | 迴圈決策顯式化缺失 | 中 | P2 | ☐ | |
| N2 | 主流 | 訊息級儲存 vs part 級 | 中 | P2 | ☐ | |
| N3 | 主流 | 重試策略單薄 | 中 | P2 | ☐ | |
| N4 | 主流 | 標題生成用主模型 | 低 | P2 | ☐ | |
| N5 | 主流 | 每 turn 冷啟動（重構主線） | 高 | P1 | ☐ | |

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

## 追蹤約定

- 每修復一項：在總表把狀態改為 `☑`，備註欄填 commit / PR 連結與驗證方式。
- 決定不修：改為 `➖` 並在備註記錄原因（產品決策／成本過高／與現有機制衝突）。
- 文件頭部「追蹤狀態」欄位：`☐ 待修 / ◐ 進行中 / ☑ 已修 / ➖ 決定不修`。

### 修訂紀錄

| 日期 | 變更 |
|---|---|
| 2026-08-27 | 初版：全環路隱性問題盤點（45 項），基於與 codex / opencode-dev 對照 |
| 2026-08-27 | 記憶注入 M1–M5 已修復（方案 A+B 對齊 codex），含實作紀錄與驗證 |
