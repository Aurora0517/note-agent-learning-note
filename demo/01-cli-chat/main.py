"""Day 1 骨架：最小可运行入口。

这一版的唯一目的是验证全链路通：uv 建的环境、装好的依赖、选中的解释器。
`uv run main.py` 能打印出内容，就说明 Phase 0 的地基打好了。

Day 2 起这个文件会被换成真实的 LLM 调用（非流式 + usage 统计）。
"""

import sys


def main() -> None:
    print("hello from 01-cli-chat")
    print(f"python      : {sys.version.split()[0]}")
    print(f"executable  : {sys.executable}")


if __name__ == "__main__":
    main()
