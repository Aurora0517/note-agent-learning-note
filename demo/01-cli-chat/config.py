"""配置层：所有「外部输入」都从这里进出。

职责边界很窄——只做两件事：
1. 把 `.env` / 环境变量 / 命令行参数合成一个 `Settings` 对象
2. 缺配置时给出人能照着做的提示

`main.py` 和 `chat.py` 不再自己读 env，也就不可能一个读到了、一个忘了读。
"""

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import os
import sys

ENV_PATH = Path(__file__).with_name(".env")

DEFAULT_SYSTEM_PROMPT = "你是一个简洁的中文技术助手，回答控制在三句话以内。"
DEFAULT_TIMEOUT = 30.0   # SDK 默认 600s 太长，用户会盯着卡死的界面十分钟
DEFAULT_RETRIES = 3      # 总尝试次数（含首发），不是「重试 3 次」= 4 个请求


@dataclass(frozen=True)
class Settings:
    """一次运行需要的全部配置。构造后不可变，避免跑到一半被人改掉。"""

    api_key: str
    base_url: str
    model: str
    timeout: float = DEFAULT_TIMEOUT
    max_tokens: int = 512
    retries: int = DEFAULT_RETRIES

    def __repr__(self) -> str:
        # 别让 key 出现在日志、异常、调试输出里。这一行看着不起眼，
        # 但 dataclass 默认 repr 会把 api_key 原样打出来。
        return (
            f"Settings(model={self.model!r}, base_url={self.base_url!r}, "
            f"timeout={self.timeout}, max_tokens={self.max_tokens}, "
            f"retries={self.retries}, api_key=***)"
        )


def load_settings(
    *,
    model: str | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
    retries: int | None = None,
) -> Settings:
    """合并优先级：命令行参数 > 环境变量 > `.env` > 代码默认值。"""
    # override=False：已经存在的环境变量优先，方便 `OPENAI_API_KEY=xxx uv run ...` 临时覆盖
    load_dotenv(ENV_PATH, override=False)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    model_name = (model or os.getenv("OPENAI_MODEL", "")).strip()

    missing = [
        name
        for name, value in (
            ("OPENAI_API_KEY", api_key),
            ("OPENAI_BASE_URL", base_url),
            ("OPENAI_MODEL", model_name),
        )
        if not value
    ]
    if missing:
        sys.exit(
            f"缺少配置：{', '.join(missing)}\n"
            f"在 {ENV_PATH} 里补齐（参考同目录 .env.example），"
            f"或临时用环境变量覆盖：OPENAI_API_KEY=sk-xxx uv run main.py"
        )

    return Settings(
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        timeout=timeout if timeout is not None else float(os.getenv("OPENAI_TIMEOUT", DEFAULT_TIMEOUT)),
        max_tokens=max_tokens if max_tokens is not None else int(os.getenv("OPENAI_MAX_TOKENS", "512")),
        retries=retries if retries is not None else int(os.getenv("OPENAI_RETRIES", DEFAULT_RETRIES)),
    )
