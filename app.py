"""app.py —— 给 time 套一个网页界面。

跑：
    .venv/Scripts/python.exe app.py
然后浏览器打开 http://127.0.0.1:8000

这个文件只做三件事：收 HTTP 请求 → 调 plan.py 里的函数 → 返回 JSON。
**排程逻辑一行都不在这里**——它全在 plan.py，同一份逻辑命令行和网页共用。
"""

import hmac
import sys

from flask import Flask, Response, jsonify, request, send_from_directory

from ai import parse_request
from config import APP_PASSWORD
from plan import (
    DAY_END,
    DAY_START,
    blocked_reason,
    friendly_day,
    load_tasks,
    make_task,
    mark_done,
    parse_date,
    save_tasks,
    schedule,
    shift_day,
    tasks_on,
    to_hhmm,
    today_iso,
)

app = Flask(__name__, static_folder="static")


@app.before_request
def require_password():
    """整个站点的口令门。跑在每个请求之前，所以**没有路由能绕过去**。

    用的是 HTTP Basic：浏览器自己弹一个输入框，输一次就记住，
    之后连 fetch() 发的请求也会自动带上认证头。
    **我们刻意不写登录页、不写会话管理**——那两样是 web 里最容易写出
    漏洞的地方（cookie 没签名、会话固定攻击、忘记加 HttpOnly……）。
    交给浏览器做，比自己做靠谱得多。

    APP_PASSWORD 为空 = 不启用这道门。
    """
    if not APP_PASSWORD:
        return None

    # 先判空再取属性。反过来写的话，没带认证头的请求会在 auth.password
    # 那一行炸成 500——本该是"请出示口令"，结果变成"服务器坏了"。
    auth = request.authorization
    supplied = (auth.password if auth else "") or ""

    # 用 compare_digest 而不是 ==：== 一旦发现不同的字符就立刻返回，
    # 攻击者能靠"这次响应是不是快了零点几微秒"一个字符一个字符试出口令。
    # compare_digest 无论对不对都花一样长的时间，这条路就堵死了。
    #
    # **两边都要先 encode 成 bytes。** compare_digest 传字符串时只接受纯 ASCII，
    # 中文会直接抛 TypeError——也就是把口令设成中文就登不进来了（500，不是 401）。
    # 转成 bytes 之后它就不挑内容了。
    if hmac.compare_digest(supplied.encode("utf-8"), APP_PASSWORD.encode("utf-8")):
        return None

    # 这个响应头不能省：没有它浏览器不会弹口令框，
    # 只会干巴巴显示"需要口令"，用户根本不知道该干什么。
    return Response(
        "需要口令。用户名随便填，密码是 .env 里的 APP_PASSWORD。",
        401,
        {"WWW-Authenticate": 'Basic realm="time", charset="UTF-8"'},
    )


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/plan")
def api_plan():
    """某一天的行程。前端拿这个渲染时间表。

    看哪天由 `?date=2026-11-03` 决定，不传就是今天。
    """
    today = today_iso()
    day, error = parse_date(request.args.get("date"))
    if error:
        return jsonify({"error": error}), 400
    day = day or today

    scheduled, blocked = schedule(tasks_on(load_tasks(), day, today))

    def brief(t):
        return {
            "id": t["id"],
            "name": t["name"],
            "minutes": t["minutes"],
            "priority": t["priority"],
        }

    return jsonify({
        # 这一天是谁、怎么称呼它、前后一天是哪天——**全在服务端算好再发下去**。
        # 前端只负责显示，不自己算日期。这样命令行、网页看到的
        # 「明天」永远是同一个意思；JS 里再实现一遍迟早会跟 Python 这边跑偏
        # （时区、跨月、跨年，都是容易写错的地方）。
        "day": day,
        "label": friendly_day(day, today),
        "today": today,
        "prev": shift_day(day, -1),
        "next": shift_day(day, 1),
        "day_start": to_hhmm(DAY_START),
        "day_end": to_hhmm(DAY_END),
        # 已排的补上可显示的时间文本，别把内部的分钟数漏给前端。
        # auto=True 表示这个时间是我算出来的，不是你定的——前端据此画标记。
        "scheduled": [
            {
                **brief(t),
                "start": to_hhmm(t["start"]),
                "end": to_hhmm(t["end"]),
                "auto": t["auto"],
            }
            for t in scheduled
        ],
        # 待办栏：每条都带一句"为什么没排上"，否则用户只看到东西消失
        "blocked": [
            {
                **brief(t),
                "start": to_hhmm(t["start"]) if t["start"] is not None else None,
                "blocked_by": t["blocked_by"],
                "reason": blocked_reason(t),
            }
            for t in blocked
        ],
    })


