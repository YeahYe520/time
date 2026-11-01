"""app.py —— 给 time 套一个网页界面。

跑：
    .venv/Scripts/python.exe app.py
然后浏览器打开 http://127.0.0.1:8000

这个文件只做三件事：收 HTTP 请求 → 调 plan.py 里的函数 → 返回 JSON。
**排程逻辑一行都不在这里**——它全在 plan.py，同一份逻辑命令行和网页共用。
"""

from flask import Flask, jsonify, request, send_from_directory

from ai import parse_request
from plan import (
    DAY_END,
    DAY_START,
    blocked_reason,
    load_tasks,
    make_task,
    mark_done,
    save_tasks,
    schedule,
    to_hhmm,
)

app = Flask(__name__, static_folder="static")


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/plan")
def api_plan():
    """今日行程。前端拿这个渲染时间表。"""
    scheduled, blocked = schedule(load_tasks())

    def brief(t):
        return {
            "id": t["id"],
            "name": t["name"],
            "minutes": t["minutes"],
            "priority": t["priority"],
        }

    return jsonify({
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
    task, error = make_task(
        tasks,
        data.get("name", ""),
        data.get("minutes", ""),
        data.get("priority", ""),
        data.get("start", ""),
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


if __name__ == "__main__":
    # debug=True：改完代码自动重启，出错时浏览器能看到详细堆栈。
    # 但它会把源码和调试台暴露给任何能访问的人，**上线前必须关掉**。
    app.run(host="127.0.0.1", port=8000, debug=True)
