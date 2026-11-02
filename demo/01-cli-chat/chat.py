"""W01 多轮版：交互式对话，维护 messages 历史。

和 main.py 的区别只有一处：每次请求都带上完整历史，
所以 prompt_tokens 一轮轮往上涨，累计花费按轮数近似平方增长。

用法：
    uv run chat.py
    uv run chat.py --model deepseek-v4-pro --max-tokens 2048
    uv run chat.py --system "你是资深后端面试官，只问问题不给答案"

交互命令：
    /clear  清空历史
    /stats  看累计统计
    /exit   退出
"""

import argparse
import sys
from datetime import datetime, timezone

from config import DEFAULT_SYSTEM_PROMPT, load_settings
from llm import USD_TO_CNY, ask, build_client, is_peak
from retry import describe

CONTEXT_WARN = 4000  # prompt 超过这个数就提醒清空历史

HELP = """命令：
  /clear  清空历史，重新开始
  /stats  打印累计统计
  /exit   退出（也可以 Ctrl+C 或 Ctrl+D）"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chat.py",
        description="多轮对话 CLI：流式输出 + 逐轮 token 与费用统计",
    )
    parser.add_argument("--model", help="覆盖 .env 里的模型名")
    parser.add_argument("--max-tokens", type=int, help="单轮回答的最大长度（默认读 .env，缺省 512）")
    parser.add_argument("--timeout", type=float, help="单次请求超时秒数（默认读 .env，缺省 30）")
    parser.add_argument("--retries", type=int, help="最多尝试次数含首发（默认读 .env，缺省 3）")
    parser.add_argument("--system", default=DEFAULT_SYSTEM_PROMPT, help="system prompt")
    return parser


def print_turn_stats(index: int, result) -> None:
    usage = result.usage
    if usage is None:
        print("\n[本轮] 未返回 usage")
        return
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    cost = f"${result.cost_usd:.6f} ≈ ¥{result.cost_usd * USD_TO_CNY:.6f}" if result.cost_usd is not None else "未知"
    print(f"\n[第 {index} 轮] TTFT {result.ttft:.2f}s / 总耗时 {result.total:.2f}s")
    print(f"          prompt {usage.prompt_tokens}（缓存命中 {hit} / 未命中 {miss}）"
          f" + completion {usage.completion_tokens} = {usage.total_tokens} tokens")
    print(f"          本轮 {cost}")


def print_summary(turns: list) -> None:
    if not turns:
        print("本次没有成功的对话轮次。")
        return
    print("\n" + "=" * 64)
    print(f"{'轮次':<4}{'prompt':>8}{'completion':>12}{'累计 tokens':>14}{'TTFT':>8}")
    print("-" * 64)
    cumulative = 0
    for i, turn in enumerate(turns, 1):
        usage = turn.usage
        if usage is None:
            continue
        cumulative += usage.total_tokens
        print(f"{i:<4}{usage.prompt_tokens:>8}{usage.completion_tokens:>12}{cumulative:>14}{turn.ttft:>7.2f}s")
    total_cost = sum(t.cost_usd for t in turns if t.cost_usd is not None)
    print("-" * 64)
    print(f"共 {len(turns)} 轮，累计 {cumulative} tokens，合计 ${total_cost:.6f} ≈ ¥{total_cost * USD_TO_CNY:.6f}")
    print("注意第一列：prompt 每轮都在涨，因为它包含之前所有轮次的内容。")


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings(
        model=args.model,
        timeout=args.timeout,
        max_tokens=args.max_tokens,
        retries=args.retries,
    )
    client = build_client(settings)
    peak = is_peak(datetime.now(timezone.utc))

    messages: list[dict] = [{"role": "system", "content": args.system}]
    turns: list = []

    print(f"模型：{settings.model}　端点：{settings.base_url}")
    print(f"system prompt：{args.system}")
    print(f"重试：最多 {settings.retries} 次尝试（429/5xx/超时才重试）")
    print(f"计费：{'高峰' if peak else '低峰'}")
    print(HELP + "\n")

    printer = (lambda s: (sys.stdout.write(s), sys.stdout.flush()))

    while True:
        try:
            sys.stdout.write("\n你 > ")
            sys.stdout.flush()
            line = input()
        except (EOFError, KeyboardInterrupt):
            break

        line = line.strip()
        if not line:
            continue
        if line in ("/exit", "exit", "quit"):
            break
        if line == "/clear":
            messages = [{"role": "system", "content": args.system}]
            print("已清空历史，开始新对话。")
            continue
        if line == "/stats":
            print_summary(turns)
            continue
        if line == "/help":
            print(HELP)
            continue

        messages.append({"role": "user", "content": line})
        sys.stdout.write("AI > ")
        sys.stdout.flush()

        try:
            result = ask(client, settings, messages, stream=True, on_chunk=printer)
        except KeyboardInterrupt:
            print("\n[已中断本轮，历史未写入]")
            messages.pop()  # 半句话不能进历史
            continue
        except Exception as exc:
            print(f"\n[调用失败] {describe(exc)}")
            messages.pop()  # 失败的那一问不留脏上下文
            continue

        # 唯一的「记忆」机制：回答回写进历史，下一轮才能带上
        messages.append({"role": "assistant", "content": result.text})
        turns.append(result)
        print_turn_stats(len(turns), result)

        if result.usage and result.usage.prompt_tokens > CONTEXT_WARN:
            print(f"          ⚠ prompt 已超 {CONTEXT_WARN} tokens，长上下文每轮都在重复计费，考虑 /clear")

    print_summary(turns)
    print("再见。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
