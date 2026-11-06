# -*- coding: utf-8 -*-
"""部署入口：监听 PORT 环境变量并绑定 0.0.0.0"""
import os, uvicorn

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8500")))
