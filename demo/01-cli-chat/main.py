"""Day 2：第一次真实调用（非流式）——回答 + usage + 费用估算。

这一版只关心三件事：能不能拿到回答、token 用了多少、这次花了多少钱。
流式、多轮、重试分别留给 Day 3 / Day 4 / Day 5。

用法：
    uv run main.py                  # 用 .env 里的默认问题
    uv run main.py "你的问题"        # 临时换问题
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
# 官方随时可能调价，跨周复用前先回去核对一眼
# ---------------------------------------------------------------------------
PRICING = {
    "deepseek-flash": {"cache_hit": (0.003, 0.006), "cache_miss": (0.15, 0.3), "output": (0.6, 1.2)},
    "deepseek-v4-pro": {"cache_hit": (0.022, 0.044), "cache_miss": (0.66, 1.32), "output": (1.98, 3.96)},
}

# 高峰时段（UTC）：01:00-04:00 与 06:00-10:00，周一到周五
# 中国法定节假日按低峰计费，这里没法自动判断，只管工作日
PEAK_WINDOWS_UTC = ((1, 4), (6, 10))

# 只用于给人看的参考换算，实际扣费按美元结算
USD_TO_CNY = 7.1

DEFAULT_QUESTION = "用一句话解释什么是 Agent，再补一句它和普通聊天机器人的区别。"


def is_peak(now_utc: datetime) -> bool:
    """当前是否处于高峰计费时段。"""
    if now_utc.weekday() >= 5:  # 5=周六, 6=周日
        return False
    return any(start <= now_utc.hour < end for start, end in PEAK_WINDOWS_UTC)


def estimate_cost(model: str, usage, peak: bool) -> dict:
    """按官方单价估算本次请求花费（USD）。"""
    idx = 1 if peak else 0
    price = PRICING.get(model)
    if price is None:  # 新模型没录进价格表时别崩，只提示
        return {"total": None, "detail": "价格表里没有这个模型"}

    # DeepSeek 会把输入拆成缓存命中 / 未命中，两者差价很大，必须分开算
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    if not hit and not miss:  # 没有这些字段时退回整包计价（按未命中算，偏保守）
        miss = usage.prompt_tokens

    cost = (
        hit * price["cache_hit"][idx]
        + miss * price["cache_miss"][idx]
        + usage.completion_tokens * price["output"][idx]
    ) / 1_000_000
    return {
        "total": cost,
        "detail": f"命中 {hit} tok / 未命中 {miss} tok",
    }


def build_client() -> tuple[OpenAI, str, str]:
    """读 .env，返回 client 和配置。key 只从环境变量来，绝不硬编码。"""
    env_path = Path(__file__).with_name(".env")
    load_dotenv(env_path)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    model = os.getenv("OPENAI_MODEL", "").strip()

    missing = [k for k, v in
               {"OPENAI_API_KEY": api_key, "OPENAI_BASE_URL": base_url, "OPENAI_MODEL": model}.items()
               if not v]
    if missing:
        sys.exit(
            f"缺少配置：{', '.join(missing)}\n"
            f"在 {env_path} 里补齐（可参考同目录 .env.example）。"
        )

    return OpenAI(api_key=api_key, base_url=base_url), model, base_url


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    client, model, base_url = build_client()

    print(f"模型：{model}")
    print(f"端点：{base_url}")
    print(f"问题：{question}\n")

    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个简洁的中文技术助手，回答控制在三句话以内。"},
                {"role": "user", "content": question},
            ],
            max_tokens=512,
            # 思考模式默认开启，会先输出一大段思维链；Day 2 先关掉，
            # 让 TTFT 和费用都干净可比。想对比效果把它删掉即可。
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as exc:  # Day 5 会换成按状态码区分的重试，现在先给出人话
        sys.exit(f"调用失败：{type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - started

    answer = response.choices[0].message.content or ""
    usage = response.usage
    peak = is_peak(datetime.now(timezone.utc))
    cost = estimate_cost(model, usage, peak)

    print("回答：")
    print(answer)
    print("\n" + "-" * 50)
    print(f"耗时      : {elapsed:.2f}s")
    print(f"计费时段  : {'高峰' if peak else '低峰'}（UTC {datetime.now(timezone.utc):%H:%M}）")
    print(f"prompt    : {usage.prompt_tokens} tokens（{cost['detail']}）")
    print(f"completion: {usage.completion_tokens} tokens")
    print(f"total     : {usage.total_tokens} tokens")
    if cost["total"] is None:
        print(f"费用      : {cost['detail']}")
    else:
        print(f"费用      : ${cost['total']:.6f} ≈ ¥{cost['total'] * USD_TO_CNY:.6f}")


if __name__ == "__main__":
    main()
