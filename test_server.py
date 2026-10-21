# 執行：.venv/bin/python test_server.py
import asyncio

from fastapi import HTTPException

import server


def status(call):
    try:
        r = call()
        if asyncio.iscoroutine(r):
            asyncio.run(r)
    except HTTPException as e:
        return e.status_code


# 錯的模型 / effort 在碰檔案之前就要被擋下
assert status(lambda: server.create_report(slides=None, model="gpt", effort="low")) == 400
assert status(lambda: server.create_report(slides=None, model="sonnet", effort="huge")) == 400
assert status(lambda: server.ask("x", server.Question(page=1, question="q", effort="huge"))) == 400
# 合法的 effort 會往下走到 report_dir，找不到報告是 404
assert status(lambda: server.ask("no-such-report", server.Question(page=1, question="q", effort="max"))) == 404


# 產生導讀的進度：照 claude 串流事件的實際長相餵進去
def tool(name, **inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def stream(e):
    return {"type": "stream_event", "event": e}


def write_delta(s):
    return stream({"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": s}})


st = {}
p = lambda ev: server.progress_from(ev, st, 10)
assert p({"type": "user"}) is None
assert p(tool("Read", file_path="/r/x/slides.pdf", pages="1-4")) == (1, 13, "4 / 10 頁")
assert p(tool("Read", file_path="/r/x/slides.pdf", pages="5-10")) == (1, 25, "10 / 10 頁")
assert p(tool("Read", file_path="/r/x/paper-2.txt")) == (2, 30, "")
assert p(stream({"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Write"}})) == (3, 35, "0 / 10 頁")
assert p(write_delta('{"file_path": "x", "content": "{\\"slides\\":[{\\"pa')) == (3, 35, "0 / 10 頁")
assert p(write_delta('ge\\": 1, \\"guide\\": \\"a\\"},{\\"page\\":2')) == (3, 46, "2 / 10 頁")  # key 被切成兩段也算得到
assert p(write_delta('}], \\"synthesis\\": {')) == (4, 92, "")
assert p(tool("Write", file_path="/r/x/guide.tmp.json", content="{}")) == (5, 95, "")
assert p(write_delta('\\"page\\":')) is None  # 寫完之後的 delta 不再算
assert server.progress_from(tool("Read", file_path="/r/x/slides.pdf"), {}, 0) == (1, 15, "")  # 不知道總頁數
print("ok")
