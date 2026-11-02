# 01-cli-chat

W01 的练手项目：一个能流式对话的 Python CLI，跑通「Python 工程 + LLM API」全链路。

不是玩具——它有 token 统计、费用估算、TTFT 测量、指数退避重试和人类可读的报错。
代码分四层，每层只干一件事。

## 快速开始（3 步）

### 1. 装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

装完新开一个终端（或 `source ~/.zshrc`）。验证：`uv --version`。

### 2. 配置 API key

```bash
cp .env.example .env
```

编辑 `.env`，把 `OPENAI_API_KEY=` 后面填上你的 key（DeepSeek 控制台申请）。
`.env` 已被 `.gitignore` 忽略，不会进仓库。

```dotenv
OPENAI_API_KEY=sk-你的key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-flash
```

### 3. 运行

```bash
uv run main.py "用一句话解释什么是 Agent"
```

不需要手动 `pip install` 或建虚拟环境——`uv run` 会自己同步依赖。

## 三个入口

| 命令 | 用途 |
| --- | --- |
| `uv run main.py "问题"` | 单次提问，流式输出 + 指标 |
| `uv run chat.py` | 多轮对话（`/clear` `/stats` `/exit`） |
| `uv run selftest_retry.py` | 离线自检重试判定表，不联网不花钱 |

常用参数（`main.py` 和 `chat.py` 都支持）：

```bash
uv run main.py --no-stream "问题"           # 关掉流式，对比总耗时
uv run main.py --compare                    # 流式与非流式各跑一次看差距
uv run main.py --model deepseek-v4-pro "问题"
uv run main.py --max-tokens 200 "问题"      # 限制回答长度，省钱
uv run main.py --timeout 10 --retries 2 "问题"
uv run main.py --debug                      # 出错时打印完整堆栈
uv run main.py --help                       # 全部参数
```

## 输出长这样

```
$ uv run main.py "一句话说明分层的价值"

分层能隔离变化、降低耦合，每层只关注自身职责，便于独立开发、复用和测试。

--- 流式 ---
TTFT      : 0.933s          ← 首 token 延迟，用户感知的「快」只看这个
总耗时    : 1.242s
出字速度  : 77.6 tok/s      ← 远快于人眼阅读速度，所以后面的时间感知不到
tokens    : prompt 24（命中 0 / 未命中 24） / completion 24
费用      : $0.000036 ≈ ¥0.000256
```

## 文件结构

```
.
├── config.py          # 配置层：.env + 环境变量 + 命令行 → Settings（带 key 脱敏）
├── llm.py             # 调用层：客户端、流式/非流式、usage、计价
├── retry.py           # 健壮性：哪些错重试、指数退避、人话报错
├── main.py            # 入口：单次提问（argparse）
├── chat.py            # 入口：多轮对话（维护 messages 历史）
├── selftest_retry.py  # 离线自检：重试判定表
├── .env               # 你的 key（不提交）
├── .env.example       # 配置模板（提交）
└── uv.lock            # 依赖锁文件（提交）
```

分层规则：**入口不碰 HTTP，调用层不碰 argparse，配置层不碰任何业务**。
换平台只改 `.env`，换模型只改一行，加参数只在入口加。

## 三个指标

| 指标 | 含义 | 什么时候看 |
| --- | --- | --- |
| **TTFT** | 从发请求到第一段文字到达 | 判断「卡不卡」，这才是用户感知的延迟 |
| **总耗时** | 到最后一个字到达 | 只影响「等全文复制走」的场景 |
| **tokens / 费用** | prompt、completion、缓存命中率 | 调优和控预算，天天要看 |

实测：非流式 1.41s，流式 TTFT 0.51s / 总耗时 1.11s——**感知快 2.8 倍，总耗时几乎不变**。

## 常见错误

程序只给人话，不给堆栈（要堆栈加 `--debug`）：

| 症状 | 提示 | 怎么办 |
| --- | --- | --- |
| key 错 | API key 无效或已过期（401） | 检查 `.env` 有没有多余空格、引号 |
| 模型名错 | 接口或模型不存在（404） | 现役是 `deepseek-flash` / `deepseek-v4-pro`，别抄老教程的 `deepseek-chat` |
| 限流 | 被限流了（429） | 降并发或确认余额；程序已自动退避重试 |
| 连不上 | 连不上服务器 | 查网络/代理，或 `OPENAI_BASE_URL` 写错 |
| 超时 | 请求超时 | 加 `--timeout`，或换低峰时段 |

401/403/404/400 **不会重试**（重试也没用，只烧配额）；
429/5xx/超时/连接失败 会按 1s→2s→4s 指数退避，最多尝试 3 次。

## 成本参考

`deepseek-flash` 一次普通问答约 **¥0.0003~0.001**，跑一整天调试也就几分钱。
真正贵的是长上下文和多轮累积：第 n 轮的 prompt 包含前 n-1 轮全部内容，累计按平方涨。

当前价格（USD / 1M tokens，2026-11-02 核对）：

| 模型 | 输入-命中 | 输入-未命中 | 输出 |
| --- | --- | --- | --- |
| `deepseek-flash` | $0.006 | $0.3 | $1.2 |
| `deepseek-v4-pro` | $0.044 | $1.32 | $3.96 |

高峰时段（工作日北京时间 09-12、14-18）单价翻倍；缓存命中与未命中输入价差 **50 倍**，
所以 system prompt 要固定放在最前面、别掺时间戳——否则缓存永远不命中。

## 踩坑记录

更细的过程和数字见仓库 `notes/`：

- `2026-11-02-W01-Day2调用记录.md` — 首次调用、价格表、费用推导
- `2026-11-02-W01-Day3流式与TTFT.md` — TTFT 与感知延迟
- `2026-11-02-W01-Day4多轮对话.md` — token 平方增长、缓存 64 粒度实测
- `2026-11-02-W01-Day5健壮性.md` — 五个故障场景验证
