"""app.py 的口令门测试。跑：.venv/Scripts/python.exe test_app.py

**注意用 venv 的 Python，不是 `py`。** Flask 装在 .venv 里，
系统那个 py 里没有——这就是"依赖隔离"的实际后果。

这里不用真的起服务器：Flask 自带一个测试客户端，直接喂请求进去，
走的是完整的请求处理流程（路由、before_request 钩子全都会跑到），
但不碰网络、不占端口。所以它跑得又快又稳。
"""

import base64
import unittest

import app as app_module
from plan import shift_day, today_iso


def basic(password, username="time"):
    """拼一个 HTTP Basic 认证头。

    格式就是 `Basic ` + base64("用户名:密码")。
    **注意它只是编码，不是加密**——base64 谁都能解开，
    所以 HTTP 上跑 Basic 认证，口令在网线上等于明文。
    """
    raw = f"{username}:{password}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


class PasswordGateTestCase(unittest.TestCase):
    """改 APP_PASSWORD 会影响全局，所以每个测试前后都要还原。"""

    def setUp(self):
        self.client = app_module.app.test_client()
        self.original = app_module.APP_PASSWORD

    def tearDown(self):
        app_module.APP_PASSWORD = self.original

    def use_password(self, value):
        app_module.APP_PASSWORD = value


class TestPasswordGate(PasswordGateTestCase):
    def test_no_password_means_open(self):
        # 没设口令 = 不启用这道门。本机开发时不该每开一次页面就被拦。
        self.use_password("")
        self.assertEqual(self.client.get("/api/plan").status_code, 200)

    def test_blocks_without_credentials(self):
        self.use_password("hunter2")
        self.assertEqual(self.client.get("/api/plan").status_code, 401)

    def test_blocks_wrong_password(self):
        self.use_password("hunter2")
        res = self.client.get("/api/plan", headers=basic("wrong"))
        self.assertEqual(res.status_code, 401)

    def test_allows_correct_password(self):
        self.use_password("hunter2")
        res = self.client.get("/api/plan", headers=basic("hunter2"))
        self.assertEqual(res.status_code, 200)

    def test_username_does_not_matter(self):
        # 口令才是秘密，用户名不是。省得用户在手机上多纠结一个字段。
        self.use_password("hunter2")
        res = self.client.get("/api/plan", headers=basic("hunter2", username="随便填"))
        self.assertEqual(res.status_code, 200)

    def test_protects_write_routes_too(self):
        # **最要紧的一条：不能只挡住读。**
        # /api/parse 会拿真密钥去调 AI，谁都能打就是谁都能烧你的钱。
        self.use_password("hunter2")
        for path in ("/api/tasks", "/api/parse"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json={}).status_code, 401)

    def test_protects_the_page_itself(self):
        self.use_password("hunter2")
        self.assertEqual(self.client.get("/").status_code, 401)

    def test_401_asks_the_browser_for_credentials(self):
        # 少了这个响应头，浏览器不会弹口令框，只会干巴巴显示一行字，
        # 用户根本不知道下一步该干什么。
        self.use_password("hunter2")
        res = self.client.get("/api/plan")
        self.assertIn("Basic", res.headers.get("WWW-Authenticate", ""))

    def test_malformed_header_is_rejected(self):
        # 乱发的认证头不能让服务崩，也不能放行。
        #
        # 这些用例全是 ASCII——**不是我偷懒**：HTTP 头按规范只能是 Latin-1，
        # 中文压根进不了请求头（测试客户端会在编码那一步就抛 UnicodeEncodeError）。
        # 我第一版在这里写了「Basic 这不是base64」，它挂的是 500，
        # 但那个 500 是"发不出这个请求"，不是"服务处理不好"——
        # 测了个真实世界不可能发生的情况。
        self.use_password("hunter2")
        for junk in (
            "Basic",                      # 光有方案名，没有内容
            "Bearer hunter2",             # 方案就不对
            "Basic !!!!",                 # 不是合法 base64
            "Basic " + base64.b64encode(b"nocolon").decode(),   # 解出来没有冒号
            "hunter2",                    # 根本不是认证头
        ):
            with self.subTest(header=junk):
                res = self.client.get("/api/plan", headers={"Authorization": junk})
                self.assertEqual(res.status_code, 401)

    def test_empty_password_in_header_is_rejected(self):
        # 有人发 "Basic " + base64("user:")，密码是空串。
        # 不拦的话，空口令会去和真口令比——比不中，但万一哪天
        # 比较写反了就是个洞。这里钉死它。
        self.use_password("hunter2")
        res = self.client.get("/api/plan", headers=basic(""))
        self.assertEqual(res.status_code, 401)

    def test_chinese_password_works(self):
        # 口令里有中文也得能登进去（base64 按 UTF-8 编）
        self.use_password("我的口令")
        res = self.client.get("/api/plan", headers=basic("我的口令"))
        self.assertEqual(res.status_code, 200)


