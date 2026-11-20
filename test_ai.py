"""ai.py 的测试。跑：py test_ai.py

**注意：这里一行网络代码都没测，也测不了。**

网络不可测——它依赖对面服务器活着、网络通、密钥有效。
所以 ai.py 刻意把「判断」（parse_reply）和「联网」（ask_ai）拆开了：
判断全在这里被测到，联网那部分薄到几乎没有逻辑可错。

这是「纯函数」这个习惯的第三次回报：
    v0.1：schedule() 测得动
    v0.2：命令行和网页共用同一份逻辑
    v0.3：AI 的回复能被反复验证，不用真的调 AI
"""

import base64
import unittest

from ai import (
    MAX_IMAGE_BYTES,
    build_content,
    build_prompt,
    check_image,
    parse_reply,
)


class TestParseReply(unittest.TestCase):
    def test_plain_json(self):
        drafts, problems = parse_reply(
            '{"tasks":[{"name":"写周报","minutes":60,"priority":"高","guessed":false}]}'
        )
        self.assertIsNone(problems)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["name"], "写周报")
        self.assertEqual(drafts[0]["minutes"], 60)
        self.assertEqual(drafts[0]["priority"], "高")

    def test_strips_markdown_code_block(self):
        drafts, problems = parse_reply(
            '```json\n{"tasks":[{"name":"写周报","minutes":60,"priority":"高"}]}\n```'
        )
        self.assertIsNone(problems)
        self.assertEqual(len(drafts), 1)

    def test_ignores_surrounding_prose(self):
        # AI 最常见的毛病：先说一堆话，再给 JSON，最后还要再问一句
        drafts, problems = parse_reply(
            "好的，我帮你解析如下：\n"
            '{"tasks":[{"name":"写周报","minutes":60,"priority":"高"}]}\n'
            "还需要我帮你安排时间吗？"
        )
        self.assertIsNone(problems)
        self.assertEqual(len(drafts), 1)

    def test_drafts_have_no_id(self):
        # 草稿还没确认，绝不能带编号——
        # 带了就说明有人提前把它当成正式数据了
        drafts, _ = parse_reply(
            '{"tasks":[{"name":"写周报","minutes":60,"priority":"高"}]}'
        )
        self.assertNotIn("id", drafts[0])

    def test_empty_tasks_list(self):
        drafts, problems = parse_reply('{"tasks":[]}')
        self.assertEqual(drafts, [])
        self.assertIsNone(problems)

    def test_empty_reply(self):
        for bad in ("", "   ", None):
            with self.subTest(text=bad):
                drafts, error = parse_reply(bad)
                self.assertIsNone(drafts)
                self.assertIn("空", error)

    def test_rejects_text_without_json(self):
        drafts, error = parse_reply("我不知道你在说什么")
        self.assertIsNone(drafts)
        self.assertIn("找不到 JSON", error)

    def test_rejects_malformed_json(self):
        drafts, error = parse_reply('{"tasks":[{"name":}]}')
        self.assertIsNone(drafts)
        self.assertIn("不是合法 JSON", error)

    def test_rejects_wrong_shape(self):
        drafts, error = parse_reply('{"items":[1,2,3]}')
        self.assertIsNone(drafts)
        self.assertIn("结构不对", error)

    def test_skips_invalid_priority_and_reports(self):
        # AI 编了个不存在的优先级——validate_task 必须挡住
        drafts, problems = parse_reply(
            '{"tasks":['
            '{"name":"写周报","minutes":60,"priority":"很高"},'
            '{"name":"看论文","minutes":90,"priority":"高"}'
            "]}"
        )
        self.assertEqual([d["name"] for d in drafts], ["看论文"])
        self.assertIn("第 1 条被拒", problems)

    def test_skips_negative_minutes(self):
        drafts, problems = parse_reply(
            '{"tasks":[{"name":"写周报","minutes":-30,"priority":"高"}]}'
        )
        self.assertEqual(drafts, [])
        self.assertIn("被拒", problems)

    def test_skips_empty_name(self):
        drafts, problems = parse_reply(
            '{"tasks":[{"name":"","minutes":30,"priority":"高"}]}'
        )
        self.assertEqual(drafts, [])
        self.assertIn("被拒", problems)

    def test_skips_non_object_entries(self):
        drafts, problems = parse_reply('{"tasks":["写周报", 42]}')
        self.assertEqual(drafts, [])
        self.assertIn("不是对象", problems)

    def test_keeps_guessed_flag(self):
        drafts, _ = parse_reply(
            '{"tasks":[{"name":"写周报","minutes":60,"priority":"高","guessed":true}]}'
        )
        self.assertTrue(drafts[0]["guessed"])

    def test_missing_guessed_defaults_to_false(self):
        drafts, _ = parse_reply(
            '{"tasks":[{"name":"写周报","minutes":60,"priority":"高"}]}'
        )
        self.assertFalse(drafts[0]["guessed"])

    def test_accepts_numeric_minutes(self):
        # AI 有时给数字有时给字符串，两种都得认
        drafts, problems = parse_reply(
            '{"tasks":[{"name":"写周报","minutes":"60","priority":"高"}]}'
        )
        self.assertIsNone(problems)
        self.assertEqual(drafts[0]["minutes"], 60)


