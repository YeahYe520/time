"""time —— 把你输入的事务排成一天的行程。

用法:
    py plan.py add "写周报" 60 高           添加事务（名称 分钟 优先级）
    py plan.py add "开会" 30 高 14:00       带开始时间（可选）
    py plan.py add "评审" 90 中 --date 2026-11-03    加到别的日子
    py plan.py done 1                       标记某条事务已完成
    py plan.py list                         列出全部事务
    py plan.py                              显示今天的行程
    py plan.py plan 2026-11-03              显示指定那天的行程

事务分两个维度：

**按时间**：
    · **定了时间的**（写了开始时间）——固定在那个时段，会参与冲突仲裁
    · **没定时间的**——自动塞进空档里

**按日期**：每条事务都属于某一天（`date` 字段，'YYYY-MM-DD'）。
    不写日期就是今天，而且**加进去的那一刻就定死了**——
    不会明天再看它就跑到明天去。见 make_task。

冲突仲裁的规则：两个事务时段重叠时，优先级高的赢；
优先级相同时**先建的赢**（先入原则）；输的进待办栏。
**只有同一天的事务之间才会冲突**——排程是按天分开算的。

DAY_START/DAY_END 只用来「自动找空档」和显示，**不限制事务能排在哪**——
你写 20:00 的事务就排 20:00。
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

# Windows 控制台默认用 GBK 输出，中文在 Git Bash 里会变乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TASKS_FILE = Path(__file__).parent / "tasks.json"

# 自动找空档的范围。不影响定死时间的事务。
DAY_START = 9 * 60    # 09:00
DAY_END = 18 * 60     # 18:00

PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}


def to_hhmm(minutes):
    """从零点起的分钟数 -> '09:00'。540 -> '09:00'"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# ---------- 日期 ----------
#
# 日期一律用 'YYYY-MM-DD' 这种写法存。选它有三个理由：
#   1. 按字符串排序 = 按时间排序（'2026-11-02' > '2026-11-01'），不用转来转去
#   2. 人和程序都读得懂，打开 tasks.json 一眼能看明白
#   3. 这是国际标准（ISO 8601），哪天要跟别的工具对接也不会打架
#
# 反例是 '2026/11/2'——月份补没补零不一致，排序就乱了。

def today_iso():
    """今天是几号，形如 '2026-11-01'。

    **这是整个项目唯一读系统时钟的地方。** 别处要用"今天"，
    一律从参数传进去——不然那些函数就偷偷依赖时钟，测不动了。
    """
    return date.today().isoformat()


def parse_date(text):
    """把用户/AI 给的日期解析成 'YYYY-MM-DD'。返回 (日期, 错误信息)。

    留空 -> (None, None)，意思是"没说日期"，由调用方决定默认值。

    **宽进严出**：进来的写法尽量宽容，出去的一律是 'YYYY-MM-DD'。
    收这些写法：

        '2026-11-03'   标准写法
        '2026/11/03'   手写习惯
        '2026-1-3'     没补零
        '20261103'     ISO 8601 紧凑格式，AI 有时这么给

    但存下去、显示出来永远是补好零的标准写法。理由：
    宽进是为了不跟人较劲，严出是为了**下游只有一种形状要处理**——
    排序、比较、传给界面，都不用再考虑"万一它是斜杠的呢"。
    """
    if text is None:
        return None, None
    # datetime.date 对象直接用。放在 bool 检查之前，因为 date 不是 bool。
    if isinstance(text, date):
        return text.isoformat(), None
    # bool 是 int 的子类，不拦的话 True 会被 str() 成 'True' 去解析
    if isinstance(text, bool):
        return None, f"日期格式不对：「{text}」"

    raw = str(text).strip().replace("/", "-")
    if not raw:
        return None, None

    # '2026-1-3' -> '2026-01-03'。只补零，**不判断这个日期存不存在**——
    # "11 月有没有 31 号""是不是闰年"这些交给 fromisoformat，
    # 自己写一套迟早会写错，而且错了很难发现。
    parts = raw.split("-")
    if len(parts) == 3 and all(p.isdecimal() for p in parts):
        raw = f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"

    try:
        return date.fromisoformat(raw).isoformat(), None
    except ValueError:
        return None, f"日期要写成 2026-11-01 这样，收到的是「{text}」"


