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

from retry import call_with_retry, describe

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

    timeout = float(os.getenv("OPENAI_TIMEOUT", "30"))
    # max_retries=0 很关键：SDK 默认会自己重试 2 次，叠加上我们的重试，
    # 一次调用可能真的发出 3×N 个请求——钱和日志都会失控。重试权收归自己。
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
    return client, model, base_url


def log_retry(attempt: int, total: int, delay: float, exc: Exception) -> None:
    status = getattr(exc, "status_code", None) or type(exc).__name__
    print(f"  [重试 {attempt}/{total - 1}] {status}，{delay:.1f}s 后重试…", flush=True)


def call_once(client, model, question, *, stream: bool, retries: int = 3) -> dict:
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
        # 重试包住整个 create；非流式重试是安全的（服务端还没开始吐内容）
        response = call_with_retry(
            lambda: client.chat.completions.create(**kwargs),
            max_attempts=retries,
            on_retry=log_retry,
        )
        ttft = time.perf_counter() - started  # 非流式没有「首 token」概念，等同总耗时
        text = response.choices[0].message.content or ""
        usage = response.usage
        print(text)
        pieces.append(text)
    else:
        # 只对「建立连接」这一步重试。一旦开始吐字就不能重试了——
        # 重试会重头再讲一遍，用户屏幕上的半句话没法撤回。
        resp = call_with_retry(
            lambda: client.chat.completions.create(
                **kwargs, stream=True, stream_options={"include_usage": True}
            ),
            max_attempts=retries,
            on_retry=log_retry,
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
                    ttft = time.perf_counter() - started  # 第一段正文到达的时刻
                print(content, end="", flush=True)  # 不 flush 就会攒在缓冲区里看着像卡住
                pieces.append(content)
        except Exception as exc:
            # 中途断流：已输出的内容保留，但明确告诉用户这是残缺的
            if pieces:
                print(f"\n[输出中断] 已收到 {len(pieces)} 段内容，本次回答不完整。原因：{describe(exc)}")
            raise
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


def take_option(args: list[str], name: str, default: str) -> str:
    """从参数里取 `--name value`，取到就删掉，剩下的才是问题正文。"""
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            sys.exit(f"{name} 后面缺一个值")
        value = args[i + 1]
        del args[i:i + 2]
        return value
    return default


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
    retries = int(take_option(args, "--retries", "3"))
    # 优先级：命令行 > 环境变量 > 默认 30s
    os.environ["OPENAI_TIMEOUT"] = take_option(args, "--timeout", os.getenv("OPENAI_TIMEOUT", "30"))
    question = " ".join(args) if args else DEFAULT_QUESTION

    client, model, base_url = build_client()
    peak = is_peak(datetime.now(timezone.utc))
    print(f"模型：{model}　端点：{base_url}")
    print(f"问题：{question}")
    print(f"超时：{os.environ['OPENAI_TIMEOUT']}s　最多尝试：{retries} 次")
    print(f"计费：{'高峰' if peak else '低峰'}（UTC {datetime.now(timezone.utc):%H:%M}）\n")

    try:
        if compare:
            # 先非流式再流式，同一个问题，两个口径各测一次
            plain = call_once(client, model, question, stream=False, retries=retries)
            report("非流式", plain, model, peak, streamed=False)
            print("\n" + "=" * 52)
            streamed = call_once(client, model, question, stream=True, retries=retries)
            report("流式", streamed, model, peak, streamed=True)
            print("\n" + "=" * 52)
            speedup = plain["total"] / streamed["ttft"] if streamed["ttft"] else 0
            print(f"结论：总耗时几乎一样（{plain['total']:.2f}s vs {streamed['total']:.2f}s），")
            print(f"      但用户等第一句话的时间从 {plain['total']:.2f}s 降到 {streamed['ttft']:.2f}s，快了 {speedup:.1f} 倍。")
        else:
            result = call_once(client, model, question, stream=stream, retries=retries)
            report("流式" if stream else "非流式", result, model, peak, streamed=stream)
    except KeyboardInterrupt:
        sys.exit("\n已取消。")
    except Exception as exc:
        # 只给结论和下一步，不甩堆栈。想看堆栈时设 DEBUG=1。
        if os.getenv("DEBUG"):
            raise
        sys.exit(f"\n调用失败。\n{describe(exc)}\n（想看完整堆栈：DEBUG=1 再跑一次）")


if __name__ == "__main__":
    main()
