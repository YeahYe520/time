"""time —— 把你输入的事务排成一天的行程。

用法:
    py plan.py add "写周报" 60 高    添加事务（名称 分钟 优先级）
    py plan.py done 1                标记某条事务已完成
    py plan.py list                  列出全部事务
    py plan.py                       显示今天的行程

v0.1：命令行 + JSON 文件，零依赖，纯标准库。
"""

import json
import sys
from pathlib import Path

# Windows 控制台默认用 GBK 输出，中文在 Git Bash 里会变乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TASKS_FILE = Path(__file__).parent / "tasks.json"

# 一天的可用时间窗。v0.1 先写死，之后做成可配置。
DAY_START = 9 * 60    # 09:00
DAY_END = 18 * 60     # 18:00

PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}


def to_hhmm(minutes):
    """从零点起的分钟数 -> '09:00'。540 -> '09:00'"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def load_tasks():
    if not TASKS_FILE.exists():
        return []
    return json.loads(TASKS_FILE.read_text(encoding="utf-8"))


def save_tasks(tasks):
    TASKS_FILE.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sort_tasks(tasks):
    """优先级高的排前面；同优先级下短任务排前面，这样能塞进更多事。"""
    return sorted(
        tasks,
        key=lambda t: (PRIORITY_ORDER.get(t["priority"], 99), t["minutes"]),
    )


def schedule(tasks, day_start=DAY_START, day_end=DAY_END):
    """把未完成的事务依次塞进 [day_start, day_end)。

    返回 (已排, 排不下)。排不下的事务不推进时间指针，
    所以排在它后面的短任务仍然有机会塞进去。

    纯函数：不读文件、不打印，所以测得动。
    """
    cursor = day_start
    scheduled, unscheduled = [], []

    for task in sort_tasks(tasks):
        if task.get("done"):
            continue
        end = cursor + task["minutes"]
        if end <= day_end:
            scheduled.append({**task, "start": cursor, "end": end})
            cursor = end
        else:
            unscheduled.append(task)

    return scheduled, unscheduled


def render(scheduled, unscheduled, day_start=DAY_START, day_end=DAY_END):
    if not scheduled and not unscheduled:
        return '今天没有待办事务。用 `py plan.py add "事务名" 分钟 高/中/低` 添加。'

    lines = [f"今天的行程（{to_hhmm(day_start)}–{to_hhmm(day_end)}）", ""]

    for t in scheduled:
        lines.append(
            f"  {to_hhmm(t['start'])}–{to_hhmm(t['end'])}  "
            f"{t['name']}（{t['minutes']}分钟 · {t['priority']}）"
        )

    used = sum(t["minutes"] for t in scheduled)
    lines += [
        "",
        f"已排 {len(scheduled)} 件 · 占用 {used} 分钟 · "
        f"空闲 {day_end - day_start - used} 分钟",
    ]

    if unscheduled:
        lines += ["", "今天排不下，考虑挪到明天或砍掉："]
        for t in unscheduled:
            lines.append(f"  {t['name']}（{t['minutes']}分钟 · {t['priority']}）")

    return "\n".join(lines)


# ---------- 命令行 ----------

def cmd_add(args):
    if len(args) != 3:
        print('用法: py plan.py add "事务名" 分钟 优先级(高/中/低)')
        return 1

    name, minutes_text, priority = args

    if priority not in PRIORITY_ORDER:
        print(f"优先级只能是 高/中/低，收到的是「{priority}」")
        return 1
    try:
        minutes = int(minutes_text)
    except ValueError:
        print(f"分钟数要是个整数，收到的是「{minutes_text}」")
        return 1
    if minutes <= 0:
        print("分钟数要大于 0")
        return 1

    tasks = load_tasks()
    new_id = max((t["id"] for t in tasks), default=0) + 1
    tasks.append({
        "id": new_id,
        "name": name,
        "minutes": minutes,
        "priority": priority,
        "done": False,
    })
    save_tasks(tasks)
    print(f"已添加 #{new_id} {name}（{minutes}分钟 · {priority}）")
    return 0


def cmd_done(args):
    if len(args) != 1 or not args[0].isdigit():
        print("用法: py plan.py done 编号")
        return 1

    target = int(args[0])
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == target:
            t["done"] = True
            save_tasks(tasks)
            print(f"已完成 #{target} {t['name']}")
            return 0

    print(f"没找到编号 {target} 的事务")
    return 1


def cmd_list(_args):
    tasks = load_tasks()
    if not tasks:
        print("还没有任何事务。")
        return 0

    for t in tasks:
        mark = "✓" if t.get("done") else " "
        print(f"  [{mark}] #{t['id']}  {t['name']}（{t['minutes']}分钟 · {t['priority']}）")
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