def task_day(t, today):
    """这条事务属于哪一天。**没写日期 = 今天。**

    这是给 v0.5 之前的老数据兜底的：那时候压根没有日期这个概念，
    所有事务都只存在于"今天"。把它们当成今天，行为跟以前一模一样。

    今天之后新建的事务都带显式日期（`make_task` 会填上），
    所以这条兜底路径实际只有老数据会走到。
    """
    return t.get("date") or today


def tasks_on(tasks, day, today=None):
    """挑出属于 day 这一天的事务。

    today 显式传进来（不传才去读时钟）是为了**测得动**——
    否则这个函数的结果会随"今天是几号"变，没法写断言。
    """
    if today is None:
        today = today_iso()
    return [t for t in tasks if task_day(t, today) == day]


def shift_day(day, days):
    """'2026-11-01' 往后挪 days 天，days 可以是负数（往前挪）。"""
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


# "距今几天"能接受的范围。见 parse_day_offset。
MAX_PAST_DAYS = 30
MAX_FUTURE_DAYS = 365

_WEEKDAYS = "一二三四五六日"


def weekday_cn(day):
    """'2026-11-01' -> '周六'。"""
    return f"周{_WEEKDAYS[date.fromisoformat(day).weekday()]}"


def friendly_day(day, today=None):
    """给人看的说法：昨天/今天/明天，其余写成「11月3日 周一」。

    为什么要单独标出这三天？因为「明天」比「11-02」好认得多——
    你不用在脑子里做减法。
    """
    if today is None:
        today = today_iso()
    if day == today:
        return "今天"
    if day == shift_day(today, 1):
        return "明天"
    if day == shift_day(today, -1):
        return "昨天"
    d = date.fromisoformat(day)
    return f"{d.month}月{d.day}日 {weekday_cn(day)}"


def parse_day_offset(text, today):
    """把"距今几天"解析成具体日期。返回 (日期, 错误信息)。

    **AI 不直接说日期，只说差几天**（今天=0，明天=1，后天=2，昨天=-1）。

    为什么不让它直接说 "2026-11-02"？因为那是算术，而算术不该交给 AI：

    · 它可能把 2026 写成 2025——**这种错你一眼看不出来**，
      而且要在几个月后翻到那天才发现
    · 它可能说出 2026-02-30 这种根本不存在的日期
    · 它可能记错今天是几号，整个偏移一位

    改成"差几天"之后：输出是个小整数，**小到能一眼验证**；
    加日期这一步由 `shift_day` 做，而它是纯算术，不可能加错。
    AI 只需要把「下周三」这个词翻译成一个数——那是语义问题，
    正是它擅长的。

    留空 -> (None, None)：没说日期，由调用方决定（加到用户正在看的那天）。
    """
    if text is None:
        return None, None
    # bool 是 int 的子类，不拦的话 True 会变成"差 1 天"
    if isinstance(text, bool):
        return None, f"日期没看懂：「{text}」"
    # int(3.7) 会**悄悄截成 3**——先拦掉，不然是个静默的错误
    if isinstance(text, float) and not text.is_integer():
        return None, f"日期没看懂：「{text}」"

    try:
        offset = int(text)
    except (TypeError, ValueError):
        return None, f"日期没看懂：「{text}」"

    # 往前一个月够补记昨天前天的事，往后一年够排任何正常计划了。
    # 再离谱就是 AI 算错了——宁可不要这个日期，也不能存一个荒唐的日子。
    if not -MAX_PAST_DAYS <= offset <= MAX_FUTURE_DAYS:
        return None, f"日期差得太离谱（{offset} 天），已忽略"

    return shift_day(today, offset), None


def parse_hhmm(text):
    """'09:30' -> 570。空值 -> (None, None)。返回 (分钟数, 错误信息)。

    留空表示"没定时间"，不是错误——那条事务会被自动安排到空档里。
    也接受裸整数分钟数，因为 AI 有时直接给数字。
    """
    if text is None:
        return None, None

    # bool 是 int 的子类，得先拦下来，否则 True 会被当成 1 分钟
    if isinstance(text, bool):
        return None, f"开始时间格式不对：「{text}」"

    if isinstance(text, int):
        if 0 <= text < 24 * 60:
            return text, None
        return None, f"开始时间超出范围：「{text}」"

    raw = str(text).strip()
    if not raw:
        return None, None

    hours_text, sep, minutes_text = raw.partition(":")
    if not sep:
        return None, f"开始时间要写成 09:30 这样，收到的是「{text}」"
    try:
        hours, minutes = int(hours_text), int(minutes_text)
    except ValueError:
        return None, f"开始时间要写成 09:30 这样，收到的是「{text}」"

    if not (0 <= hours < 24 and 0 <= minutes < 60):
        return None, f"开始时间超出范围：「{text}」"
    return hours * 60 + minutes, None


