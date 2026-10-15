# Paper Guide

聽簡報前後，幫你讀懂論文的小工具。上傳簡報 PDF（可加論文 PDF），用 [Claude Code](https://claude.com/claude-code) 的 `claude -p` 產生：

- **導讀**：每一頁簡報在講什麼、為什麼重要
- **綜整**：整篇論文的問題、方法、結果、貢獻
- **術語表**：專有名詞保留英文，滑鼠移上去看中文解釋
- **問答**：對任何一頁提問，答案存在那一頁

導讀預設用繁體中文，想換語言或風格，改 `prompt.md` 就行。

## 需要

- Python 3.10 以上
- [Claude Code](https://claude.com/claude-code) CLI，而且已登入（`claude` 指令要能用）
- 選用：`pdftotext`（macOS：`brew install poppler`）。沒裝也能跑，只是 Claude 會直接看 PDF，比較吃 token

## 安裝

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## 啟動

```bash
.venv/bin/uvicorn server:app --port 8765
```

打開 http://localhost:8765

## 檔案

- `reports/<日期> <論文標題>/`：每份報告一個資料夾（`slides.pdf`、`paper.pdf`、`guide.json`、`paper.txt` 論文文字稿、`qa.json` 問答紀錄）。這個資料夾不會進 git
- `prompt.md`：產生導讀的指示
- `qa_prompt.md`：回答問題的指示
- `CONTEXT.md`：名詞定義

## 注意

- 每次產生導讀和回答問題，都會呼叫 `claude`，用的是你自己的 Claude 額度
- 這是本機工具，沒有登入機制，不要直接開到公開網路
