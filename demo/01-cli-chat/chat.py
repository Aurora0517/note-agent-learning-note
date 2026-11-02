"""Day 4：多轮对话 —— 维护 messages 列表，看 token 怎么一轮轮涨上去。

和 Day 2/Day 3 的单轮调用最大的区别：**每次请求都要把完整历史重新发一遍**。
所以第 n 轮的 prompt_tokens ≈ 前 n-1 轮的全部 token + 本轮输入，
累计花费按轮数近似平方增长——这是多轮应用的第一个成本陷阱。

Day 5 补上了重试与友好报错：连接阶段失败会按指数退避重试，
401/404/400 这类「重试也没用」的错误立刻停手，并给一句人话。

用法：
    uv run chat.py

交互命令：
    /clear  清空历史，重新开始
    /stats  打印累计统计
    /exit   退出

公共部分（读配置、计价、TTFT 判定）暂时从 main.py 复用，
Day 6 会把这些抽到 llm.py / config.py，到时候把 import 换掉即可。
"""

import os
import sys
import time
from datetime import datetime, timezone

from main import SYSTEM_PROMPT, USD_TO_CNY, build_client, estimate_cost, is_peak
from retry import call_with_retry, describe

MAX_TOKENS = 1024
RETRIES = int(os.getenv("MAX_RETRIES", "3"))
CONTEXT_WARN = 4000  # prompt 超过这个数就提醒清空历史，避免不知不觉刷长上下文

HELP = """命令：
  /clear  清空历史，重新开始
  /stats  打印累计统计
  /exit   退出（也可以直接 Ctrl+C 或 Ctrl+D）"""


def _log_retry(attempt: int, total: int, delay: float, exc: Exception) -> None:
    status = getattr(exc, "status_code", None) or type(exc).__name__
    sys.stdout.write(f"\n  [重试 {attempt}/{total - 1}] {status}，{delay:.1f}s 后重试…")
    sys.stdout.flush()


def stream_turn(client, model: str, messages: list[dict], *, peak: bool, retries: int = 3) -> dict:
    """发一轮请求，流式打印回答，返回本轮的指标。

    重试只包住「建立连接」这一步：一旦开始吐字就不能重来，
    否则用户会看到同一句话讲两遍。
    """
    started = time.perf_counter()
    ttft = None
    pieces: list[str] = []
    usage = None

    resp = call_with_retry(
        lambda: client.chat.completions.create(
            model=model,
            messages=messages,  # 注意：这里传的是完整历史，不是单条消息
            max_tokens=MAX_TOKENS,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"thinking": {"type": "disabled"}},
        ),
        max_attempts=retries,
        on_retry=_log_retry,
    )

    try:
        for chunk in resp:
            if chunk.usage:
                usage = chunk.usage
            if not chunk.choices:  # usage-only chunk
                continue
            content = chunk.choices[0].delta.content
            if not content:
                continue
            if ttft is None:
                ttft = time.perf_counter() - started
            sys.stdout.write(content)  # 不用 print，避免每段都换行
            sys.stdout.flush()
            pieces.append(content)
    except Exception as exc:
        if pieces:
            # 中途断流：内容不回滚（屏幕上已经有了），但必须让用户知道这是残句
            print(f"\n[输出中断] 已收到 {len(pieces)} 段，这句回答不完整。")
        raise exc

    return {
        "ttft": ttft if ttft is not None else 0.0,
        "total": time.perf_counter() - started,
        "usage": usage,
        "text": "".join(pieces),
        "cost": estimate_cost(model, usage, peak)["total"],
    }


def print_turn_stats(index: int, result: dict) -> None:
    usage = result["usage"]
    if usage is None:
        print("\n[本轮] 未返回 usage")
        return

    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    cost = result["cost"]
    cost_text = f"${cost:.6f} ≈ ¥{cost * USD_TO_CNY:.6f}" if cost is not None else "未知"

    print(f"\n[第 {index} 轮] TTFT {result['ttft']:.2f}s / 总耗时 {result['total']:.2f}s")
    print(f"          prompt {usage.prompt_tokens}（缓存命中 {hit} / 未命中 {miss}）"
          f" + completion {usage.completion_tokens} = {usage.total_tokens} tokens")
    print(f"          本轮 {cost_text}")


def print_summary(turns: list[dict]) -> None:
    if not turns:
        print("本次没有成功的对话轮次。")
        return

    print("\n" + "=" * 64)
    print(f"{'轮次':<4}{'prompt':>8}{'completion':>12}{'累计 tokens':>14}{'TTFT':>8}")
    print("-" * 64)
    cumulative = 0
    for i, turn in enumerate(turns, 1):
        usage = turn["usage"]
        if usage is None:
            continue
        cumulative += usage.total_tokens
        print(f"{i:<4}{usage.prompt_tokens:>8}{usage.completion_tokens:>12}"
              f"{cumulative:>14}{turn['ttft']:>7.2f}s")
    total_cost = sum(t["cost"] for t in turns if t["cost"] is not None)
    print("-" * 64)
    print(f"共 {len(turns)} 轮，累计 {cumulative} tokens，合计 ${total_cost:.6f} ≈ ¥{total_cost * USD_TO_CNY:.6f}")
    print("注意第一列：prompt 每轮都在涨，因为它包含之前所有轮次的内容。")


def main() -> None:
    client, model, base_url = build_client()
    peak = is_peak(datetime.now(timezone.utc))
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    turns: list[dict] = []

    print(f"模型：{model}　端点：{base_url}")
    print(f"system prompt：{SYSTEM_PROMPT}")
    print(f"重试：最多 {RETRIES} 次尝试，指数退避（429/5xx/超时才重试）")
    print(HELP + "\n")

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
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
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
            result = stream_turn(client, model, messages, peak=peak, retries=RETRIES)
        except KeyboardInterrupt:
            # 半途 Ctrl+C：这轮回答作废，不回写 assistant 消息，避免历史里出现半句
            print("\n[已中断本轮，历史未写入]")
            messages.pop()
            continue
        except Exception as exc:
            # 重试用尽或不可重试的错误：一句人话，然后把这轮从历史里撤掉
            print(f"\n[调用失败] {describe(exc)}")
            messages.pop()  # 失败的那一问不留脏上下文
            continue

        # 关键一行：把模型回答回写进历史，下一轮它才「记得」
        messages.append({"role": "assistant", "content": result["text"]})
        turns.append(result)
        print_turn_stats(len(turns), result)

        if result["usage"] and result["usage"].prompt_tokens > CONTEXT_WARN:
            print(f"          ⚠ prompt 已超 {CONTEXT_WARN} tokens，长上下文每一轮都在重复计费，考虑 /clear")

    print_summary(turns)
    print("再见。")


if __name__ == "__main__":
    main()