def load_tasks():
    if not TASKS_FILE.exists():
        return []
    return json.loads(TASKS_FILE.read_text(encoding="utf-8"))


def save_tasks(tasks):
    TASKS_FILE.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------- 排序：两个场景，两种顺序 ----------

def sort_tasks(tasks):
    """**没定时间**的事务找空档的顺序：优先级高的先找，同级短的先找。

    同级短任务优先是为了塞进更多事——短任务灵活，先安排它更容易填满空隙。
    """
    return sorted(
        tasks,
        key=lambda t: (PRIORITY_ORDER.get(t["priority"], 99), t["minutes"]),
    )


def sort_for_claiming(tasks):
    """**定了时间**的事务占位的顺序：优先级高的先占，同级**先建的**先占。

    这个顺序就是「先入原则」的全部实现——`id` 是自增的，
    id 小 = 建得早。所以后建的事务排在后面，撞车时自然被挤掉。
    """
    return sorted(
        tasks,
        key=lambda t: (PRIORITY_ORDER.get(t["priority"], 99), t["id"]),
    )


# ---------- 区间运算 ----------

def overlaps(start_a, end_a, start_b, end_b):
    """两个区间有没有重叠。

    注意首尾相接不算重叠：10:00 结束和 10:00 开始可以共存，
    否则每个事务之间都得强行留一分钟空隙。
    """
    return start_a < end_b and start_b < end_a


def find_gap(occupied, minutes, day_start=DAY_START, day_end=DAY_END):
    """在 [day_start, day_end) 里找第一个放得下 minutes 的空档，返回起点。

    occupied 是 [(start, end, 谁占的), ...]，可以乱序。
    **超出窗口的区间不会挡住窗口内的空档**——晚上 8 点的会议
    不应该影响早上 9 点的安排。

    找不到返回 None。
    """
    cursor = day_start
    for start, end, _ in sorted(occupied):
        if start - cursor >= minutes:
            return cursor               # 这个已占区间之前就够放
        cursor = max(cursor, end)
    if day_end - cursor >= minutes:
        return cursor
    return None


def schedule(tasks, day_start=DAY_START, day_end=DAY_END):
    """把未完成的事务排进一天。返回 (已排, 待办)。

    两步：

    **第一步：定了时间的先占位。** 顺序是「优先级高的先占，同级先建的先占」。
    占位时如果和已占的区间撞上就进待办——这就是冲突仲裁：
    高优先级挤掉低优先级，同优先级先建的挤掉后建的。
    被挤掉的事务**不占时间**，所以它不会连锁影响后面的判定。

    **第二步：没定时间的去找空档。** 顺序是「优先级高的先找，同级短的先找」。
    找不到足够大的空档就进待办。

    纯函数：不读文件、不打印，所以测得动。
    """
    alive = [t for t in tasks if not t.get("done")]
    fixed = [t for t in alive if t.get("start") is not None]
    floating = [t for t in alive if t.get("start") is None]

    scheduled, blocked = [], []
    occupied = []                       # [(start, end, 事务名)]

    # 第一步：占位 + 冲突仲裁
    for t in sort_for_claiming(fixed):
        start = t["start"]
        end = start + t["minutes"]
        clash = next((o for o in occupied if overlaps(start, end, o[0], o[1])), None)
        if clash:
            blocked.append({**t, "end": end, "auto": False, "blocked_by": clash[2]})
            continue
        occupied.append((start, end, t["name"]))
        scheduled.append({**t, "end": end, "auto": False, "blocked_by": None})

    # 第二步：自动找空档
    for t in sort_tasks(floating):
        start = find_gap(occupied, t["minutes"], day_start, day_end)
        if start is None:
            blocked.append({**t, "start": None, "end": None,
                            "auto": True, "blocked_by": None})
            continue
        end = start + t["minutes"]
        occupied.append((start, end, t["name"]))
        scheduled.append({**t, "start": start, "end": end,
                          "auto": True, "blocked_by": None})

    scheduled.sort(key=lambda t: t["start"])
    return scheduled, blocked


