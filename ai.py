"""ai.py —— 把一句人话翻译成结构化任务。

分工（这是整个 v0.3 最重要的一句话）：
    这一层只负责**翻译**——听懂人话，吐出结构化数据。
    **排时间永远由 plan.py 的 schedule() 做，不归 AI 管。**

    为什么？因为排时间是确定性问题，有唯一正确答案，
    而 AI 不可复现、算术不可靠、还要联网花钱。
    翻译是语义问题，没有唯一答案，正是 AI 擅长的。

结构上刻意拆成两半：

    parse_reply(text)  —— 纯函数：AI 的原始回复 → 校验过的任务草稿。可测。
    ask_ai(text)       —— 网络调用：发请求、收回复。不可测。

**测试覆盖率的关键就在这个拆法**：所有"会出错的地方"都在 parse_reply 里，
它测得动；真正碰网络的只剩 ask_ai 一个函数，而它薄到几乎没逻辑。
"""

import json

import requests

from config import AI_API_KEY, AI_BASE_URL, AI_MODEL, ai_configured
from plan import parse_day_offset, parse_hhmm, today_iso, validate_task, weekday_cn

PROMPT = """你是日程录入助手。把用户说的一句话拆成若干条待办事务。

**今天是 __TODAY__，__WEEKDAY__。**

规则：
- name：任务名，简短，不超过 20 字，尽量保留用户自己的说法
- minutes：预计耗时（分钟），必须是正整数。用户没说就按常识估一个
- priority：只能是「高」「中」「低」三者之一。
  **默认一律填「低」**，只有用户用了明确的修饰词才升级：
    · 「高」——很急、必须、一定要、今天就得交
    · 「中」——尽量、最好、优先做
  没有这类修饰词就填「低」。不要自己脑补重要性，
  「重要」这个词本身不算修饰词，得是上面那些。
- start：开始时间，24 小时制的 "HH:MM"。
  **只有用户报了具体钟点才填**（"9点半"→"09:30"，"下午三点"→"15:00"）。
  只说了「上午」「下午」「晚上」这类模糊时段，或者压根没提时间，
  一律填 null——排程程序会自己找空档把它放进去，比瞎猜一个钟点准。
- day_offset：这条事务是**距今几天**，一个整数。
    今天 = 0，明天 = 1，后天 = 2，大后天 = 3，昨天 = -1。
  用户说「下周三」「周五」这种星期几，**先照上面给的今天和星期几
  算出差几天**，再填那个数。
  **绝对不要输出具体日期**（不要写 "2026-11-05"），只输出天数差。
  用户**完全没提日期**时填 null——注意这和"今天"不是一回事，
  没说日期就填 null，让程序去决定。
- guessed：如果 minutes 是你自己估的而不是用户明说的，填 true，否则 false

只输出 JSON，不要解释、不要 markdown 代码块、不要多余文字。格式：
{"tasks":[{"name":"写周报","minutes":60,"priority":"低","start":null,"day_offset":null,"guessed":false}]}

如果用户的话里没有任何可执行的事务（比如只是在闲聊），返回 {"tasks":[]}。"""


def build_prompt(today):
    """把今天的日期和星期几填进提示词。

    **为什么用 replace 而不是 .format()？** 因为提示词里有一整段 JSON 示例，
    里面全是花括号。用 .format() 的话每个花括号都得写成 {{{{，
    以后每改一次提示词都要数一遍括号，迟早数错——而且错了是运行时才炸。
    占位符替换没这个问题。

    为什么要把"今天几号、周几"告诉 AI？因为「明天」「下周三」这类词
    离开今天就没有意义。**把它算好的信息喂进去，它就不需要去猜今天是几号**——
    这是它能算对「下周三差几天」的前提。
    """
    return PROMPT.replace("__TODAY__", today).replace("__WEEKDAY__", weekday_cn(today))


