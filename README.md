# Paper Guide

上傳 lab meeting 的簡報 PDF（＋論文 PDF），用 `claude -p` 產生繁中導讀、綜整、術語表。

## 第一次安裝

```bash
/opt/homebrew/bin/python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

另外需要 `pdftotext`（`brew install poppler`）來抽論文文字。沒裝也能跑，只是 Claude 會直接看 PDF，比較吃 token。

## 啟動

```bash
.venv/bin/uvicorn server:app --port 8765
```

打開 http://localhost:8765

## 檔案

- `reports/<日期> <論文標題>/`：每個報告一個資料夾（`slides.pdf`、`paper.pdf`、`guide.json`、`paper.txt` 論文文字稿、`qa.json` 問答紀錄）
- `prompt.md`：產生導讀的指示，想改導讀風格就改這裡
- `qa_prompt.md`：回答問題的指示
- 名詞定義見 `CONTEXT.md`
