"""独立服务（Standalone）启动入口。

上游 astrbot-t2i-service 的原有部署方式：本进程只跑 FastAPI 服务，
由 `src/api.py` 提供 /text2img 接口。

在 AstrBot 插件模式下**不会**执行本文件；插件模式由仓库根目录的
`main.py`（Star 插件入口）在 AstrBot 进程内以 aiohttp 提供同契约接口。
"""

import os

if __name__ == "__main__":
    import uvicorn

    port = os.getenv("PORT", 8999)

    uvicorn.run("t2i_core.api:app", host="0.0.0.0", port=int(port))
