"""排程逻辑的测试。跑：py test_plan.py

只测纯函数（sort_tasks / schedule / to_hhmm），不碰文件和命令行。
这就是为什么这几个函数不读文件、不打印。
"""

import unittest

from plan import schedule, sort_tasks, to_hhmm


def task(name, minutes, priority="中", done=False, id=1):
    """造一条测试用的事务。"""
    return {
        "id": id,
        "name": name,
        "minutes": minutes,
        "priority": priority,
        "done": done,
    }


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
        scheduled, unscheduled = schedule(
            [task("A", 60, "高"), task("B", 30, "中")],
            day_start=540, day_end=1080,
        )
        self.assertEqual(unscheduled, [])
        self.assertEqual([t["name"] for t in scheduled], ["A", "B"])
        self.assertEqual((scheduled[0]["start"], scheduled[0]["end"]), (540, 600))
        self.assertEqual((scheduled[1]["start"], scheduled[1]["end"]), (600, 630))

    def test_skips_done_tasks(self):
        scheduled, _ = schedule(
            [task("A", 60, "高", done=True), task("B", 30, "高")],
            day_start=540, day_end=1080,
        )
        self.assertEqual([t["name"] for t in scheduled], ["B"])

    def test_overflow_goes_to_unscheduled(self):
        scheduled, unscheduled = schedule(
            [task("A", 60, "高"), task("B", 600, "中")],
            day_start=540, day_end=600,
        )
        self.assertEqual([t["name"] for t in scheduled], ["A"])
        self.assertEqual([t["name"] for t in unscheduled], ["B"])

    def test_small_task_still_fits_after_big_one_fails(self):
        # 大任务塞不下时不该挡住后面能塞下的小任务
        scheduled, unscheduled = schedule(
            [task("大", 600, "高"), task("小", 30, "中")],
            day_start=540, day_end=600,
        )
        self.assertEqual([t["name"] for t in scheduled], ["小"])
        self.assertEqual([t["name"] for t in unscheduled], ["大"])

    def test_nothing_scheduled_when_nothing_fits(self):
        scheduled, unscheduled = schedule(
            [task("大", 600, "高")],
            day_start=540, day_end=600,
        )
        self.assertEqual(scheduled, [])
        self.assertEqual([t["name"] for t in unscheduled], ["大"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
