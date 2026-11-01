"""排程逻辑的测试。跑：py test_plan.py

只测纯函数（sort_tasks / schedule / to_hhmm），不碰文件和命令行。
这就是为什么这几个函数不读文件、不打印。
"""

import unittest
from datetime import date

from plan import (
    blocked_reason,
    describe,
    find_gap,
    friendly_day,
    make_task,
    mark_done,
    overlaps,
    parse_date,
    parse_day_offset,
    parse_hhmm,
    render,
    schedule,
    shift_day,
    sort_for_claiming,
    sort_tasks,
    take_date_flag,
    task_day,
    tasks_on,
    to_hhmm,
    today_iso,
    validate_task,
    weekday_cn,
)


def task(name, minutes, priority="中", done=False, id=1, start=None, date=None):
    """造一条测试用的事务。

    start 是"从零点起的分钟数"，None 表示没定时间（会被自动安排）。
    date 是 'YYYY-MM-DD'，None 表示不写这个键（模拟 v0.5 之前的老数据）。
    """
    t = {
        "id": id,
        "name": name,
        "minutes": minutes,
        "priority": priority,
        "done": done,
    }
    if start is not None:
        t["start"] = start
    if date is not None:
        t["date"] = date
    return t


# 测试里所有"今天"都用这个常量，绝不调 today_iso()。
# 否则测试结果会随真实日期变，哪天跑失败都说不清是代码坏了还是日子不对。
TODAY = "2026-11-01"        # 周日
TOMORROW = "2026-11-02"     # 周一
YESTERDAY = "2026-10-31"    # 周六


class TestToHhmm(unittest.TestCase):
    def test_whole_hour(self):
        self.assertEqual(to_hhmm(540), "09:00")

    def test_with_minutes(self):
        self.assertEqual(to_hhmm(615), "10:15")

    def test_midnight(self):
        self.assertEqual(to_hhmm(0), "00:00")


class TestSortTasks(unittest.TestCase):
    def test_high_priority_first(self):
        tasks = [task("低", 10, "低"), task("高", 10, "高"), task("中", 10, "中")]
        self.assertEqual([t["name"] for t in sort_tasks(tasks)], ["高", "中", "低"])

    def test_shorter_first_within_same_priority(self):
        tasks = [task("长", 90, "高"), task("短", 15, "高")]
        self.assertEqual([t["name"] for t in sort_tasks(tasks)], ["短", "长"])


class TestSchedule(unittest.TestCase):
    def test_packs_tasks_back_to_back(self):
        scheduled, blocked = schedule(
            [task("A", 60, "高"), task("B", 30, "中")],
            day_start=540, day_end=1080,
        )
        self.assertEqual(blocked, [])
        self.assertEqual([t["name"] for t in scheduled], ["A", "B"])
        self.assertEqual((scheduled[0]["start"], scheduled[0]["end"]), (540, 600))
        self.assertEqual((scheduled[1]["start"], scheduled[1]["end"]), (600, 630))

    def test_skips_done_tasks(self):
        scheduled, _ = schedule(
            [task("A", 60, "高", done=True), task("B", 30, "高")],
            day_start=540, day_end=1080,
        )
        self.assertEqual([t["name"] for t in scheduled], ["B"])

    def test_overflow_goes_to_blocked(self):
        scheduled, blocked = schedule(
            [task("A", 60, "高"), task("B", 600, "中")],
            day_start=540, day_end=600,
        )
        self.assertEqual([t["name"] for t in scheduled], ["A"])
        self.assertEqual([t["name"] for t in blocked], ["B"])

    def test_small_task_still_fits_after_big_one_fails(self):
        # 大任务塞不下时不该挡住后面能塞下的小任务
        scheduled, blocked = schedule(
            [task("大", 600, "高"), task("小", 30, "中")],
            day_start=540, day_end=600,
        )
        self.assertEqual([t["name"] for t in scheduled], ["小"])
        self.assertEqual([t["name"] for t in blocked], ["大"])

    def test_nothing_scheduled_when_nothing_fits(self):
        scheduled, blocked = schedule(
            [task("大", 600, "高")],
            day_start=540, day_end=600,
        )
        self.assertEqual(scheduled, [])
        self.assertEqual([t["name"] for t in blocked], ["大"])