def blocked_reason(t):
    """给待办栏里的事务生成一句人话解释。"""
    if t.get("blocked_by"):
        return f"和「{t['blocked_by']}」冲突"
    return f"没有 {t['minutes']} 分钟的空档"


def render(scheduled, blocked, day=None, today=None,
           day_start=DAY_START, day_end=DAY_END):
    """把排好的行程画成给人看的文本。

    day/today 决定标题写「今天」还是「11月3日 周一」。
    不传 day 就当今天——这样老的调用方式（只传两个列表）照样能用。
    """
    if today is None:
        today = today_iso()
    if day is None:
        day = today
    title = friendly_day(day, today)

    if not scheduled and not blocked:
        return (f'{title}没有安排。用 '
                '`py plan.py add "事务名" 分钟 高/中/低 [09:30]` 添加。')

    lines = [f"{title}的行程", ""]

    for t in scheduled:
        mark = "  " if t["auto"] else "● "     # ● = 你自己定的时间
        lines.append(
            f"{mark}{to_hhmm(t['start'])}–{to_hhmm(t['end'])}  "
            f"{t['name']}（{t['minutes']}分钟 · {t['priority']}）"
        )

    used = sum(t["minutes"] for t in scheduled)
    lines += [
        "",
        f"已排 {len(scheduled)} 件 · 占用 {used} 分钟",
        f"（● = 你定的时间；没 ● 的是自动排进 {to_hhmm(day_start)}–{to_hhmm(day_end)} 的空档）",
    ]

    if blocked:
        lines += ["", "待办栏："]
        for t in blocked:
            when = f"{to_hhmm(t['start'])} " if t.get("start") is not None else ""
            lines.append(
                f"  {when}{t['name']}（{t['minutes']}分钟 · {t['priority']}）"
                f"—— {blocked_reason(t)}"
            )

    return "\n".join(lines)


# ---------- 校验与增删 ----------

def validate_task(name, minutes_text, priority, start_text=None, date_text=None):
    """校验一条事务的字段。返回 (字段字典, 错误信息)。

    **只做判断，不做动作**：不分配编号、不追加到任何列表、**不读时钟**。
    所以 AI 解析出来的草稿也能拿它验，验完不留下任何痕迹。

    这是全项目唯一的校验入口——命令行、网页、AI 三条路都走这里。

    start_text 留空表示"没定时间"，那条事务会被自动安排到空档里。
    date_text 留空表示"没说哪天"，由 make_task 填成今天。
    """
    if not isinstance(name, str) or not name.strip():
        return None, "事务名不能为空"
    if priority not in PRIORITY_ORDER:
        return None, f"优先级只能是 高/中/低，收到的是「{priority}」"
    try:
        minutes = int(minutes_text)
    except (TypeError, ValueError):
        return None, f"分钟数要是个整数，收到的是「{minutes_text}」"
    if minutes <= 0:
        return None, "分钟数要大于 0"

    start, error = parse_hhmm(start_text)
    if error:
        return None, error

    day, date_error = parse_date(date_text)
    if date_error:
        return None, date_error

    fields = {
        "name": name.strip(),
        "minutes": minutes,
        "priority": priority,
        "done": False,
    }
    # 没定时间就**不写这个字段**——老数据文件里本来就没有它，
    # 保持"有就是有、没有就是没有"，比塞一个 null 干净。
    if start is not None:
        fields["start"] = start
    if day is not None:
        fields["date"] = day
    return fields, None


def make_task(tasks, name, minutes_text, priority, start_text=None,
              date_text=None, today=None):
    """校验 + 分配编号 + 追加进 tasks。返回 (任务, 错误信息)。

    纯函数：不读文件、不打印、不保存。CLI 和网页界面都调它，
    所以两边共用同一套校验规则——网页不可能绕过检查塞进脏数据。

    **没说日期就填今天，而且是此刻就写进数据里。**
    这一条很要紧：如果改成"读取时再看今天几号"，你今天加的
    「写周报」明天就会跑到明天去，后天又跑到后天——永远追着你。
    日期是这条事务的属性，不是查询时才决定的。
    """
    fields, error = validate_task(name, minutes_text, priority,
                                  start_text, date_text)
    if error:
        return None, error

    fields.setdefault("date", today or today_iso())
    task = {"id": max((t["id"] for t in tasks), default=0) + 1, **fields}
    tasks.append(task)
    return task, None