class TestStartupPlan(unittest.TestCase):
    """启动决定：绑哪个地址、开不开调试台、要不要拒绝启动。

    纯函数，所以不用真起服务器就能测——包括那条"拒绝启动"的规矩。
    """

    def test_default_is_localhost_only(self):
        host, debug, error = app_module.startup_plan([], "口令")
        self.assertEqual(host, "127.0.0.1")
        self.assertIsNone(error)

    def test_localhost_keeps_debug_on(self):
        # 本机自己用，调试台是帮手，留着
        _, debug, _ = app_module.startup_plan([], "口令")
        self.assertTrue(debug)

    def test_lan_binds_all_interfaces(self):
        host, _, error = app_module.startup_plan(["--lan"], "口令")
        self.assertEqual(host, "0.0.0.0")
        self.assertIsNone(error)

    def test_lan_turns_debug_off(self):
        # 调试台 = 能在服务器上跑任意代码的 Python 交互环境。
        # 开放到局域网还开着它，等于把刀递给同一网络里的每个人。
        _, debug, _ = app_module.startup_plan(["--lan"], "口令")
        self.assertFalse(debug)

    def test_lan_refuses_to_start_without_password(self):
        # **这条是整个功能的重点**：失败要往安全那边倒。
        # 宁可不让启动，也不能让一个没口令的服务裸奔在局域网上。
        host, _, error = app_module.startup_plan(["--lan"], "")
        self.assertIsNone(host)
        self.assertIn("APP_PASSWORD", error)

    def test_no_lan_needs_no_password(self):
        # 不开放就不要求口令——本机开发别来烦我
        _, _, error = app_module.startup_plan([], "")
        self.assertIsNone(error)

    def test_extra_arguments_do_not_confuse_it(self):
        host, _, _ = app_module.startup_plan(["--verbose", "--lan"], "口令")
        self.assertEqual(host, "0.0.0.0")


class TestPlanApiByDate(PasswordGateTestCase):
    """按日期取行程、按日期加事务。

    **绝不碰真的 tasks.json**：把 app 模块里的 load_tasks/save_tasks
    换成假的，数据只在内存里转一圈。测试跑完你的日程一条不少。
    （app.py 里是 `from plan import load_tasks`，名字绑在 app 模块上，
    所以换 app_module.load_tasks 就能拦住——换 plan.load_tasks 没用。）
    """

    def setUp(self):
        super().setUp()
        self.use_password("")            # 这组测日期，不测口令
        self.real_load = app_module.load_tasks
        self.real_save = app_module.save_tasks
        self.store = []
        app_module.load_tasks = lambda: self.store
        app_module.save_tasks = lambda tasks: None

    def tearDown(self):
        app_module.load_tasks = self.real_load
        app_module.save_tasks = self.real_save
        super().tearDown()

    def add(self, name, date=None, **extra):
        body = {"name": name, "minutes": "30", "priority": "中", **extra}
        if date is not None:
            body["date"] = date
        return self.client.post("/api/tasks", json=body)

    def test_plan_defaults_to_today(self):
        data = self.client.get("/api/plan").get_json()
        self.assertEqual(data["day"], data["today"])
        self.assertEqual(data["label"], "今天")

    def test_plan_takes_a_date(self):
        data = self.client.get("/api/plan?date=2026-11-03").get_json()
        self.assertEqual(data["day"], "2026-11-03")

    def test_prev_and_next_are_adjacent_days(self):
        data = self.client.get("/api/plan?date=2026-11-03").get_json()
        self.assertEqual(data["prev"], "2026-11-02")
        self.assertEqual(data["next"], "2026-11-04")

    def test_prev_and_next_cross_the_month(self):
        # 跨月自己算很容易错，所以前端不自己算，全用服务端给的
        data = self.client.get("/api/plan?date=2026-11-30").get_json()
        self.assertEqual(data["next"], "2026-12-01")

    def test_rejects_bad_date(self):
        res = self.client.get("/api/plan?date=明天")
        self.assertEqual(res.status_code, 400)
        self.assertIn("日期", res.get_json()["error"])

    def test_slash_date_is_accepted(self):
        # 宽进：手写习惯的斜杠也认
        data = self.client.get("/api/plan?date=2026/11/03").get_json()
        self.assertEqual(data["day"], "2026-11-03")

    def test_future_day_reports_the_configured_window(self):
        # 明天不受"现在几点"影响——翻到明天，窗口就是配置的 08:00–24:00。
        # 这条是确定性的（不像"今天"那条要看跑测试的时刻），所以敢断言具体值。
        tomorrow = shift_day(today_iso(), 1)
        data = self.client.get(f"/api/plan?date={tomorrow}").get_json()
        self.assertEqual(data["day_start"], "08:00")
        self.assertEqual(data["day_end"], "24:00")

    def test_today_window_is_never_in_the_past(self):
        # 今天这条只能查下限，不能断言具体值——
        # 断言具体值的话，这个测试上午跑绿、下午跑红。
        # "HH:MM" 字符串可以直接比大小，因为补了零。
        data = self.client.get("/api/plan").get_json()
        self.assertGreaterEqual(data["day_start"], "08:00")
        self.assertLessEqual(data["day_start"], data["day_end"])

    def test_added_task_lands_on_the_requested_day(self):
        self.assertEqual(self.add("评审", date="2026-11-03").status_code, 201)
        self.assertEqual(self.store[0]["date"], "2026-11-03")

    def test_added_task_defaults_to_today(self):
        self.add("写周报")
        self.assertEqual(self.store[0]["date"], today_iso())

    def test_plan_shows_only_that_day(self):
        self.add("今天的事")
        self.add("明天的事", date=shift_day(today_iso(), 1))

        today_names = [t["name"] for t in
                       self.client.get("/api/plan").get_json()["scheduled"]]
        tomorrow_names = [t["name"] for t in
                          self.client.get(f"/api/plan?date={shift_day(today_iso(), 1)}")
                          .get_json()["scheduled"]]
        self.assertEqual(today_names, ["今天的事"])
        self.assertEqual(tomorrow_names, ["明天的事"])

    def test_bad_date_on_add_is_rejected(self):
        res = self.add("评审", date="下周三")
        self.assertEqual(res.status_code, 400)
        self.assertIn("日期", res.get_json()["error"])
        self.assertEqual(self.store, [])       # 没通过校验就一条都不该留下


if __name__ == "__main__":
    unittest.main(verbosity=2)
