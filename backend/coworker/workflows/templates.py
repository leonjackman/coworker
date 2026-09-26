"""Built-in workflow templates and connector recipes (W40/W41).

A lightweight, off-line catalogue: parameterized workflows users can install and
adapt. The platform "connectors" are browser-driven publish/interact recipes
with human approval gates — the honest local-first substitute for closed
platform APIs.

Node ids use the system scheme (``id:1``, ``id:2``, …); installation renumbers
anyway, but keeping the source consistent avoids surprises.
"""

from __future__ import annotations

from typing import Any

_WRITE_SCRIPT = (
    "import os,sys; p=os.path.expanduser(sys.argv[1]); "
    "os.makedirs(os.path.dirname(p) or '.', exist_ok=True); "
    "open(p,'w',encoding='utf-8').write(sys.argv[2]); print('SAVED',p)"
)

TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "web-research-report",
        "name": "Web research → save report",
        "description": "Search the web for a topic and save the results to a text file.",
        "category": "research",
        "yaml": f"""name: web-research-report
description: Search the web for a topic and save the results to a file.
version: 1
inputs:
  topic:
    type: string
    required: true
  path:
    type: string
    default: "research/{{{{inputs.topic}}}}.txt"
steps:
  - id: "id:1"
    kind: tool
    do: web_search
    params:
      query: "{{{{inputs.topic}}}}"
      max_results: 5
    post:
      - "not_error"
    on_error:
      retry: 2
      then: abort
  - id: "id:2"
    kind: command
    params:
      command:
        - python3
        - "-c"
        - "{_WRITE_SCRIPT}"
        - "{{{{inputs.path}}}}"
        - "{{{{steps.id:1}}}}"
    post:
      - "equals result.return_code 0"
      - "contains SAVED"
""",
    },
    {
        "id": "page-watch",
        "name": "Watch a page for a keyword",
        "description": "Fetch a URL and fail the assertion unless a keyword appears (good for cron alerts).",
        "category": "monitoring",
        "yaml": """name: page-watch
description: Fetch a page and assert a keyword is present.
version: 1
inputs:
  url:
    type: string
    required: true
  keyword:
    type: string
    required: true
steps:
  - id: "id:1"
    kind: tool
    do: web_fetch
    params:
      url: "{{inputs.url}}"
    post:
      - "not_error"
  - id: "id:2"
    kind: assert
    do: "contains {{inputs.keyword}}"
""",
    },
    {
        "id": "browser-publish-generic",
        "name": "Browser publish (generic)",
        "description": "Open a compose page, fill title/body, screenshot, ask for approval, then click publish.",
        "category": "connector",
        "yaml": """name: browser-publish-generic
description: Generic browser publish recipe with a human approval gate.
version: 1
inputs:
  compose_url:
    type: string
    required: true
  title:
    type: string
    required: true
  body:
    type: string
    default: ""
steps:
  - id: "id:1"
    kind: browser
    do: navigate
    params:
      url: "{{inputs.compose_url}}"
  - id: "id:2"
    kind: browser
    do: type
    locator:
      role: textbox
      name: title
      fallback:
        - selector: "input[placeholder*='标题'], input[placeholder*='title']"
    params:
      text: "{{inputs.title}}"
  - id: "id:3"
    kind: browser
    do: type
    locator:
      role: textbox
      name: body
      fallback:
        - selector: "textarea, [contenteditable='true']"
    params:
      text: "{{inputs.body}}"
    when: "{{inputs.body}}"
  - id: "id:4"
    kind: human
    do: "请检查页面内容后确认发布"
  - id: "id:5"
    kind: browser
    do: click
    locator:
      role: button
      name: publish
      fallback:
        - text: 发布
        - text: Publish
""",
    },
    {
        "id": "xiaohongshu-publish",
        "name": "小红书 图文发布",
        "description": "登录小红书创作平台，上传图片、填写标题正文，人工确认后发布。",
        "category": "connector",
        "platform": "xiaohongshu",
        "yaml": """name: xiaohongshu-publish
description: 小红书网页版图文发布（含人工确认）。
version: 1
platform: xiaohongshu
inputs:
  title:
    type: string
    required: true
  body:
    type: string
    default: ""
  images:
    type: list
    required: true
steps:
  - id: "id:1"
    kind: browser
    do: navigate
    params:
      url: https://creator.xiaohongshu.com/publish/publish
  - id: "id:2"
    kind: browser
    do: set_files
    locator:
      role: button
      name: 上传图文
      fallback:
        - text: 上传图文
        - selector: "input[type='file']"
    params:
      files: "{{inputs.images}}"
  - id: "id:3"
    kind: browser
    do: type
    locator:
      selector: "input[placeholder*='标题']"
    params:
      text: "{{inputs.title}}"
  - id: "id:4"
    kind: browser
    do: type
    locator:
      selector: "[contenteditable='true']"
    params:
      text: "{{inputs.body}}"
    when: "{{inputs.body}}"
  - id: "id:5"
    kind: human
    do: "确认发布到小红书？"
  - id: "id:6"
    kind: browser
    do: click
    locator:
      role: button
      name: 发布
      fallback:
        - text: 发布
""",
    },
    {
        "id": "wechat-mp-draft",
        "name": "公众号 定时草稿",
        "description": "定时打开公众号后台，填入标题正文并保存为草稿（人工确认）。",
        "category": "connector",
        "platform": "wechat-mp",
        "yaml": """name: wechat-mp-draft
description: 公众号后台定时创建草稿（含人工确认）。
version: 1
platform: wechat-mp
inputs:
  title:
    type: string
    required: true
  body:
    type: string
    default: ""
steps:
  - id: "id:1"
    kind: browser
    do: navigate
    params:
      url: https://mp.weixin.qq.com/
  - id: "id:2"
    kind: browser
    do: type
    locator:
      selector: "input[placeholder*='标题']"
    params:
      text: "{{inputs.title}}"
  - id: "id:3"
    kind: browser
    do: type
    locator:
      selector: "[contenteditable='true']"
    params:
      text: "{{inputs.body}}"
    when: "{{inputs.body}}"
  - id: "id:4"
    kind: human
    do: "确认保存公众号草稿？"
  - id: "id:5"
    kind: browser
    do: click
    locator:
      role: button
      name: 保存
      fallback:
        - text: 保存为草稿
        - text: 保存
""",
    },
]


def list_templates() -> list[dict[str, Any]]:
    return [
        {k: t[k] for k in ("id", "name", "description", "category") if k in t} | {
            "platform": t.get("platform", "")
        }
        for t in TEMPLATES
    ]


def get_template(template_id: str) -> dict[str, Any] | None:
    for template in TEMPLATES:
        if template["id"] == template_id:
            return template
    return None