class TestMakeTask(unittest.TestCase):
    """校验规则。CLI 和网页共用这一套，所以这里测到的就是网页也守的规矩。"""

    def test_adds_task_and_returns_it(self):
        tasks = []
        task, error = make_task(tasks, "写周报", "60", "高")
        self.assertIsNone(error)
        self.assertEqual(task["name"], "写周报")
        self.assertEqual(task["minutes"], 60)
        self.assertEqual(task["priority"], "高")
        self.assertFalse(task["done"])
        self.assertEqual(tasks, [task])          # 确实追加进去了

    def test_id_increments(self):
        tasks = []
        first, _ = make_task(tasks, "A", "10", "低")
        second, _ = make_task(tasks, "B", "10", "低")
        self.assertEqual((first["id"], second["id"]), (1, 2))

    def test_strips_whitespace_from_name(self):
        task, _ = make_task([], "  写周报  ", "60", "高")
        self.assertEqual(task["name"], "写周报")

    def test_accepts_integer_minutes(self):
        # 网页传过来的是 JSON 数字，不是字符串
        task, error = make_task([], "写周报", 60, "高")
        self.assertIsNone(error)
        self.assertEqual(task["minutes"], 60)

    def test_rejects_bad_priority(self):
        tasks = []
        task, error = make_task(tasks, "写周报", "60", "很高")
        self.assertIsNone(task)
        self.assertIn("优先级", error)
        self.assertEqual(tasks, [])              # 出错时不能污染列表

    def test_rejects_non_numeric_minutes(self):
        task, error = make_task([], "写周报", "一小时", "高")
        self.assertIsNone(task)
        self.assertIn("整数", error)

    def test_rejects_zero_and_negative_minutes(self):
        for bad in ("0", "-30"):
            with self.subTest(minutes=bad):
                task, error = make_task([], "写周报", bad, "高")
                self.assertIsNone(task)
                self.assertIn("大于 0", error)

    def test_rejects_empty_name(self):
        for bad in ("", "   ", None):
            with self.subTest(name=bad):
                task, error = make_task([], bad, "60", "高")
                self.assertIsNone(task)
                self.assertIn("不能为空", error)


class TestValidateTask(unittest.TestCase):
    """只判断、不动作的那一层。命令行、网页、AI 三条路都走它。"""

    def test_returns_fields_without_id(self):
        # 关键：不给编号。给了就说明"判断"和"分配编号"又揉回去了
        fields, error = validate_task("写周报", "60", "高")
        self.assertIsNone(error)
        self.assertEqual(
            fields,
            {"name": "写周报", "minutes": 60, "priority": "高", "done": False},
        )

    def test_has_no_side_effects(self):
        # 传进去的东西不会被改（这里根本没有东西可改，正是重点）
        first, _ = validate_task("写周报", "60", "高")
        second, _ = validate_task("写周报", "60", "高")
        self.assertEqual(first, second)

    def test_rejects_bad_priority(self):
        fields, error = validate_task("写周报", "60", "很高")
        self.assertIsNone(fields)
        self.assertIn("优先级", error)

    def test_rejects_non_numeric_minutes(self):
        fields, error = validate_task("写周报", "一小时", "高")
        self.assertIsNone(fields)
        self.assertIn("整数", error)

    def test_rejects_non_positive_minutes(self):
        for bad in ("0", "-30", 0, -1):
            with self.subTest(minutes=bad):
                fields, error = validate_task("写周报", bad, "高")
                self.assertIsNone(fields)
                self.assertIn("大于 0", error)

    def test_rejects_empty_name(self):
        for bad in ("", "   ", None, 42):
            with self.subTest(name=bad):
                fields, error = validate_task(bad, "60", "高")
                self.assertIsNone(fields)
                self.assertIn("不能为空", error)