def mark_done(tasks, task_id):
    """把某条任务标记完成。返回 (任务, 错误信息)。同样是纯函数。"""
    for t in tasks:
        if t["id"] == task_id:
            t["done"] = True
            return t, None
    return None, f"没找到编号 {task_id} 的事务"


def describe(t, today=None):
    """一条事务的一行摘要，命令行和列表共用。

    **只在不是今天的时候才写出日期。** 今天的事务占了绝大多数，
    每条都缀个「2026-11-01」纯属噪音；哪天不一样了才值得标出来。
    """
    if today is None:
        today = today_iso()
    day = task_day(t, today)
    day_tag = "" if day == today else f"{friendly_day(day, today)} · "
    when = f" · {to_hhmm(t['start'])} 开始" if t.get("start") is not None else " · 自动安排"
    return f"{t['name']}（{day_tag}{t['minutes']}分钟 · {t['priority']}{when}）"


def take_date_flag(args):
    """从参数里摘出 `--date 2026-11-03`。返回 (剩下的参数, 日期文本, 错误)。

    为什么单独写一个函数、而不是把日期接成第五个位置参数？
    因为位置参数靠**顺序**记，`add "开会" 30 高 14:00 2026-11-03` 里
    日期排在哪一位，你得数着记。带名字的开关（`--date`）自己说明自己，
    而且能跟开始时间任意先后。
    """
    args = list(args)
    if "--date" not in args:
        return args, None, None

    i = args.index("--date")
    if i + 1 >= len(args):
        return None, None, "用法: --date 后面要跟日期，比如 --date 2026-11-03"
    date_text = args[i + 1]
    del args[i:i + 2]
    return args, date_text, None


# ---------- 命令行 ----------

def cmd_add(args):
    args, date_text, error = take_date_flag(args)
    if error:
        print(error)
        return 1
    if len(args) not in (3, 4):
        print('用法: py plan.py add "事务名" 分钟 优先级(高/中/低) '
              '[开始时间 09:30] [--date 2026-11-03]')
        return 1

    today = today_iso()
    tasks = load_tasks()
    task, error = make_task(tasks, *args, date_text=date_text, today=today)
    if error:
        print(error)
        return 1

    save_tasks(tasks)
    print(f"已添加 #{task['id']} {describe(task, today)}")
    return 0


def cmd_done(args):
    if len(args) != 1 or not args[0].isdigit():
        print("用法: py plan.py done 编号")
        return 1

    tasks = load_tasks()
    task, error = mark_done(tasks, int(args[0]))
    if error:
        print(error)
        return 1

    save_tasks(tasks)
    print(f"已完成 #{task['id']} {task['name']}")
    return 0


def cmd_list(_args):
    tasks = load_tasks()
    if not tasks:
        print("还没有任何事务。")
        return 0

    today = today_iso()
    # 按日期、再按编号排。日期是 'YYYY-MM-DD'，**字符串排序就是时间排序**，
    # 这就是当初选这个格式的回报——不用转成日期对象再比。
    for t in sorted(tasks, key=lambda t: (task_day(t, today), t["id"])):
        mark = "✓" if t.get("done") else " "
        print(f"  [{mark}] #{t['id']}  {describe(t, today)}")
    return 0


def cmd_plan(args):
    """看某一天的行程。不写日期就是今天。"""
    if len(args) > 1:
        print("用法: py plan.py plan [日期 2026-11-03]")
        return 1

    today = today_iso()
    day, error = parse_date(args[0] if args else None)
    if error:
        print(error)
        return 1
    day = day or today

    # 先按日期筛，再排程——**schedule 完全不知道"日期"这回事**，
    # 它只管把给它的那几条排进一天。日期是"给哪几条"的问题，
    # 排程是"怎么摆"的问题，两件事分开，各自都测得动。
    scheduled, blocked = schedule(tasks_on(load_tasks(), day, today))
    print(render(scheduled, blocked, day=day, today=today))
    return 0


def main(argv):
    command = argv[0] if argv else "plan"
    rest = argv[1:]

    if command == "add":
        return cmd_add(rest)
    if command == "done":
        return cmd_done(rest)
    if command == "list":
        return cmd_list(rest)
    if command == "plan":
        return cmd_plan(rest)

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
