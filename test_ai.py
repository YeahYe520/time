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

import unittest

from ai import parse_reply


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