def parse_reply(text, today=None):
    """把 AI 的原始回复解析成校验过的任务草稿。

    返回 (草稿列表, 问题说明)。纯函数——不联网、不读文件、不写文件。
    today 显式传进来（不传才去读时钟）是为了**测得动**。

    这里要防四件事，每一件我都见过 AI 干：
    1. 把 JSON 包在 ```json 代码块里
    2. 在 JSON 前后夹带解释文字（"好的，我帮你解析如下：..."）
    3. 输出根本不是合法 JSON
    4. JSON 合法但字段不对（优先级写成「很高」、时长写成「一小时」）

    第 4 条由 plan.validate_task 兜底——**和命令行、网页用的是同一套规则**。
    所以 AI 编不出一个合法但违规的任务。

    **开始时间和日期都是单独处理的**：任务本身没问题、只是时间或日期没看懂时，
    不整条丢掉，而是退回"自动安排"/"你正在看的那天"并说明一句。
    用户想要的是那条事务，不是一个格式完美的钟点。
    """
    if today is None:
        today = today_iso()
    if not text or not text.strip():
        return None, "AI 返回了空内容"

    cleaned = text.strip()

    # 防 1：剥掉 markdown 代码块
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0]

    # 防 2：从第一个 { 截到最后一个 }
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return None, f"AI 的回复里找不到 JSON：{text[:80]}"
    cleaned = cleaned[start:end + 1]

    # 防 3
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return None, f"AI 返回的不是合法 JSON：{e}"

    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        return None, "AI 返回的 JSON 结构不对：缺少 tasks 数组"

    drafts, problems = [], []
    for i, raw in enumerate(data["tasks"], 1):
        if not isinstance(raw, dict):
            problems.append(f"第 {i} 条不是对象，已跳过")
            continue

        # 防 4：走和命令行完全一样的校验。
        # 先不传 start——任务本身合不合法和它有没有定时间无关。
        fields, error = validate_task(
            raw.get("name", ""), raw.get("minutes", ""), raw.get("priority", "")
        )
        if error:
            problems.append(f"第 {i} 条被拒（{error}），已跳过")
            continue

        # 时间没看懂不算这条作废，退回自动安排
        start, start_error = parse_hhmm(raw.get("start"))
        if start_error:
            problems.append(f"第 {i} 条的时间没看懂，改成自动安排")
        elif start is not None:
            fields["start"] = start

        # 日期同理。AI 给的是"距今几天"，**换成具体日期这一步在这里做**——
        # 也就是说 AI 自始至终没机会说出一个错的日期，它只能说出一个错的偏移。
        day, day_error = parse_day_offset(raw.get("day_offset"), today)
        if day_error:
            problems.append(f"第 {i} 条的日期没看懂，加到你正在看的那天")
        elif day is not None:
            fields["date"] = day

        drafts.append({**fields, "guessed": bool(raw.get("guessed"))})

    return drafts, ("；".join(problems) if problems else None)


def ask_ai(user_text, timeout=60, today=None):
    """把用户的一句话发给 AI，拿回原始回复文本。返回 (文本, 错误信息)。

    这一层只负责网络。**所有判断都在 parse_reply 里**——那边测得动，
    所以这里越薄越好。网络会以各种方式失败，每一种都要给人话，
    而不是把异常栈丢到界面上。
    """
    if today is None:
        today = today_iso()
    if not ai_configured():
        return None, (
            "还没配置 AI。在项目根目录建一个 .env（照 .env.example 抄），"
            "填上 AI_BASE_URL 和 AI_API_KEY 再重启服务。"
        )

    try:
        resp = requests.post(
            f"{AI_BASE_URL.rstrip('/')}/v1/messages",
            headers={
                # 不同网关认不同的认证头，两个都发，谁认哪个都行
                "Authorization": f"Bearer {AI_API_KEY}",
                "x-api-key": AI_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": AI_MODEL,
                "max_tokens": 1024,
                "messages": [
                    {"role": "user", "content": f"{build_prompt(today)}\n\n用户说：{user_text}"}
                ],
            },
            timeout=timeout,
        )
    except requests.Timeout:
        return None, f"AI {timeout} 秒没响应，再试一次或者把话说短点"
    except requests.RequestException as e:
        return None, f"连不上 AI：{e}"

    if resp.status_code != 200:
        return None, f"AI 返回 HTTP {resp.status_code}：{resp.text[:200]}"

    try:
        data = resp.json()
    except ValueError:
        return None, f"AI 返回的不是 JSON：{resp.text[:200]}"

    # Anthropic 格式：content 是内容块数组，把文字块拼起来
    blocks = data.get("content") or []
    text = "".join(
        b.get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    )
    if not text:
        return None, f"AI 的回复里没有文字：{str(data)[:200]}"

    return text, None


def parse_request(user_text, timeout=60, today=None):
    """完整流程：发请求 → 解析回复。返回 (草稿列表, 错误信息)。"""
    if today is None:
        today = today_iso()
    raw, error = ask_ai(user_text, timeout, today)
    if error:
        return None, error
    return parse_reply(raw, today)