class TestParseReplyStartTime(unittest.TestCase):
    """开始时间：AI 给得对就用，给得不对退回自动安排。"""

    def test_start_is_parsed(self):
        drafts, problems = parse_reply(
            '{"tasks":[{"name":"开会","minutes":30,"priority":"高","start":"14:00"}]}'
        )
        self.assertIsNone(problems)
        self.assertEqual(drafts[0]["start"], 840)     # 14*60

    def test_null_start_means_auto(self):
        # 这是最常见的情况：用户只说"下午"，AI 该填 null
        drafts, problems = parse_reply(
            '{"tasks":[{"name":"看论文","minutes":90,"priority":"低","start":null}]}'
        )
        self.assertIsNone(problems)
        self.assertNotIn("start", drafts[0])

    def test_missing_start_field_is_fine(self):
        drafts, problems = parse_reply(
            '{"tasks":[{"name":"看论文","minutes":90,"priority":"低"}]}'
        )
        self.assertIsNone(problems)
        self.assertNotIn("start", drafts[0])

    def test_bad_start_keeps_the_task(self):
        # 关键行为：时间没看懂，**不该把整条事务丢掉**。
        # 用户要的是那条事务，不是一个格式完美的钟点。
        drafts, problems = parse_reply(
            '{"tasks":[{"name":"开会","minutes":30,"priority":"高","start":"下午"}]}'
        )
        self.assertEqual([d["name"] for d in drafts], ["开会"])
        self.assertNotIn("start", drafts[0])
        self.assertIn("自动安排", problems)

    def test_bad_start_does_not_hide_other_tasks(self):
        drafts, problems = parse_reply(
            '{"tasks":['
            '{"name":"开会","minutes":30,"priority":"高","start":"下午"},'
            '{"name":"写周报","minutes":60,"priority":"低","start":"09:30"}'
            "]}"
        )
        self.assertEqual([d["name"] for d in drafts], ["开会", "写周报"])
        self.assertEqual(drafts[1]["start"], 570)
        self.assertIn("自动安排", problems)

    def test_out_of_range_start_keeps_the_task(self):
        drafts, problems = parse_reply(
            '{"tasks":[{"name":"开会","minutes":30,"priority":"高","start":"25:00"}]}'
        )
        self.assertEqual([d["name"] for d in drafts], ["开会"])
        self.assertNotIn("start", drafts[0])