class TestMarkDone(unittest.TestCase):
    def test_marks_matching_task(self):
        tasks = [task("A", 10, id=1), task("B", 10, id=2)]
        found, error = mark_done(tasks, 2)
        self.assertIsNone(error)
        self.assertEqual(found["name"], "B")
        self.assertTrue(tasks[1]["done"])
        self.assertFalse(tasks[0]["done"])       # 只动目标那条

    def test_missing_id_returns_error(self):
        tasks = [task("A", 10, id=1)]
        found, error = mark_done(tasks, 99)
        self.assertIsNone(found)
        self.assertIn("99", error)


class TestParseHhmm(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(parse_hhmm("09:30"), (570, None))

    def test_single_digit_hour(self):
        self.assertEqual(parse_hhmm("9:30"), (570, None))

    def test_midnight(self):
        self.assertEqual(parse_hhmm("00:00"), (0, None))

    def test_blank_means_no_time(self):
        # 留空不是错误，是"没定时间"
        for blank in (None, "", "   "):
            with self.subTest(text=blank):
                self.assertEqual(parse_hhmm(blank), (None, None))

    def test_accepts_integer_minutes(self):
        # AI 有时直接给分钟数而不是 'HH:MM'
        self.assertEqual(parse_hhmm(570), (570, None))

    def test_rejects_bool(self):
        # bool 是 int 的子类，不先拦的话 True 会被当成第 1 分钟
        value, error = parse_hhmm(True)
        self.assertIsNone(value)
        self.assertIn("格式不对", error)

    def test_rejects_missing_colon(self):
        value, error = parse_hhmm("0930")
        self.assertIsNone(value)
        self.assertIn("09:30", error)

    def test_rejects_out_of_range(self):
        for bad in ("25:00", "12:60", "99:99"):
            with self.subTest(text=bad):
                value, error = parse_hhmm(bad)
                self.assertIsNone(value)
                self.assertIn("超出范围", error)

    def test_rejects_garbage(self):
        value, error = parse_hhmm("下午三点")
        self.assertIsNone(value)
        self.assertIn("09:30", error)


class TestOverlaps(unittest.TestCase):
    def test_partial_overlap(self):
        self.assertTrue(overlaps(540, 600, 570, 630))

    def test_one_contains_other(self):
        self.assertTrue(overlaps(540, 660, 570, 600))

    def test_touching_is_not_overlap(self):
        # 10:00 结束和 10:00 开始能共存，
        # 否则每个事务之间都得强行留一分钟空隙
        self.assertFalse(overlaps(540, 600, 600, 660))
        self.assertFalse(overlaps(600, 660, 540, 600))

    def test_disjoint(self):
        self.assertFalse(overlaps(540, 600, 700, 760))


class TestFindGap(unittest.TestCase):
    def test_empty_day_starts_at_window_start(self):
        self.assertEqual(find_gap([], 60, 540, 1080), 540)

    def test_gap_before_first_block(self):
        self.assertEqual(find_gap([(600, 660, "A")], 60, 540, 1080), 540)

    def test_gap_between_blocks(self):
        occupied = [(540, 600, "A"), (700, 760, "B")]
        self.assertEqual(find_gap(occupied, 60, 540, 1080), 600)

    def test_returns_none_when_nothing_fits(self):
        self.assertIsNone(find_gap([(540, 600, "A")], 600, 540, 600))

    def test_ignores_blocks_outside_window(self):
        # 晚上 8 点的会议不该挡住早上的空档
        self.assertEqual(find_gap([(1200, 1260, "晚")], 60, 540, 1080), 540)

    def test_handles_unsorted_input(self):
        occupied = [(700, 760, "B"), (540, 600, "A")]
        self.assertEqual(find_gap(occupied, 60, 540, 1080), 600)


class TestSortForClaiming(unittest.TestCase):
    def test_priority_first(self):
        tasks = [task("低", 10, "低", id=1), task("高", 10, "高", id=2)]
        self.assertEqual(
            [t["name"] for t in sort_for_claiming(tasks)], ["高", "低"]
        )

    def test_earlier_id_first_within_same_priority(self):
        # 这就是「先入原则」：同级时 id 小的先占位
        tasks = [task("后建", 10, "高", id=5), task("先建", 10, "高", id=2)]
        self.assertEqual(
            [t["name"] for t in sort_for_claiming(tasks)], ["先建", "后建"]
        )

    def test_ignores_duration(self):
        # 占位顺序不看时长，看优先级和先来后到
        tasks = [task("长", 120, "高", id=1), task("短", 10, "高", id=2)]
        self.assertEqual(
            [t["name"] for t in sort_for_claiming(tasks)], ["长", "短"]
        )


class TestConflictArbitration(unittest.TestCase):
    """核心：区间冲突仲裁。"""

    def test_higher_priority_wins(self):
        # 开会(高) 09:30 撞上 写周报(中) 09:00–10:00 -> 开会赢
        tasks = [
            task("写周报", 60, "中", id=1, start=540),
            task("开会", 30, "高", id=2, start=570),
        ]
        scheduled, blocked = schedule(tasks)
        self.assertEqual([t["name"] for t in scheduled], ["开会"])
        self.assertEqual([t["name"] for t in blocked], ["写周报"])
        self.assertEqual(blocked[0]["blocked_by"], "开会")

    def test_same_priority_first_come_wins(self):
        tasks = [
            task("先建的", 60, "高", id=1, start=540),
            task("后建的", 60, "高", id=2, start=540),
        ]
        scheduled, blocked = schedule(tasks)
        self.assertEqual([t["name"] for t in scheduled], ["先建的"])
        self.assertEqual([t["name"] for t in blocked], ["后建的"])

    def test_later_but_higher_priority_still_wins(self):
        # 先入原则只在同级时生效，不能压过优先级
        tasks = [
            task("先建的低", 60, "低", id=1, start=540),
            task("后建的高", 60, "高", id=2, start=540),
        ]
        scheduled, blocked = schedule(tasks)
        self.assertEqual([t["name"] for t in scheduled], ["后建的高"])
        self.assertEqual([t["name"] for t in blocked], ["先建的低"])

    def test_non_overlapping_both_survive(self):
        tasks = [
            task("A", 60, "中", id=1, start=540),
            task("B", 60, "中", id=2, start=600),      # 首尾相接
        ]
        scheduled, blocked = schedule(tasks)
        self.assertEqual([t["name"] for t in scheduled], ["A", "B"])
        self.assertEqual(blocked, [])

    def test_loser_does_not_occupy_time(self):
        # 被挤掉的不能连锁影响后面的判定
        tasks = [
            task("输家", 60, "低", id=1, start=540),
            task("赢家", 60, "高", id=2, start=540),
            task("无关的", 60, "中", id=3, start=600),
        ]
        scheduled, blocked = schedule(tasks)
        self.assertEqual([t["name"] for t in scheduled], ["赢家", "无关的"])
        self.assertEqual([t["name"] for t in blocked], ["输家"])

    def test_auto_flag_marks_tool_placed_tasks(self):
        tasks = [
            task("定时的", 60, "高", id=1, start=600),
            task("没定时的", 30, "中", id=2),
        ]
        scheduled, _ = schedule(tasks)
        by_name = {t["name"]: t for t in scheduled}
        self.assertFalse(by_name["定时的"]["auto"])
        self.assertTrue(by_name["没定时的"]["auto"])

    def test_fixed_task_can_go_outside_day_window(self):
        # 时间窗取消硬限制：晚上 8 点的事务照排
        tasks = [task("晚间", 60, "高", id=1, start=1200)]
        scheduled, blocked = schedule(tasks)
        self.assertEqual([t["name"] for t in scheduled], ["晚间"])
        self.assertEqual(scheduled[0]["start"], 1200)
        self.assertEqual(blocked, [])

    def test_floating_task_fills_gap_around_fixed(self):
        tasks = [
            task("定时的", 60, "高", id=1, start=540),
            task("没定时的", 60, "中", id=2),
        ]
        scheduled, _ = schedule(tasks)
        by_name = {t["name"]: t for t in scheduled}
        self.assertEqual(by_name["没定时的"]["start"], 600)   # 紧跟在后面

    def test_blocked_floating_task_has_no_start(self):
        tasks = [task("超大", 600, "高", id=1)]
        _, blocked = schedule(tasks, day_start=540, day_end=600)
        self.assertEqual(len(blocked), 1)
        self.assertIsNone(blocked[0]["start"])
        self.assertIsNone(blocked[0]["blocked_by"])   # 不是被谁挤的，是没空档


class TestBlockedReason(unittest.TestCase):
    def test_conflict_reason(self):
        self.assertIn("开会", blocked_reason({"blocked_by": "开会", "minutes": 30}))

    def test_no_gap_reason(self):
        self.assertIn("空档", blocked_reason({"blocked_by": None, "minutes": 30}))


class TestValidateTaskWithStart(unittest.TestCase):
    def test_start_is_optional(self):
        fields, error = validate_task("写周报", "60", "高")
        self.assertIsNone(error)
        self.assertNotIn("start", fields)     # 没定时间就不写这个字段

    def test_start_is_parsed(self):
        fields, _ = validate_task("写周报", "60", "高", "09:30")
        self.assertEqual(fields["start"], 570)

    def test_blank_start_means_no_time(self):
        for blank in ("", "   ", None):
            with self.subTest(text=blank):
                fields, error = validate_task("写周报", "60", "高", blank)
                self.assertIsNone(error)
                self.assertNotIn("start", fields)

    def test_rejects_bad_start(self):
        fields, error = validate_task("写周报", "60", "高", "25:00")
        self.assertIsNone(fields)
        self.assertIn("超出范围", error)


class TestParseDate(unittest.TestCase):
    def test_standard_form(self):
        self.assertEqual(parse_date("2026-11-03"), ("2026-11-03", None))

    def test_accepts_slashes(self):
        # 手写日期很多人是 2026/11/3 这个习惯
        self.assertEqual(parse_date("2026/11/03"), ("2026-11-03", None))

    def test_pads_single_digits(self):
        # '2026-1-3' 补零成 '2026-01-03'——不补的话字符串排序就乱了
        self.assertEqual(parse_date("2026-1-3"), ("2026-01-03", None))

    def test_accepts_compact_form(self):
        # ISO 8601 的紧凑写法，Python 3.11 起 fromisoformat 直接认
        self.assertEqual(parse_date("20261103"), ("2026-11-03", None))

    def test_accepts_int(self):
        self.assertEqual(parse_date(20261103), ("2026-11-03", None))

    def test_output_is_always_padded(self):
        # 宽进严出：进去的花样再多，出来的只有一种形状
        for written in ("2026-11-03", "2026/11/03", "2026-11-3", "20261103", 20261103):
            with self.subTest(written=written):
                self.assertEqual(parse_date(written)[0], "2026-11-03")

    def test_strips_whitespace(self):
        self.assertEqual(parse_date("  2026-11-03  "), ("2026-11-03", None))

    def test_blank_means_no_date(self):
        for blank in ("", "   ", None):
            with self.subTest(text=blank):
                self.assertEqual(parse_date(blank), (None, None))

    def test_accepts_date_object(self):
        self.assertEqual(parse_date(date(2026, 11, 3)), ("2026-11-03", None))

    def test_rejects_bool(self):
        # bool 是 int 的子类，不拦的话 True 会走到 str() 那条路上去
        day, error = parse_date(True)
        self.assertIsNone(day)
        self.assertIsNotNone(error)

    def test_rejects_garbage(self):
        # 注意 2026-11-31：格式对、日期不存在，fromisoformat 自己会拦
        for bad in ("明天", "2026-13-01", "2026-11-31", "11/03", 202611):
            with self.subTest(text=bad):
                day, error = parse_date(bad)
                self.assertIsNone(day)
                self.assertIn("日期", error)

    def test_does_not_read_clock(self):
        # 纯函数：同一输入永远同一输出
        self.assertEqual(parse_date("2026-11-03"), parse_date("2026-11-03"))


class TestTodayIso(unittest.TestCase):
    def test_looks_like_iso_date(self):
        # 只检查形状，不检查具体是哪天——检查具体哪天，明天就跑失败了
        self.assertEqual(parse_date(today_iso())[1], None)


class TestTaskDay(unittest.TestCase):
    def test_uses_explicit_date(self):
        self.assertEqual(task_day(task("A", 10, date=TOMORROW), TODAY), TOMORROW)

    def test_legacy_task_without_date_counts_as_today(self):
        # v0.5 之前的数据没有 date 键，那时候所有事务都只存在于"今天"
        self.assertEqual(task_day(task("A", 10), TODAY), TODAY)


class TestTasksOn(unittest.TestCase):
    def setUp(self):
        self.tasks = [
            task("今天的事", 10, id=1, date=TODAY),
            task("明天的事", 10, id=2, date=TOMORROW),
            task("老数据", 10, id=3),                  # 没有 date 键
            task("也是明天", 10, id=4, date=TOMORROW),
        ]

    def test_filters_by_day(self):
        names = [t["name"] for t in tasks_on(self.tasks, TOMORROW, TODAY)]
        self.assertEqual(names, ["明天的事", "也是明天"])

    def test_legacy_tasks_show_up_today(self):
        names = [t["name"] for t in tasks_on(self.tasks, TODAY, TODAY)]
        self.assertEqual(names, ["今天的事", "老数据"])

    def test_other_day_is_empty(self):
        self.assertEqual(tasks_on(self.tasks, "2026-12-01", TODAY), [])

    def test_does_not_mutate_input(self):
        before = [dict(t) for t in self.tasks]
        tasks_on(self.tasks, TOMORROW, TODAY)
        self.assertEqual(self.tasks, before)


class TestShiftDay(unittest.TestCase):
    def test_forward(self):
        self.assertEqual(shift_day(TODAY, 1), TOMORROW)

    def test_backward(self):
        self.assertEqual(shift_day(TODAY, -1), YESTERDAY)

    def test_zero_is_identity(self):
        self.assertEqual(shift_day(TODAY, 0), TODAY)

    def test_crosses_month(self):
        self.assertEqual(shift_day("2026-11-30", 1), "2026-12-01")

    def test_crosses_year(self):
        self.assertEqual(shift_day("2026-12-31", 1), "2027-01-01")


class TestWeekdayCn(unittest.TestCase):
    def test_sunday(self):
        self.assertEqual(weekday_cn(TODAY), "周日")

    def test_monday(self):
        self.assertEqual(weekday_cn(TOMORROW), "周一")

    def test_saturday(self):
        self.assertEqual(weekday_cn(YESTERDAY), "周六")


class TestFriendlyDay(unittest.TestCase):
    def test_today(self):
        self.assertEqual(friendly_day(TODAY, TODAY), "今天")

    def test_tomorrow(self):
        self.assertEqual(friendly_day(TOMORROW, TODAY), "明天")

    def test_yesterday(self):
        self.assertEqual(friendly_day(YESTERDAY, TODAY), "昨天")

    def test_far_day_spells_it_out(self):
        self.assertEqual(friendly_day("2026-11-03", TODAY), "11月3日 周二")

    def test_far_day_does_not_pad_month(self):
        # 「11月3日」比「11月03日」像人话
        self.assertNotIn("03", friendly_day("2026-11-03", TODAY))


class TestParseDayOffset(unittest.TestCase):
    """AI 给的是"距今几天"，换成日期由这里做。"""

    def test_zero_is_today(self):
        self.assertEqual(parse_day_offset(0, TODAY), (TODAY, None))

    def test_one_is_tomorrow(self):
        self.assertEqual(parse_day_offset(1, TODAY), (TOMORROW, None))

    def test_negative_is_the_past(self):
        self.assertEqual(parse_day_offset(-1, TODAY), (YESTERDAY, None))

    def test_crosses_month(self):
        # 11-30 加一天是 12-01，靠的是 datetime，不是自己写进位
        self.assertEqual(parse_day_offset(1, "2026-11-30"), ("2026-12-01", None))

    def test_numeric_string_is_accepted(self):
        # AI 有时给数字有时给字符串
        self.assertEqual(parse_day_offset("2", TODAY), ("2026-11-03", None))

    def test_blank_means_no_date(self):
        for blank in (None,):
            with self.subTest(text=blank):
                self.assertEqual(parse_day_offset(blank, TODAY), (None, None))

    def test_rejects_bool(self):
        # bool 是 int 的子类，不拦的话 True 会变成"差 1 天"
        day, error = parse_day_offset(True, TODAY)
        self.assertIsNone(day)
        self.assertIsNotNone(error)

    def test_rejects_fractional_number(self):
        # int(3.7) 会**悄悄截成 3**——这种静默的错误最要命
        day, error = parse_day_offset(3.7, TODAY)
        self.assertIsNone(day)
        self.assertIn("没看懂", error)

    def test_accepts_whole_float(self):
        self.assertEqual(parse_day_offset(1.0, TODAY), (TOMORROW, None))

    def test_rejects_non_numeric(self):
        for bad in ("明天", "下周三", "3天"):
            with self.subTest(text=bad):
                day, error = parse_day_offset(bad, TODAY)
                self.assertIsNone(day)
                self.assertIn("没看懂", error)

    def test_rejects_absurd_offsets(self):
        # 一年以后 / 一个月以前都超出任何正常计划，八成是 AI 算错了。
        # **宁可不要这个日期，也不能存一个荒唐的日子。**
        for bad in (366, -31, 100000):
            with self.subTest(offset=bad):
                day, error = parse_day_offset(bad, TODAY)
                self.assertIsNone(day)
                self.assertIn("离谱", error)

    def test_boundaries_are_inclusive(self):
        self.assertIsNotNone(parse_day_offset(365, TODAY)[0])
        self.assertIsNotNone(parse_day_offset(-30, TODAY)[0])


class TestMakeTaskWithDate(unittest.TestCase):
    def test_no_date_fills_in_today(self):
        t, error = make_task([], "写周报", "60", "高", today=TODAY)
        self.assertIsNone(error)
        self.assertEqual(t["date"], TODAY)

    def test_explicit_date_wins(self):
        t, _ = make_task([], "评审", "60", "中", date_text=TOMORROW, today=TODAY)
        self.assertEqual(t["date"], TOMORROW)

    def test_date_is_frozen_at_creation(self):
        """**这条是整个日期功能的核心。**

        今天加的「写周报」写死成今天。明天再用 tomorrow 当"今天"
        去读它，它还在原来的日子——不会跟着用户往前滚。
        如果当初写成"读取时现算今天"，这条就会失败。
        """
        t, _ = make_task([], "写周报", "60", "高", today=TODAY)
        self.assertEqual(t["date"], TODAY)
        self.assertEqual(task_day(t, TOMORROW), TODAY)   # 第二天再看，还在 11-01

    def test_rejects_bad_date(self):
        t, error = make_task([], "评审", "60", "中", date_text="明天", today=TODAY)
        self.assertIsNone(t)
        self.assertIn("日期", error)

    def test_date_and_start_together(self):
        t, error = make_task([], "评审", "90", "中", "14:00", TOMORROW, TODAY)
        self.assertIsNone(error)
        self.assertEqual((t["date"], t["start"]), (TOMORROW, 840))


class TestTakeDateFlag(unittest.TestCase):
    def test_extracts_flag(self):
        rest, day, error = take_date_flag(["开会", "30", "高", "--date", TOMORROW])
        self.assertEqual((rest, day, error), (["开会", "30", "高"], TOMORROW, None))

    def test_flag_can_come_first(self):
        rest, day, _ = take_date_flag(["--date", TOMORROW, "开会", "30", "高"])
        self.assertEqual((rest, day), (["开会", "30", "高"], TOMORROW))

    def test_no_flag_passes_through(self):
        rest, day, error = take_date_flag(["开会", "30", "高"])
        self.assertEqual((rest, day, error), (["开会", "30", "高"], None, None))

    def test_flag_without_value_is_an_error(self):
        rest, day, error = take_date_flag(["开会", "30", "高", "--date"])
        self.assertIsNone(rest)
        self.assertIn("--date", error)

    def test_does_not_mutate_input(self):
        args = ["开会", "30", "高", "--date", TOMORROW]
        take_date_flag(args)
        self.assertEqual(len(args), 5)


class TestDescribeWithDate(unittest.TestCase):
    def test_today_has_no_date_tag(self):
        # 今天的事务是绝大多数，每条都缀个日期纯属噪音
        line = describe(task("写周报", 60, date=TODAY), TODAY)
        self.assertNotIn("11月", line)
        self.assertIn("写周报", line)

    def test_other_day_shows_date(self):
        line = describe(task("评审", 90, date=TOMORROW), TODAY)
        self.assertIn("明天", line)

    def test_legacy_task_has_no_date_tag(self):
        self.assertNotIn("11月", describe(task("老数据", 60), TODAY))


class TestRenderWithDay(unittest.TestCase):
    def test_title_uses_friendly_day(self):
        text = render([], [], day=TOMORROW, today=TODAY)
        self.assertIn("明天", text)

    def test_empty_day_says_so(self):
        self.assertIn("没有安排", render([], [], day=TODAY, today=TODAY))

    def test_legacy_call_still_works(self):
        # 老的调用方式（只传两个列表）不能坏
        scheduled, blocked = schedule([task("写周报", 60)])
        self.assertIn("写周报", render(scheduled, blocked))


class TestScheduleIsPerDay(unittest.TestCase):
    def test_two_days_do_not_collide(self):
        """不同天的事务**各排各的**——都从 09:00 开始，谁也不挤谁。"""
        tasks = [
            task("今天的会", 60, "高", id=1, start=540, date=TODAY),
            task("明天的会", 60, "高", id=2, start=540, date=TOMORROW),
        ]
        for day in (TODAY, TOMORROW):
            with self.subTest(day=day):
                scheduled, blocked = schedule(tasks_on(tasks, day, TODAY))
                self.assertEqual(len(scheduled), 1)
                self.assertEqual(blocked, [])

    def test_same_day_still_collides(self):
        tasks = [
            task("先建的", 60, "高", id=1, start=540, date=TODAY),
            task("后建的", 60, "高", id=2, start=540, date=TODAY),
        ]
        scheduled, blocked = schedule(tasks_on(tasks, TODAY, TODAY))
        self.assertEqual([t["name"] for t in scheduled], ["先建的"])
        self.assertEqual([t["name"] for t in blocked], ["后建的"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
