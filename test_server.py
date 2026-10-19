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
print("ok")