class TestParseReplyDayOffset(unittest.TestCase):
    """日期：AI 只报"距今几天"，换成具体日期由 plan.parse_day_offset 做。

    这一组全部显式传 today——**测试里绝不读真实时钟**，
    否则今天跑绿、明天跑红，你分不清是代码坏了还是日子不对。
    """

    TODAY = "2026-11-01"

    def parse(self, day_offset_json):
        # 用 replace 填占位符，不用 f-string——这段 JSON 里全是花括号，
        # f-string 里每个都得写成 {{ }}，我第一版就栽在这儿：
        # 少写一个，拼出来的 JSON 坏在中间，报错还指向别的地方。
        # （ai.py 的 build_prompt 用 replace，也是同一个理由。）
        raw = ('{"tasks":[{"name":"写周报","minutes":60,"priority":"高",'
               '"day_offset":__OFFSET__}]}')
        return parse_reply(raw.replace("__OFFSET__", day_offset_json), self.TODAY)

    def test_offset_is_turned_into_a_date(self):
        drafts, problems = self.parse("1")
        self.assertIsNone(problems)
        self.assertEqual(drafts[0]["date"], "2026-11-02")

    def test_zero_means_today(self):
        drafts, _ = self.parse("0")
        self.assertEqual(drafts[0]["date"], self.TODAY)

    def test_null_means_no_date(self):
        # **关键：没说日期和"今天"是两回事。**
        # 草稿不带 date 键，前端才知道该用"你正在看的那天"兜底。
        drafts, problems = self.parse("null")
        self.assertIsNone(problems)
        self.assertNotIn("date", drafts[0])

    def test_missing_field_is_fine(self):
        drafts, problems = parse_reply(
            '{"tasks":[{"name":"写周报","minutes":60,"priority":"高"}]}', self.TODAY
        )
        self.assertIsNone(problems)
        self.assertNotIn("date", drafts[0])

    def test_bad_offset_keeps_the_task(self):
        # 和 start 一个道理：日期没看懂，**不该把整条事务丢掉**
        drafts, problems = self.parse('"明天"')
        self.assertEqual([d["name"] for d in drafts], ["写周报"])
        self.assertNotIn("date", drafts[0])
        self.assertIn("日期", problems)

    def test_absurd_offset_keeps_the_task(self):
        drafts, problems = self.parse("9999")
        self.assertEqual([d["name"] for d in drafts], ["写周报"])
        self.assertNotIn("date", drafts[0])
        self.assertIn("日期", problems)

    def test_bad_offset_does_not_hide_other_tasks(self):
        drafts, problems = parse_reply(
            '{"tasks":['
            '{"name":"开会","minutes":30,"priority":"高","day_offset":"下周三"},'
            '{"name":"写周报","minutes":60,"priority":"低","day_offset":2}'
            "]}",
            self.TODAY,
        )
        self.assertEqual([d["name"] for d in drafts], ["开会", "写周报"])
        self.assertEqual(drafts[1]["date"], "2026-11-03")
        self.assertIn("日期", problems)

    def test_ai_cannot_produce_a_wrong_date(self):
        """**这是整个设计的意义所在。**

        提示词里明令 AI 只输出天数差、不要输出日期。就算它不听话，
        硬塞一个 "2026-11-05" 进来，那也只会被当成"差 2026-11-05 天"
        而被拒掉——**AI 根本没有机会说出一个错的日期**，
        它最多说出一个错的偏移，而偏移是个小到能一眼看出来的整数。
        """
        drafts, problems = self.parse('"2026-11-05"')
        self.assertNotIn("date", drafts[0])
        self.assertIn("日期", problems)


class TestBuildPrompt(unittest.TestCase):
    """提示词必须带上"今天几号、周几"——离开它「明天」就没有意义。"""

    def test_today_is_filled_in(self):
        text = build_prompt("2026-11-01")
        self.assertIn("2026-11-01", text)
        self.assertNotIn("__TODAY__", text)

    def test_weekday_is_filled_in(self):
        # 2026-11-01 是周日
        text = build_prompt("2026-11-01")
        self.assertIn("周日", text)
        self.assertNotIn("__WEEKDAY__", text)

    def test_json_example_survives(self):
        # 提示词里那段 JSON 示例含大量花括号。用 .format() 的话这里会炸，
        # 用 replace 就安然无恙——这就是当初不用 .format() 的原因。
        text = build_prompt("2026-11-01")
        self.assertIn('"day_offset":null', text)

    def test_tells_ai_not_to_output_dates(self):
        # 提示词里必须有这条禁令，否则 AI 会自作主张给具体日期
        self.assertIn("不要输出具体日期", build_prompt("2026-11-01"))


class TestDraftsAreValidated(unittest.TestCase):
    """AI 吐出来的东西必须能直接喂给 make_task。"""

    def test_draft_passes_validate_task(self):
        from plan import validate_task

        drafts, _ = parse_reply(
            '{"tasks":[{"name":"开会","minutes":30,"priority":"高","start":"14:00"}]}'
        )
        d = drafts[0]
        _, error = validate_task(d["name"], d["minutes"], d["priority"], d["start"])
        self.assertIsNone(error)

    def test_draft_with_date_passes_validate_task(self):
        from plan import validate_task

        drafts, _ = parse_reply(
            '{"tasks":[{"name":"开会","minutes":30,"priority":"高","day_offset":1}]}',
            "2026-11-01",
        )
        d = drafts[0]
        _, error = validate_task(
            d["name"], d["minutes"], d["priority"], None, d["date"]
        )
        self.assertIsNone(error)


