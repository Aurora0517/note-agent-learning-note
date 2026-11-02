"""W01 主线：单次提问的 CLI。

看这一层能看出分层的价值——只剩「解析参数 → 拼 messages → 调 llm.ask → 打印」，
没有 key 从哪来、重试怎么退避、token 怎么计价这些噪音。

用法：
    uv run main.py                        # 默认问题，流式
    uv run main.py "你的问题"
    uv run main.py --no-stream "你的问题"  # 非流式，看总耗时
    uv run main.py --compare              # 两种都跑一遍做对比
    uv run main.py --help                 # 全部参数
"""

import argparse
import sys

from config import DEFAULT_SYSTEM_PROMPT, load_settings
from llm import USD_TO_CNY, ask, build_client, is_peak
from retry import describe

DEFAULT_QUESTION = "用三句话解释什么是 Agent 的 ReAct 循环，并给一个具体例子。"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="单次提问：流式输出 + token 统计 + 费用估算",
    )
    parser.add_argument("question", nargs="*", help="要问的问题（不填用内置默认问题）")
    parser.add_argument("--model", help="覆盖 .env 里的模型名")
    parser.add_argument("--no-stream", dest="stream", action="store_false", help="关闭流式输出（默认流式）")
    parser.add_argument("--compare", action="store_true", help="流式与非流式各跑一次做对比")
    parser.add_argument("--max-tokens", type=int, help="单次回答的最大长度（默认读 .env，缺省 512）")
    parser.add_argument("--timeout", type=float, help="单次请求超时秒数（默认读 .env，缺省 30）")
    parser.add_argument("--retries", type=int, help="最多尝试次数含首发（默认读 .env，缺省 3）")
    parser.add_argument("--system", default=DEFAULT_SYSTEM_PROMPT, help="system prompt")
    parser.add_argument("--debug", action="store_true", help="出错时打印完整堆栈")
    return parser


def report(tag: str, result, settings) -> None:
    """打印一次调用的指标。TTFT 和总耗时分开看，这是 Day 3 的核心。"""
    print(f"\n--- {tag} ---")
    print(f"TTFT      : {result.ttft:.3f}s")
    print(f"总耗时    : {result.total:.3f}s")

    speed = result.generation_speed
    if speed is None:
        print("出字速度  : 拿不到——非流式只在结束时一次性到达，中途没有时间点可测")
    else:
        print(f"出字速度  : {speed:.1f} tok/s")

    if result.usage is None:
        print("tokens    : 未返回 usage")
        return

    usage = result.usage
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    print(f"tokens    : prompt {usage.prompt_tokens}（命中 {hit} / 未命中 {miss}）"
          f" / completion {usage.completion_tokens}")
    if result.cost_usd is None:
        print("费用      : 价格表里没有这个模型")
    else:
        print(f"费用      : ${result.cost_usd:.6f} ≈ ¥{result.cost_usd * USD_TO_CNY:.6f}")


def main() -> int:
    args = build_parser().parse_args()
    question = " ".join(args.question) if args.question else DEFAULT_QUESTION

    settings = load_settings(
        model=args.model,
        timeout=args.timeout,
        max_tokens=args.max_tokens,
        retries=args.retries,
    )
    client = build_client(settings)
    messages = [
        {"role": "system", "content": args.system},
        {"role": "user", "content": question},
    ]

    peak = is_peak()
    print(f"模型：{settings.model}　端点：{settings.base_url}")
    print(f"问题：{question}")
    print(f"超时：{settings.timeout}s　最多尝试：{settings.retries} 次　max_tokens：{settings.max_tokens}")
    print(f"计费：{'高峰' if peak else '低峰'}")

    printer = (lambda s: print(s, end="", flush=True))

    try:
        if args.compare:
            plain = ask(client, settings, messages, stream=False)
            print(plain.text)
            report("非流式", plain, settings)
            print("\n" + "=" * 52)
            streamed = ask(client, settings, messages, stream=True, on_chunk=printer)
            print()
            report("流式", streamed, settings)
            print("\n" + "=" * 52)
            speedup = plain.total / streamed.ttft if streamed.ttft else 0
            print(f"结论：总耗时接近（{plain.total:.2f}s vs {streamed.total:.2f}s），")
            print(f"      但用户等第一句话的时间从 {plain.total:.2f}s 降到 {streamed.ttft:.2f}s，快了 {speedup:.1f} 倍。")
        elif args.stream:
            result = ask(client, settings, messages, stream=True, on_chunk=printer)
            print()
            report("流式", result, settings)
        else:
            result = ask(client, settings, messages, stream=False)
            print(result.text)
            report("非流式", result, settings)
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as exc:
        if args.debug:
            raise
        print(f"\n调用失败。\n{describe(exc)}\n（想看完整堆栈：加 --debug）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
