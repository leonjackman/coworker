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

TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "web-research-report",
        "name": "Web research → save report",
        "description": "Search the web for a topic and save the results to a text file.",
        "category": "research",
        # Cross-platform: uses the native `file` steps (no `python3`/shell), so it
        # runs identically on macOS, Windows and Linux.
        "yaml": """name: web-research-report
description: Search the web for a topic and save the results to a file.
version: 1
inputs:
  topic:
    type: string
    required: true
  path:
    type: string
    default: "research/{{inputs.topic}}.txt"
steps:
  - id: "id:1"
    kind: tool
    do: web_search
    description: Search the web for the topic
    params:
      query: "{{inputs.topic}}"
      max_results: 5
    post:
      - "not_error"
    on_error:
      retry: 2
      then: abort
  - id: "id:2"
    kind: file
    do: write
    description: Write the search results to the report file
    params:
      path: "{{inputs.path}}"
      content: "{{steps.id:1.result}}"
      mkdirs: true
    post:
      - "not_error"
  - id: "id:3"
    kind: file
    do: exists
    description: Verify the report file exists
    params:
      path: "{{inputs.path}}"
    post:
      - "equals result.exists true"
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
    description: Fetch the page HTML/text
    params:
      url: "{{inputs.url}}"
    post:
      - "not_error"
  - id: "id:2"
    kind: assert
    description: Assert the keyword appears in the fetched page text
    do: "matches {{steps.id:1.text}} {{inputs.keyword}}"
""",
    },
    {
        "id": "browser-publish-generic",
        "name": "Browser publish (generic)",
        "description": "Open a compose page, fill title/body, screenshot, ask for approval, then click publish.",
        "category": "connector",
        "yaml": """name: browser-publish-generic
description: Generic browser publish recipe (navigate → snapshot → click/type) with a human gate.
schema_version: 2
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
  title_point:
    type: list
    required: true
  body_point:
    type: list
    required: true
  publish_point:
    type: list
    required: true
steps:
  - id: "id:1"
    kind: browser
    do: navigate
    description: Open the compose page
    params:
      url: "{{inputs.compose_url}}"
  - id: "id:2"
    kind: browser
    do: snapshot
    description: Capture the page structure
  - id: "id:3"
    kind: browser
    do: click
    description: Focus the title field
    locator:
      coords: "{{inputs.title_point}}"
    bypass: [coord_only_locator]
    bypass_reason: Rich compose editors expose no stable role/name for their fields.
  - id: "id:4"
    kind: browser
    do: type
    description: Type the title
    params:
      text: "{{inputs.title}}"
  - id: "id:5"
    kind: browser
    do: click
    description: Focus the body field
    locator:
      coords: "{{inputs.body_point}}"
    when: "{{inputs.body}}"
    bypass: [coord_only_locator]
    bypass_reason: Same as the title field.
  - id: "id:6"
    kind: browser
    do: type
    description: Type the body
    params:
      text: "{{inputs.body}}"
    when: "{{inputs.body}}"
  - id: "id:7"
    kind: human
    description: Human confirms the content before publishing
    do: "请检查页面内容后确认发布"
  - id: "id:8"
    kind: browser
    do: click
    description: Click publish
    locator:
      coords: "{{inputs.publish_point}}"
    bypass: [coord_only_locator]
    bypass_reason: The publish button position is user-provided per platform.
  - id: "id:9"
    kind: browser
    do: snapshot
    description: Re-read the page after publishing (verification)
    post:
      - "not_error"
""",
    },
    {
        "id": "wechat-mp-draft",
        "name": "公众号 定时草稿",
        "description": "定时打开公众号后台，填入标题正文并保存为草稿（人工确认）。",
        "category": "connector",
        "platform": "wechat-mp",
        "yaml": """name: wechat-mp-draft
description: 公众号后台定时创建草稿（navigate → snapshot → click/type，含人工确认）。
schema_version: 2
version: 1
inputs:
  title:
    type: string
    required: true
  body:
    type: string
    default: ""
  title_point:
    type: list
    required: true
  body_point:
    type: list
    required: true
  save_point:
    type: list
    required: true
steps:
  - id: "id:1"
    kind: browser
    do: navigate
    description: 打开公众号后台
    params:
      url: https://mp.weixin.qq.com/
  - id: "id:2"
    kind: browser
    do: snapshot
    description: 读取页面结构
  - id: "id:3"
    kind: browser
    do: click
    description: 聚焦标题输入框
    locator:
      coords: "{{inputs.title_point}}"
    bypass: [coord_only_locator]
    bypass_reason: 富文本编辑器不暴露稳定的 role/name。
  - id: "id:4"
    kind: browser
    do: type
    description: 输入标题
    params:
      text: "{{inputs.title}}"
  - id: "id:5"
    kind: browser
    do: click
    description: 聚焦正文输入框
    locator:
      coords: "{{inputs.body_point}}"
    when: "{{inputs.body}}"
    bypass: [coord_only_locator]
    bypass_reason: 同标题输入框。
  - id: "id:6"
    kind: browser
    do: type
    description: 输入正文
    params:
      text: "{{inputs.body}}"
    when: "{{inputs.body}}"
  - id: "id:7"
    kind: human
    description: 人工确认保存草稿
    do: "确认保存公众号草稿？"
  - id: "id:8"
    kind: browser
    do: click
    description: 点击保存草稿
    locator:
      coords: "{{inputs.save_point}}"
    bypass: [coord_only_locator]
    bypass_reason: 保存按钮位置由用户按平台提供。
  - id: "id:9"
    kind: browser
    do: snapshot
    description: 保存后重新读取页面（验证）
    post:
      - "not_error"
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