@app.post("/api/parse")
def api_parse():
    """把一句人话翻译成任务草稿。**不写盘**——要等用户在界面上确认。

    这是「AI 的输出不直接进数据库」这条规矩的落点：
    本接口只返回草稿；真正入库走 /api/tasks，也就是走 make_task 的校验。
    AI 想绕过校验，除非它自己发一个 /api/tasks 请求——而它没有手。
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "请求体必须是 UTF-8 编码的 JSON 对象"}), 400

    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "说点什么吧"}), 400

    drafts, problems = parse_request(text)
    # drafts is None = 整个解析失败（连不上 AI / 回复不是 JSON）。
    # drafts 是列表但 problems 非空 = 部分成功，**不能连好的那几条一起丢掉**。
    if drafts is None:
        # 502 = 上游（AI）出了问题，不是这个请求本身有问题
        return jsonify({"error": problems}), 502

    return jsonify({
        "drafts": [
            # 和 /api/plan 一样，把内部的分钟数换成 "09:30" 再给前端
            {**d, "start": to_hhmm(d["start"]) if d.get("start") is not None else None}
            for d in drafts
        ],
        "warning": problems,
    })


@app.get("/api/tasks")
def api_tasks():
    return jsonify(load_tasks())


@app.post("/api/tasks")
def api_add_task():
    # silent=True：解析失败时返回 None 而不是抛异常，好让我们自己给中文错误。
    # 但必须区分"解析失败"和"字段缺失"——否则客户端编码发错时会看到
    # 「事务名不能为空」这种误导性提示（这个坑我真踩了一次）。
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "请求体必须是 UTF-8 编码的 JSON 对象"}), 400

    tasks = load_tasks()
    # 校验规则和命令行完全共用——网页绕不过去。
    # start 留空（""）表示没定时间，plan.py 会把它自动排进空档。
    #
    # date 也一起传进来：**加到哪天，由前端明确告诉我们**，不靠服务端"猜今天"。
    # 因为你可能正翻到后天在往里加事——那时候"今天"不是你想要的答案。
    # today 显式传进去，是为了让这一次请求里只读一次时钟。
    task, error = make_task(
        tasks,
        data.get("name", ""),
        data.get("minutes", ""),
        data.get("priority", ""),
        start_text=data.get("start", ""),
        date_text=data.get("date", ""),
        today=today_iso(),
    )
    if error:
        return jsonify({"error": error}), 400

    save_tasks(tasks)
    return jsonify(task), 201


@app.post("/api/tasks/<int:task_id>/done")
def api_done(task_id):
    tasks = load_tasks()
    task, error = mark_done(tasks, task_id)
    if error:
        return jsonify({"error": error}), 404

    save_tasks(tasks)
    return jsonify(task)


def startup_plan(argv, password):
    """决定怎么启动服务。返回 (绑哪个地址, 开不开调试台, 错误信息)。

    **纯函数**——不启动任何东西、不打印、不退出进程，所以测得动。
    （这正是这个项目一路在做的事：把"判断"从"动作"里剥出来。
    留在 `__main__` 里的话，想测就只能真的把服务器起起来。）

    两条规矩：

    1. 默认只绑 127.0.0.1（回环地址）= 只有这台电脑能连。
       加 --lan 才绑 0.0.0.0（所有网卡），手机才能从局域网连进来。
    2. **要开放，就必须先有口令。** 宁可拒绝启动，也不让一个没口令的
       服务裸奔在局域网上——"启动了但悄悄没保护"你是不会知道的，
       "拒绝启动"你立刻就知道。
    """
    lan = "--lan" in argv

    if lan and not password:
        return None, None, (
            "拒绝启动：--lan 会把服务开放给整个局域网，但 .env 里没有 APP_PASSWORD。\n"
            "先在 .env 里加一行 APP_PASSWORD=你的口令，再重试。"
        )

    # debug=True 会同时打开两样东西：自动重载，和**浏览器里的调试台**。
    # 那个调试台是个能在服务器上执行任意代码的 Python 交互环境——
    # 本机自己用没问题，开放到局域网就是给别人递刀。所以 --lan 时关掉它。
    return ("0.0.0.0" if lan else "127.0.0.1"), (not lan), None


if __name__ == "__main__":
    host, debug, error = startup_plan(sys.argv[1:], APP_PASSWORD)
    if error:
        sys.exit(error)

    if host != "127.0.0.1":
        print("!! 已开放到局域网：同一网络下的人只要知道口令就能访问。")
        print("!! 手机打开 http://<这台电脑的局域网地址>:8000")

    # use_reloader 留着：改完代码自动重启。它和调试台是两回事，
    # 关掉 debug 不影响它。
    app.run(host=host, port=8000, debug=debug, use_reloader=True)
