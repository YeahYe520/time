"""config.py —— 读取 .env 里的配置。

`.env` 就是一个纯文本文件，一行一个 `KEY=value`。
这里手写十几行把它读进来，是想让你看清：**它没有任何魔法**，
不是什么框架特性，也不是什么高级机制，就是个文本文件。

（等哪天配置项多到需要引号、多行、变量插值了，再换成 python-dotenv。
现在还不需要。）
"""

import os
from pathlib import Path

ROOT = Path(__file__).parent


def load_env(path=ROOT / ".env"):
    """把 .env 里的键值对读进 os.environ。

    已经存在的环境变量优先，不覆盖——这样你在命令行临时
    `export AI_MODEL=xxx` 就能压过 .env，方便调试。
    """
    if not path.exists():
        return

    # 用 utf-8-sig 而不是 utf-8：它会自动吃掉文件开头可能存在的 BOM。
    # Windows 记事本和某些编辑器保存 UTF-8 时会偷偷在开头塞三个字节（BOM），
    # 用普通 utf-8 读的话，第一个键名会变成 "﻿AI_BASE_URL"——
    # 看着一模一样，但永远匹配不上，排查起来能耗掉一小时。
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

AI_BASE_URL = os.environ.get("AI_BASE_URL", "")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "deepseek-v4.1-flash")


def ai_configured():
    """密钥配好了没。没配时网页会给出提示，而不是抛一堆栈。"""
    return bool(AI_BASE_URL and AI_API_KEY)
