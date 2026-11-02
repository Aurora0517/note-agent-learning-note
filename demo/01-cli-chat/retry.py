"""Day 5：重试策略 + 人类可读的错误说明。

两个职责分开：
- `describe(exc)`：把 SDK 的异常翻译成人话，告诉你「下一步该做什么」
- `call_with_retry()`：只对**值得重试**的错误做指数退避重试，其余立刻放弃

重试的边界（这比代码本身重要）：
- 值得重试：连接失败、超时、429 限流、5xx
- 不值得重试：401 key 错、403 无权限、404 模型不存在、400 参数错
  —— 这些重试 100 次结果一样，只会白烧钱和时间
"""

import random
import time

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

# 这些状态码可以重试；其余 4xx 一律不重试
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class RetryExhausted(Exception):
    """重试用尽后抛出，携带最后一次的原始异常。"""

    def __init__(self, attempts: int, last: Exception):
        super().__init__(f"重试 {attempts} 次仍失败")
        self.attempts = attempts
        self.last = last


def describe(exc: Exception) -> str:
    """把异常翻译成一句人话 + 一句该怎么做。"""
    if isinstance(exc, AuthenticationError):
        return (
            "API key 无效或已过期（401）。\n"
            "  → 检查 .env 里的 OPENAI_API_KEY：有没有多余空格、引号，或者 key 是不是被平台回收了。"
        )
    if isinstance(exc, PermissionDeniedError):
        return "当前 key 没有权限（403）。\n  → 去平台控制台确认这个 key 开通了对应模型。"
    if isinstance(exc, NotFoundError):
        return (
            "接口或模型不存在（404）。\n"
            "  → 检查 OPENAI_BASE_URL 末尾是否多了/少了 /v1，模型名拼写是否正确。"
            "DeepSeek 现役模型是 deepseek-flash / deepseek-v4-pro。"
        )
    if isinstance(exc, RateLimitError):
        return (
            "被限流了（429）。\n"
            "  → 说明请求太密或余额不足。降并发、放慢发送节奏，或者确认账户还有余额。"
        )
    if isinstance(exc, APITimeoutError):
        return (
            "请求超时。\n"
            "  → 网络慢或服务端排队。可以调大超时时间（--timeout），或者换低峰时段跑。"
        )
    if isinstance(exc, APIConnectionError):
        return (
            "连不上服务器。\n"
            "  → 检查网络/代理是否通，OPENAI_BASE_URL 是否写对（本地探测可用 curl 直接试一下）。"
        )
    if isinstance(exc, BadRequestError):
        detail = _message_of(exc)
        return (
            f"请求参数被拒（400）：{detail}\n"
            "  → 常见原因：max_tokens 超上限、上下文太长、参数名拼错、模型不支持该参数。"
        )
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", "?")
        return f"服务端返回 HTTP {status}。\n  → 多看官方状态页；5xx 属于对方的问题，等一会再试。"
    return f"{type(exc).__name__}: {exc}"


def _message_of(exc: Exception) -> str:
    """从 SDK 异常里挖出服务端给的原始 message。"""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if body.get("message"):
            return str(body["message"])
    return str(exc)


def retryable(exc: Exception) -> bool:
    """判断这个异常是否值得重试。"""
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, AuthenticationError | PermissionDeniedError | NotFoundError | BadRequestError):
        return False
    if isinstance(exc, APIStatusError):
        return getattr(exc, "status_code", 0) in RETRYABLE_STATUS
    return False


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 16.0) -> float:
    """第 attempt 次重试前该等多久：指数增长 + 抖动，且不超过 cap。"""
    delay = base * (2 ** (attempt - 1))
    # 抖动：避免多个客户端在同一时刻一起重试，把服务端再打垮一次
    # 注意 cap 要在加抖动之后再夹，否则抖动会把上限顶破（19s > 16s）
    return min(cap, delay * random.uniform(0.8, 1.2))


def retry_after(exc: Exception) -> float | None:
    """服务端用 Retry-After 头指定了等待时间时优先听它的。"""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def call_with_retry(
    fn,
    *,
    max_attempts: int = 3,
    base: float = 1.0,
    cap: float = 16.0,
    on_retry=None,
):
    """执行 fn()，仅对可重试错误做指数退避。

    参数 max_attempts 是**总尝试次数**（含第一次），设 1 等于不重试。
    这样 3 次尝试最多花 1 + 2 = 3 秒左右，不会无限烧钱。
    """
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if not retryable(exc) or attempt == max_attempts:
                raise
            delay = retry_after(exc) or backoff_delay(attempt, base=base, cap=cap)
            if on_retry:
                on_retry(attempt, max_attempts, delay, exc)
            time.sleep(delay)
    raise RetryExhausted(max_attempts, last)  # 理论上到不了这
