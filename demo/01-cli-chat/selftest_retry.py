"""Day 5 自检：重试决策表 + 退避序列，不联网、不花钱。

跑法：
    uv run selftest_retry.py

为什么值得留一个自检脚本：
重试写错的两个方向都很贵——该重试的不重试，用户体验是「一次网络抖动就报错」；
不该重试的乱重试，401 会被重放 N 次，白烧时间和配额，日志里还全是噪音。
这张表就是判定标准，改 retry.py 之后跑一遍，防止手滑。
"""

import sys

import httpx2 as httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from retry import backoff_delay, describe, retryable


def make(cls, status: int):
    """造一个带 HTTP 状态码的假异常，不用真的发请求。"""
    req = httpx.Request("POST", "http://x/chat/completions")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


CASES = [
    ("500 服务端错误", make(InternalServerError, 500), True),
    ("503 服务不可用", make(InternalServerError, 503), True),
    ("429 限流", make(RateLimitError, 429), True),
    ("超时", APITimeoutError(request=httpx.Request("POST", "http://x")), True),
    ("连不上", APIConnectionError(request=httpx.Request("POST", "http://x")), True),
    ("400 参数错", make(BadRequestError, 400), False),
    ("401 key 错", make(AuthenticationError, 401), False),
    ("403 无权限", make(PermissionDeniedError, 403), False),
    ("404 模型不存在", make(NotFoundError, 404), False),
]


def main() -> int:
    failed = 0
    print("重试决策表：")
    for name, exc, expected in CASES:
        got = retryable(exc)
        ok = got == expected
        failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {name:<16} 重试={got}（期望 {expected}）")

    print("\n退避序列（base=1s, cap=16s, ±20% 抖动）：")
    print("  " + ", ".join(f"{backoff_delay(i):.1f}s" for i in range(1, 7)))

    print("\n错误说明抽样：")
    for name, exc, _ in CASES:
        print(f"  [{name}] {describe(exc).splitlines()[0]}")

    print(f"\n{'全部通过' if not failed else f'{failed} 项不符'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
