"""Day 3：流式输出 —— 逐 chunk 打印 + 测 TTFT。

核心问题：同样一次请求，非流式和流式的「总耗时」差不多，
但用户感受到的等待时间差了一个数量级。差的就是 TTFT。

用法：
    uv run main.py                     # 流式（默认）
    uv run main.py "你的问题"
    uv run main.py --no-stream         # 非流式，看总耗时
    uv run main.py --compare           # 两种都跑一遍，直接对比
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# ---------------------------------------------------------------------------
# 价格表：USD / 1M tokens，(低峰价, 高峰价)
# 来源 https://api-docs.deepseek.com/quick_start/pricing ，2026-11-02 抓取
# ---------------------------------------------------------------------------
PRICING = {
    "deepseek-flash": {"cache_hit": (0.003, 0.006), "cache_miss": (0.15, 0.3), "output": (0.6, 1.2)},
    "deepseek-v4-pro": {"cache_hit": (0.022, 0.044), "cache_miss": (0.66, 1.32), "output": (1.98, 3.96)},
}

PEAK_WINDOWS_UTC = ((1, 4), (6, 10))  # 工作日 UTC 01-04、06-10 计高峰
USD_TO_CNY = 7.1

DEFAULT_QUESTION = "用三句话解释什么是 Agent 的 ReAct 循环，并给一个具体例子。"
SYSTEM_PROMPT = "你是一个简洁的中文技术助手，回答控制在三句话以内。"


def is_peak(now_utc: datetime) -> bool:
    if now_utc.weekday() >= 5:
        return False
    return any(start <= now_utc.hour < end for start, end in PEAK_WINDOWS_UTC)


def estimate_cost(model: str, usage, peak: bool) -> dict:
    """按官方单价估算花费（USD）。流式和非流式共用。"""
    if usage is None:
        return {"total": None, "detail": "未返回 usage"}
    idx = 1 if peak else 0
    price = PRICING.get(model)
    if price is None:
        return {"total": None, "detail": "价格表里没有这个模型"}

    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    if not hit and not miss:
        miss = usage.prompt_tokens

    cost = (
        hit * price["cache_hit"][idx]
        + miss * price["cache_miss"][idx]
        + usage.completion_tokens * price["output"][idx]
    ) / 1_000_000
    return {"total": cost, "detail": f"命中 {hit} / 未命中 {miss}"}


def build_client() -> tuple[OpenAI, str, str]:
    env_path = Path(__file__).with_name(".env")
    load_dotenv(env_path)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    model = os.getenv("OPENAI_MODEL", "").strip()

    missing = [k for k, v in
               {"OPENAI_API_KEY": api_key, "OPENAI_BASE_URL": base_url, "OPENAI_MODEL": model}.items()
               if not v]
    if missing:
        sys.exit(f"缺少配置：{', '.join(missing)}\n在 {env_path} 里补齐（参考同目录 .env.example）。")
    return OpenAI(api_key=api_key, base_url=base_url), model, base_url


def call_once(client, model, question, *, stream: bool) -> dict:
    """发一次请求，返回 {ttft, total, usage, text}。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    kwargs = dict(
        model=model,
        messages=messages,
        max_tokens=512,
        extra_body={"thinking": {"type": "disabled"}},
    )

    started = time.perf_counter()
    ttft = None
    pieces: list[str] = []
    usage = None

    if not stream:
        response = client.chat.completions.create(**kwargs)
        ttft = time.perf_counter() - started  # 非流式没有「首 token」概念，等同总耗时
        text = response.choices[0].message.content or ""
        usage = response.usage
        print(text)
        pieces.append(text)
    else:
        # 关键：stream_options 让最后一个 chunk 带回 usage，否则流式拿不到 token 数
        resp = client.chat.completions.create(
            **kwargs, stream=True, stream_options={"include_usage": True}
        )
        for chunk in resp:
            if chunk.usage:  # 只有最后一个 chunk 带 usage
                usage = chunk.usage
            if not chunk.choices:  # usage-only chunk 的 choices 是空列表
                continue
            content = chunk.choices[0].delta.content
            if not content:
                continue
            if ttft is None:
                ttft = time.perf_counter() - started  # 第一段正文到达的时刻
            print(content, end="", flush=True)  # 不 flush 就会攒在缓冲区里看着像卡住
            pieces.append(content)
        print()

    total = time.perf_counter() - started
    return {"ttft": ttft, "total": total, "usage": usage, "text": "".join(pieces)}


def report(tag: str, result: dict, model: str, peak: bool, *, streamed: bool) -> None:
    usage = result["usage"]
    cost = estimate_cost(model, usage, peak)
    ttft, total = result["ttft"], result["total"]

    print(f"\n--- {tag} ---")
    print(f"TTFT      : {ttft:.3f}s")
    print(f"总耗时    : {total:.3f}s")
    if not streamed:
        print("出字速度  : 拿不到——非流式只在结束时一次性到达，中途没有时间点可测")
    elif usage is not None and total > ttft:
        # 去掉等待首 token 的时间，才是模型真正的出字速度
        print(f"出字速度  : {usage.completion_tokens / (total - ttft):.1f} tok/s")
    if usage is not None:
        print(f"tokens    : prompt {usage.prompt_tokens} / completion {usage.completion_tokens}")
    if cost["total"] is None:
        print(f"费用      : {cost['detail']}")
    else:
        print(f"费用      : ${cost['total']:.6f} ≈ ¥{cost['total'] * USD_TO_CNY:.6f}（{cost['detail']}）")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    stream = True
    compare = False
    if "--no-stream" in args:
        stream = False
        args.remove("--no-stream")
    if "--compare" in args:
        compare = True
        args.remove("--compare")
    question = " ".join(args) if args else DEFAULT_QUESTION

    client, model, base_url = build_client()
    peak = is_peak(datetime.now(timezone.utc))
    print(f"模型：{model}　端点：{base_url}")
    print(f"问题：{question}")
    print(f"计费：{'高峰' if peak else '低峰'}（UTC {datetime.now(timezone.utc):%H:%M}）\n")

    try:
        if compare:
            # 先非流式再流式，同一个问题，两个口径各测一次
            plain = call_once(client, model, question, stream=False)
            report("非流式", plain, model, peak, streamed=False)
            print("\n" + "=" * 52)
            streamed = call_once(client, model, question, stream=True)
            report("流式", streamed, model, peak, streamed=True)
            print("\n" + "=" * 52)
            speedup = plain["total"] / streamed["ttft"] if streamed["ttft"] else 0
            print(f"结论：总耗时几乎一样（{plain['total']:.2f}s vs {streamed['total']:.2f}s），")
            print(f"      但用户等第一句话的时间从 {plain['total']:.2f}s 降到 {streamed['ttft']:.2f}s，快了 {speedup:.1f} 倍。")
        else:
            result = call_once(client, model, question, stream=stream)
            report("流式" if stream else "非流式", result, model, peak, streamed=stream)
    except Exception as exc:  # Day 5 换成按状态码区分的重试
        sys.exit(f"调用失败：{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
