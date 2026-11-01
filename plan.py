"""time —— 把你输入的事务排成一天的行程。

用法:
    py plan.py add "写周报" 60 高           添加事务（名称 分钟 优先级）
    py plan.py add "开会" 30 高 14:00       带开始时间（可选）
    py plan.py done 1                       标记某条事务已完成
    py plan.py list                         列出全部事务
    py plan.py                              显示今天的行程

两种事务：
    · **定了时间的**（写了开始时间）——固定在那个时段，会参与冲突仲裁
    · **没定时间的**——自动塞进空档里

冲突仲裁的规则：两个事务时段重叠时，优先级高的赢；
优先级相同时**先建的赢**（先入原则）；输的进待办栏。

DAY_START/DAY_END 只用来「自动找空档」和显示，**不限制事务能排在哪**——
你写 20:00 的事务就排 20:00。
"""

import json
import sys
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


def render(scheduled, blocked, day_start=DAY_START, day_end=DAY_END):
    if not scheduled and not blocked:
        return ('今天没有待办事务。用 '
                '`py plan.py add "事务名" 分钟 高/中/低 [09:30]` 添加。')

    lines = ["今天的行程", ""]

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

def validate_task(name, minutes_text, priority, start_text=None):
    """校验一条事务的字段。返回 (字段字典, 错误信息)。

    **只做判断，不做动作**：不分配编号、不追加到任何列表。
    所以 AI 解析出来的草稿也能拿它验，验完不留下任何痕迹。

    这是全项目唯一的校验入口——命令行、网页、AI 三条路都走这里。

    start_text 留空表示"没定时间"，那条事务会被自动安排到空档里。
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
    return fields, None


def make_task(tasks, name, minutes_text, priority, start_text=None):
    """校验 + 分配编号 + 追加进 tasks。返回 (任务, 错误信息)。

    纯函数：不读文件、不打印、不保存。CLI 和网页界面都调它，
    所以两边共用同一套校验规则——网页不可能绕过检查塞进脏数据。
    """
    fields, error = validate_task(name, minutes_text, priority, start_text)
    if error:
        return None, error

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


def describe(t):
    """一条事务的一行摘要，命令行和列表共用。"""
    when = f" · {to_hhmm(t['start'])} 开始" if t.get("start") is not None else " · 自动安排"
    return f"{t['name']}（{t['minutes']}分钟 · {t['priority']}{when}）"


# ---------- 命令行 ----------

def cmd_add(args):
    if len(args) not in (3, 4):
        print('用法: py plan.py add "事务名" 分钟 优先级(高/中/低) [开始时间 09:30]')
        return 1

    tasks = load_tasks()
    task, error = make_task(tasks, *args)
    if error:
        print(error)
        return 1

    save_tasks(tasks)
    print(f"已添加 #{task['id']} {describe(task)}")
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

    for t in tasks:
        mark = "✓" if t.get("done") else " "
        print(f"  [{mark}] #{t['id']}  {describe(t)}")
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
        print(render(*schedule(load_tasks())))
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
