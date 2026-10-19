# Paper Guide

- 詞彙定義看 `CONTEXT.md`。
- 啟動：`.claude/launch.json`（`.venv/bin/uvicorn server:app --port 8765`）。
- 測試：`node static/guide.test.js` 和 `.venv/bin/python test_server.py`。
- 新 clone 後跑一次 `git config core.hooksPath .githooks`，commit 前會自動跑上面兩個測試。
