"""上游自带的临时验证脚本（手工执行，不会被 pytest 收集）。

用法（需先按需启动独立服务，或直接改 URL 指向插件端点）：
    python -m t2i_core.test
"""

import json
import urllib.request

from .render import ScreenshotOptions, Text2ImgRender


def main():
    render = Text2ImgRender()
    template = """<html><head><meta name="viewport" content="width=device-width,initial-scale=1.0"></head><body><h1>{{ title }}😤</h1><p>{{ content }}</p></body></html>"""
    data = {"title": "Hello, World!", "content": "This is a test."}
    html = render.from_jinja_template(template, data)
    pic = render.html2pic(html, ScreenshotOptions(type="jpeg", full_page=True))

    print(f"Rendered HTML to {pic}")


def test_api(url: str = "http://localhost:8999/text2img/generate"):
    payload = {
        "html": '<html><head><meta name="viewport" content="width=device-width,initial-scale=1.0"></head><body><h1>Hello, World!😤</h1><p>This is a test.</p></body></html>',
        "options": {"type": "jpeg", "full_page": True},
        "json": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read()
        print(response.status, response.headers.get("Content-Type"), len(body), "bytes")


if __name__ == "__main__":
    test_api()
