# CoWorker 落地页（landing page）

本目录是 CoWorker 产品的独立落地页，**与项目源码完全隔离**：
- 不修改、不依赖 `frontend/`、`backend/`、`electron/` 任何文件
- 单文件、零依赖、无构建步骤
- 可直接双击打开，也可部署到任意静态托管（GitHub Pages / Netlify / Vercel / 自己的服务器）

## 文件

```
landing/
├── index.html   # 整个落地页（HTML + 内联 CSS + 内联 JS，中英双语）
└── README.md    # 本文档
```

## 打开方式

```bash
open landing/index.html
# 或
cd landing && python3 -m http.server 8080   # 建议用 http 方式预览
```

## 页面结构

| 区块 | id | 内容 |
| --- | --- | --- |
| 导航 | — | Logo、锚点导航、中英切换、CTA |
| Hero | `#top` | 主标语 + 纯 CSS 还原的产品窗口 mockup |
| 卖点条 | — | 本地优先 / 流式 / 审计 / 人审 / 多 Provider / 双语 |
| 工作流 | `#how` | Planner → Executor → Verifier → Summarizer 四步 |
| 特性 | `#features` | 8 张功能卡 |
| 安全 | `#safety` | Plan/Build、权限档位、HITL、JSONL 审计可视化 |
| 技术栈 | `#tech` | Electron / React / FastAPI / LangGraph 等 |
| 开始使用 | `#start` | CTA + 一键运行命令 + 复制按钮 |
| Footer | — | 说明、导航、资源链接 |

## 文案与事实依据

页面文案全部来自项目内的事实文档，务必保持同步：

- 定位：local-first 桌面编程 Agent，**single-agent MVP**（多 Agent 公司模式明确不在本期范围，页面未作任何越界声称）
- 管线：LangGraph planner → executor → verifier → summarizer
- 工具：默认 `search_files` / `read_file`；Build + Full Access 才有写入/替换/编辑/执行命令
- 安全：计划先批准再执行、HITL 命令审批、JSONL 审计、工作区硬边界
- 运行：`./coworker_desktop.command`（后端 127.0.0.1:9527）
- 品牌色：`#4F83F1`（深色底、白色文字 logo 风格）

> 注意：代码/文档有更新时，请同步核对本页文案（尤其「特性」「安全」两节的措辞是否还匹配当前实现）。

## 中英文切换

页面内置 `zh-CN` / `en` 两套文案，所有翻译集中在 `<script>` 里的 `I18N` 对象。
给任意元素加 `data-i18n="key"`（纯文本）或 `data-i18n-html="key"`（可含 HTML）即可接入。

## 待你替换的占位

1. **「下载试用」按钮**（`#downloadBtn`）：当前点击后平滑滚动到「本地运行指南」。等打包分发完成后，改成真实的安装包 / dmg 下载链接。
2. **Footer 的 GitHub 链接**：当前指向 `https://github.com/` 占位，请替换成真实仓库地址。
3. **README / MVP_SUMMARY 链接**：当前是相对路径，部署到静态站后建议换成文档站或 GitHub 上的绝对链接。
4. **favicon**：当前用内联 SVG（CW 字样）；正式发布时可替换为 `assets/brand/png/cw-icon-*.png` 导出的图标。

## 部署到 GitHub Pages（示例）

```bash
# 把 landing/index.html 推到 gh-pages 分支即可
git add landing/index.html
git commit -m "feat: landing page"
git push origin main
```

或把 `landing/` 单独拎出去作为一个独立仓库 / Netlify Drop 部署。

## 自定义品牌

- 主色：改 `index.html` 顶部 `:root` 里的 `--accent` / `--accent-2` / `--accent-glow`
- 字体：默认 Geist → Inter → 系统字体栈；如需打包本地字体，把 `@font-face` 加进 `<style>` 顶部即可
- 产品截图：Hero 里的窗口是纯 CSS mockup；等拿到真实截图后，可整体替换 `.mockup` 区块为 `<img>`
