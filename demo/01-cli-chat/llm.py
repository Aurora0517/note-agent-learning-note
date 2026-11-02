"""模型调用层：客户端构建、单次调用、流式调用、计价。

`main.py` 和 `chat.py` 只管流程，不看 HTTP 细节、不碰 token 计算。
这三件事以后大概率会变（换平台、换模型、官方调价），全集中在这里改。

一个刻意没做的事：没有封装成 class。
现在只有「发一次请求」这一个行为，函数就够了；等真出现多 provider、
多套重试策略时再抽接口，那时候才知道接口该长什么样。
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from openai import OpenAI

from config import Settings
from retry import call_with_retry, describe

# ---------------------------------------------------------------------------
# 价格表：USD / 1M tokens，(低峰价, 高峰价)
# 来源 https://api-docs.deepseek.com/quick_start/pricing ，2026-11-02 抓取
# 官方随时可能调价，跨周复用前先回去核对
# ---------------------------------------------------------------------------
PRICING = {
    "deepseek-flash": {"cache_hit": (0.003, 0.006), "cache_miss": (0.15, 0.3), "output": (0.6, 1.2)},
    "deepseek-v4-pro": {"cache_hit": (0.022, 0.044), "cache_miss": (0.66, 1.32), "output": (1.98, 3.96)},
}

# 高峰时段（UTC 01-04、06-10，工作日）。折成北京时间是 09-12、14-18，
# 正好盖住上班时间——工作日白天调试都按高峰价走。
PEAK_WINDOWS_UTC = ((1, 4), (6, 10))
USD_TO_CNY = 7.1  # 只用于给人看的换算，实际按美元结算


@dataclass
class RunResult:
    """一次调用的结果。流式和非流式返回同一个结构，上层不用分情况处理。"""

    text: str
    ttft: float           # 首 token 延迟；非流式等于总耗时
    total: float          # 总耗时
    usage: object | None  # OpenAI SDK 的 usage 对象
    cost_usd: float | None
    streamed: bool

    @property
    def completion_tokens(self) -> int:
        return getattr(self.usage, "completion_tokens", 0) or 0

    @property
    def generation_speed(self) -> float | None:
        """出字速度（tok/s）。非流式没有意义，返回 None。"""
        if not self.streamed or self.total <= self.ttft:
            return None
        return self.completion_tokens / (self.total - self.ttft)


def build_client(settings: Settings) -> OpenAI:
    """建客户端。

    `max_retries=0` 是关键：SDK 默认自己重试 2 次，叠加上我们的重试，
    一次调用最坏会发出 3×3=9 个真实请求。日志上看不出来，账单上全在。
    """
    return OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout,
        max_retries=0,
    )


def is_peak(now: datetime | None = None) -> bool:
    """当前是否处于高峰计费时段（UTC 工作日 01-04 / 06-10）。"""
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:  # 5=周六, 6=周日
        return False
    return any(start <= now.hour < end for start, end in PEAK_WINDOWS_UTC)


def estimate_cost(model: str, usage, peak: bool) -> dict:
    """按官方单价估算花费（USD）。缓存命中和未命中要分开算，差价 50 倍。"""
    if usage is None:
        return {"total": None, "detail": "未返回 usage"}

    price = PRICING.get(model)
    if price is None:
        return {"total": None, "detail": "价格表里没有这个模型"}

    idx = 1 if peak else 0
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    if not hit and not miss:  # 没有这些字段时退回整包按未命中算（偏保守）
        miss = usage.prompt_tokens

    cost = (
        hit * price["cache_hit"][idx]
        + miss * price["cache_miss"][idx]
        + usage.completion_tokens * price["output"][idx]
    ) / 1_000_000
    return {"total": cost, "detail": f"命中 {hit} / 未命中 {miss}"}


def log_retry(attempt: int, total: int, delay: float, exc: Exception) -> None:
    """重试时打一行日志，让「正在退避」这件事在终端里可见。"""
    status = getattr(exc, "status_code", None) or type(exc).__name__
    print(f"  [重试 {attempt}/{total - 1}] {status}，{delay:.1f}s 后重试…", flush=True)


def ask(
    client: OpenAI,
    settings: Settings,
    messages: list[dict],
    *,
    stream: bool = True,
    on_chunk=None,
    on_retry=log_retry,
) -> RunResult:
    """发一次请求。`messages` 由调用方拼好，本函数不关心是单轮还是多轮。"""
    kwargs = dict(
        model=settings.model,
        messages=messages,
        max_tokens=settings.max_tokens,
        extra_body={"thinking": {"type": "disabled"}},
    )

    started = time.perf_counter()
    ttft: float | None = None
    pieces: list[str] = []
    usage = None

    if not stream:
        # 非流式重试是安全的：服务端还没开始吐内容，重发没有副作用
        response = call_with_retry(
            lambda: client.chat.completions.create(**kwargs),
            max_attempts=settings.retries,
            on_retry=on_retry,
        )
        ttft = time.perf_counter() - started
        text = response.choices[0].message.content or ""
        usage = response.usage
        pieces.append(text)
        if on_chunk:
            on_chunk(text)
    else:
        # 只对「建立连接」重试。一旦开始吐字就不能重来——
        # 重发会让用户看到同一段内容讲两遍，半句话也撤不回。
        resp = call_with_retry(
            lambda: client.chat.completions.create(
                **kwargs, stream=True, stream_options={"include_usage": True}
            ),
            max_attempts=settings.retries,
            on_retry=on_retry,
        )
        try:
            for chunk in resp:
                if chunk.usage:  # 只有最后一个 chunk 带 usage
                    usage = chunk.usage
                if not chunk.choices:  # usage-only chunk 的 choices 是空列表
                    continue
                content = chunk.choices[0].delta.content
                if not content:
                    continue
                if ttft is None:
                    ttft = time.perf_counter() - started
                pieces.append(content)
                if on_chunk:
                    on_chunk(content)
        except Exception as exc:
            if pieces:  # 中途断流：内容留着，但必须说清这是残句
                print(f"\n[输出中断] 已收到 {len(pieces)} 段，这句回答不完整。原因：{describe(exc)}")
            raise

    total = time.perf_counter() - started
    peak = is_peak()
    cost = estimate_cost(settings.model, usage, peak)
    return RunResult(
        text="".join(pieces),
        ttft=ttft if ttft is not None else total,
        total=total,
        usage=usage,
        cost_usd=cost["total"],
        streamed=stream,
    )