class TestCheckImage(unittest.TestCase):
    """用户传进来的图片必须先验过才能发给 AI。

    这一组全是纯函数测试——check_image 不联网、不读文件，
    就是为了能这样把各种坏输入喂进去验。
    """

    OK = base64.b64encode(b"x" * 100).decode()

    def test_accepts_jpeg(self):
        cleaned, error = check_image(self.OK, "image/jpeg")
        self.assertIsNone(error)
        self.assertEqual(cleaned, self.OK)

    def test_accepts_png(self):
        _, error = check_image(self.OK, "image/png")
        self.assertIsNone(error)

    def test_rejects_unknown_type(self):
        # 白名单之外一律拒。用户传的这个字符串会被**原样**塞进发给 AI 的 JSON，
        # 不校验等于让请求方决定我们要往上游发什么。
        _, error = check_image(self.OK, "image/svg+xml")
        self.assertIn("不支持", error)

    def test_rejects_missing_type(self):
        _, error = check_image(self.OK, None)
        self.assertIn("不支持", error)

    def test_rejects_broken_base64(self):
        _, error = check_image("这不是 base64！！", "image/jpeg")
        self.assertIn("base64", error)

    def test_rejects_data_url_prefix(self):
        # 前端很容易忘了切掉 "data:image/jpeg;base64," 这个前缀。
        # 带着它发给 AI，AI 只会说看不懂——报错必须在这里，不能跑到下游去。
        _, error = check_image("data:image/jpeg;base64," + self.OK, "image/jpeg")
        self.assertIsNotNone(error)

    def test_rejects_empty(self):
        _, error = check_image("", "image/jpeg")
        self.assertIn("空", error)

    def test_rejects_too_large(self):
        big = base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode()
        _, error = check_image(big, "image/jpeg")
        self.assertIn("太大", error)

    def test_accepts_exactly_at_the_limit(self):
        # 边界：正好等于上限要放行（判断用的是 > 不是 >=）
        edge = base64.b64encode(b"x" * MAX_IMAGE_BYTES).decode()
        _, error = check_image(edge, "image/jpeg")
        self.assertIsNone(error)


class TestBuildContent(unittest.TestCase):
    """没图片时必须是**字符串**，有图片时才是内容块数组。

    前者是为了不动已经跑通的东西：加图片功能不该改写纯文字那条路。
    """

    def test_without_image_is_a_plain_string(self):
        c = build_content("2026-11-01", "明天开会", None)
        self.assertIsInstance(c, str)
        self.assertIn("明天开会", c)

    def test_with_image_is_a_block_list(self):
        c = build_content("2026-11-01", "", ("QUJD", "image/jpeg"))
        self.assertIsInstance(c, list)
        self.assertEqual(len(c), 2)
        self.assertEqual(c[0]["type"], "text")
        self.assertEqual(c[1]["type"], "image")

    def test_image_block_shape(self):
        # 这个形状是 Anthropic Messages API 规定的，写错了 API 直接报错
        c = build_content("2026-11-01", "", ("QUJD", "image/png"))
        src = c[1]["source"]
        self.assertEqual(src["type"], "base64")
        self.assertEqual(src["media_type"], "image/png")
        self.assertEqual(src["data"], "QUJD")

    def test_image_prompt_asks_for_everything(self):
        # "一条都不要漏"是图片路径存在的理由——图里往往是一整屏安排
        c = build_content("2026-11-01", "", ("QUJD", "image/jpeg"))
        self.assertIn("一条都不要漏", c[0]["text"])

    def test_extra_text_is_appended(self):
        # 允许"图 + 一句话"：发张课表再补一句"只排下周的"
        c = build_content("2026-11-01", "只排下周的", ("QUJD", "image/jpeg"))
        self.assertIn("只排下周的", c[0]["text"])

    def test_image_path_keeps_today_in_prompt(self):
        # 图片路径也不能弄丢"今天几号"——「明天」离开它就没有意义
        c = build_content("2026-11-01", "", ("QUJD", "image/jpeg"))
        self.assertIn("2026-11-01", c[0]["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
