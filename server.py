"""Paper Guide 後端：存 PDF、在背景跑 `claude -p` 產生導讀、回答每頁的問題。"""

import json
import re
import shutil
import subprocess
import threading
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)
CLAUDE_TIMEOUT = 30 * 60
QA_TIMEOUT = 5 * 60
MAX_PAPERS = 5  # 問答會把每篇全文放進 prompt，太多篇會很貴
# 不帶使用者平常的 Claude Code 設定（MCP、skills、外掛、長的系統指示）：
# 跟讀論文無關，每次呼叫固定開銷從約 3.4 萬 token 降到約 2 千
CLAUDE = ["claude", "-p",
          "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "project",
          "--no-session-persistence"]
MODELS = ("sonnet", "opus", "haiku")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_OPTS = {"model": "sonnet", "effort": "medium"}  # 產生導讀的預設，使用者可在上傳時改
SYSTEM = "你是幫資管碩士生讀懂 lab meeting 論文簡報的助教。照使用者的指示用工具讀寫檔案，用繁體中文寫。"


def tools(*names: str) -> list[str]:
    # --tools 決定有哪些工具，--allowedTools 讓它們不用等人按同意
    return ["--tools", ",".join(names), "--allowedTools", *names]


def fill(template: str, **vals: str) -> str:
    # 一次替換完：論文或問題裡剛好出現 {page} 之類的字也不會被誤換
    return re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), m.group(0)), template)

PROMPT = (ROOT / "prompt.md").read_text(encoding="utf-8")
QA_PROMPT = (ROOT / "qa_prompt.md").read_text(encoding="utf-8")
qa_lock = threading.Lock()

# shortcut: 執行中的工作只記在記憶體，server 重開後會顯示「中斷」，按重試即可。
running: set[str] = set()
moved: dict[str, str] = {}  # 舊 id → 改名後的 id
STEPS = ["準備論文文字稿", "讀簡報", "讀論文", "寫每頁導讀", "寫綜整和術語表", "檢查格式"]
progress: dict[str, dict] = {}  # 產生中的報告 → {"step": STEPS 的第幾個, "pct": 0-100, "note": 補充說明}
live: dict[str, dict] = {}  # 產生中的報告 → 已經寫完的部分 {"title", "slides"}，讓使用者邊等邊看

app = FastAPI()


@app.middleware("http")
async def no_stale_cache(request, call_next):
    # 每次都跟 server 確認有沒有新版（沒變的話只回 304），改完前端重新整理就看得到
    resp = await call_next(request)
    resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


def report_dir(rid: str) -> Path:
    d = (REPORTS / rid).resolve()
    if d.parent != REPORTS.resolve() or not d.is_dir():
        raise HTTPException(404, "找不到這個報告")
    return d


def status_of(d: Path) -> str:
    if (d / "guide.json").exists():
        return "done"
    if d.name in running:
        return "generating"
    return "error"


def safe_name(s: str) -> str:
    s = re.sub(r'[/\\:*?"<>|\n\r\t]', " ", s).strip(" .")
    return re.sub(r"\s+", " ", s)[:120]


def unique_dir(name: str) -> Path:
    d, n = REPORTS / name, 2
    while d.exists():
        d, n = REPORTS / f"{name} ({n})", n + 1
    return d


def validate_guide(g: dict) -> None:
    assert isinstance(g.get("title"), str)
    assert isinstance(g.get("slides"), list) and g["slides"]
    for s in g["slides"]:
        assert isinstance(s.get("page"), int) and isinstance(s.get("guide"), str)
    syn = g.get("synthesis")
    assert isinstance(syn, dict)
    for k in ("problem", "method", "results", "contributions"):
        assert isinstance(syn.get(k), str), k
    for t in g.get("glossary", []):
        assert all(isinstance(t.get(k), str) for k in ("term", "zh", "gloss"))


def paper_pdfs(d: Path) -> list[Path]:
    # 論文存成 paper.pdf、paper-2.pdf、paper-3.pdf…（舊報告只有 paper.pdf）
    pdfs = [p for p in d.glob("paper*.pdf") if re.fullmatch(r"paper(-\d+)?\.pdf", p.name)]
    return sorted(pdfs, key=lambda p: int(p.stem.partition("-")[2] or 1))


def paper_text(pdf: Path) -> Path | None:
    """抽出論文文字存成同名 .txt。PDF 每頁會被 Claude 當圖片看，很吃 token，文字稿便宜很多。"""
    txt = pdf.with_suffix(".txt")
    if not txt.exists():
        try:
            r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True, timeout=60)
            pages = r.stdout.split("\f")
        except (OSError, subprocess.TimeoutExpired):
            pages = []
        # 抽不到什麼字（掃描檔、或沒裝 pdftotext）就不寫，讓 Claude 直接看 PDF
        if sum(len(p.strip()) for p in pages) >= 1000:
            txt.write_text("\n\n".join(f"=== 第 {i} 頁 ===\n{p.strip()}" for i, p in enumerate(pages, 1) if p.strip()),
                           encoding="utf-8")
    return txt if txt.exists() else None


def paper_label(i: int, n: int) -> str:
    return f"論文 {i}" if n > 1 else "論文"


TXT_HINT = ("文字稿是從 PDF 抽出來的，每頁開頭有 `=== 第 N 頁 ===`，看不到圖表。請讀文字稿，不要整份讀 PDF；"
            "只有需要看某張圖、表或公式時，才用 Read 讀原檔的那幾頁。")


def paper_note(d: Path) -> str:
    """告訴 Claude 每篇論文要讀哪個檔。"""
    pdfs, rel = paper_pdfs(d), d.name
    if not pdfs:
        return "這次沒有論文，只能根據簡報。"
    lines = []
    for i, pdf in enumerate(pdfs, 1):
        txt = paper_text(pdf)
        lines.append(f"- {paper_label(i, len(pdfs))}：文字稿 `{rel}/{txt.name}`（原檔 `{rel}/{pdf.name}`）" if txt else
                     f"- {paper_label(i, len(pdfs))}：`{rel}/{pdf.name}`（抽不到文字，直接讀 PDF，頁數多的話分段讀）")
    return "\n".join(lines) + "\n\n" + TXT_HINT


def pdf_pages(pdf: Path) -> int:
    try:
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0  # 0 = 不知道總頁數


def claude_events(cmd: list[str], timeout: int, junk: list[str] | None = None):
    """跑 claude（要帶 --output-format stream-json），一行一個事件 yield 出來。不是 JSON 的行收進 junk（錯誤訊息用）。
    呼叫端中途不讀了，finally 會把 claude 關掉。"""
    proc = subprocess.Popen(cmd, cwd=REPORTS, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        for line in proc.stdout:
            try:
                yield json.loads(line)
            except ValueError:
                if junk is not None:
                    junk[:] = (junk + [line])[-20:]
    finally:
        timer.cancel()
        proc.kill()
        proc.wait()


PAGE_KEY = re.compile(r'\\"page\\"\s*:')  # Write 的內容是跳脫過的 JSON 字串，所以 key 長這樣：\"page\":


def partial_guide(buf: str) -> dict:
    """從還沒寫完的 Write 輸入（{"file_path": ..., "content": "<跳脫過的 guide JSON>...）挑出已經完整的標題和每頁導讀。"""
    m = re.search(r'"content"\s*:\s*"', buf)
    if not m:
        return {"slides": []}
    s, text = buf[m.end() - 1:], None
    try:
        text = json.JSONDecoder().raw_decode(s)[0]  # content 已經寫完
    except ValueError:
        for cut in range(7):  # 結尾可能卡在跳脫字元中間（\ 或 \u12），切掉再補上結尾的引號
            try:
                text = json.loads(s[:len(s) - cut] + '"')
                break
            except ValueError:
                continue
    if text is None:
        return {"slides": []}
    out = {"slides": []}
    t = re.search(r'"title"\s*:\s*("(?:[^"\\]|\\.)*")', text)
    if t:
        out["title"] = json.loads(t.group(1))
    a = re.search(r'"slides"\s*:\s*\[', text)
    i, dec = (a.end() if a else len(text)), json.JSONDecoder()
    while i < len(text):
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
        try:
            obj, i = dec.raw_decode(text, i)
        except ValueError:
            break  # 這一頁還沒寫完（或陣列結束了）
        if isinstance(obj, dict) and isinstance(obj.get("page"), int) and isinstance(obj.get("guide"), str):
            out["slides"].append({"page": obj["page"], "guide": obj["guide"]})
    return out


def progress_from(ev: dict, state: dict, total: int) -> tuple[int, int, str] | None:
    """從 claude 的一個串流事件推算進度 (STEPS 的第幾步, 百分比, 補充)。跟進度無關的事件回 None。"""
    if ev.get("type") == "assistant":  # 完整的一則回覆：看它呼叫了哪個工具
        for c in ev["message"].get("content", []):
            if c.get("type") != "tool_use":
                continue
            path = str(c["input"].get("file_path", ""))
            name = path.rsplit("/", 1)[-1]
            if c["name"] == "Write":
                state["writing"] = False
                return 5, 95, ""
            if name == "slides.pdf":
                pages = re.search(r"(\d+)\s*$", str(c["input"].get("pages", "")))
                state["read"] = max(state.get("read", 0), int(pages.group(1)) if pages else total)
                if total:
                    return 1, 5 + 20 * min(state["read"], total) // total, f"{min(state['read'], total)} / {total} 頁"
                return 1, 15, ""
            if name.startswith("paper"):
                return 2, 30, ""
            if name == "guide.tmp.json":
                return 5, 97, ""
    if ev.get("type") != "stream_event":
        return None
    e = ev["event"]
    if e["type"] == "content_block_start" and e["content_block"].get("name") == "Write":
        state["writing"], state["buf"] = True, ""
        return 3, 35, f"0 / {total} 頁" if total else ""
    if state.get("writing") and e["type"] == "content_block_delta" and e["delta"].get("type") == "input_json_delta":
        state["buf"] += e["delta"]["partial_json"]
        syn = '\\"synthesis\\"' in state["buf"]
        n = len(PAGE_KEY.findall(state["buf"]))
        if (n, syn) != state.get("seen"):  # 新的一頁開始寫 = 上一頁寫完了，這時才重新解析，不用每個字都解析
            state["seen"] = (n, syn)
            state["guide"] = partial_guide(state["buf"])
        if syn:
            return 4, 92, ""
        if total:
            return 3, 35 + 55 * min(n, total) // total, f"{min(n, total)} / {total} 頁"
        return 3, 60, f"{n} 頁"
    return None


def generate(rid: str, rename: bool) -> None:
    d = REPORTS / rid
    out = d / "guide.tmp.json"
    (d / "error.txt").unlink(missing_ok=True)
    out.unlink(missing_ok=True)

    def report(step: int, pct: int, note: str = "") -> None:
        pct = max(pct, progress.get(rid, {}).get("pct", 0))  # 進度條只往前走
        progress[rid] = {"step": step, "pct": pct, "note": note}

    try:
        report(0, 1)
        prompt = fill(PROMPT, paper=paper_note(d), dir=rid)
        total = pdf_pages(d / "slides.pdf")
        report(1, 5, f"0 / {total} 頁" if total else "")
        opts_file = d / "options.json"
        opts = json.loads(opts_file.read_text(encoding="utf-8")) if opts_file.exists() else DEFAULT_OPTS
        # prompt 裡的路徑都是相對 reports/ 寫的
        cmd = CLAUDE + ["--model", opts["model"], "--effort", opts["effort"], "--system-prompt", SYSTEM, prompt,
                        "--output-format", "stream-json", "--include-partial-messages", "--verbose"] + tools("Read", "Write")
        state, junk, result = {}, [], ""
        for ev in claude_events(cmd, CLAUDE_TIMEOUT, junk):
            try:
                p = progress_from(ev, state, total)
            except (KeyError, TypeError, AttributeError):
                p = None  # CLI 事件格式變了頂多進度條不動，不能讓導讀跟著失敗
            if p:
                report(*p)
            if "guide" in state:
                live[rid] = state["guide"]
            if ev.get("type") == "result":
                result = str(ev.get("result", ""))
        if not out.exists():
            raise RuntimeError(f"claude 沒有寫出 guide.tmp.json\n\n{result[-2000:]}\n{''.join(junk)[-2000:]}")
        g = json.loads(out.read_text(encoding="utf-8"))
        validate_guide(g)
        out.rename(d / "guide.json")
        if rename and safe_name(g["title"]):
            new = unique_dir(f"{rid[:10]} {safe_name(g['title'])}")
            d.rename(new)
            moved[rid] = new.name
    except Exception as e:  # 任何失敗都寫進 error.txt，畫面上會顯示＋可重試
        (d / "error.txt").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
    finally:
        running.discard(rid)
        progress.pop(rid, None)
        live.pop(rid, None)


def start(rid: str, rename: bool) -> None:
    running.add(rid)
    threading.Thread(target=generate, args=(rid, rename), daemon=True).start()


async def save_pdf(f: UploadFile, dest: Path) -> None:
    data = await f.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, f"{f.filename} 不是 PDF")
    dest.write_bytes(data)


@app.get("/api/reports")
def list_reports():
    ds = sorted((d for d in REPORTS.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)
    return [{"id": d.name, "status": status_of(d)} for d in ds]


@app.post("/api/reports")
async def create_report(slides: UploadFile, paper: list[UploadFile] | None = None, name: str = Form(""),
                        model: str = Form(DEFAULT_OPTS["model"]), effort: str = Form(DEFAULT_OPTS["effort"])):
    if model not in MODELS or effort not in EFFORTS:
        raise HTTPException(400, "模型或 effort 不對")
    papers = [p for p in paper or [] if p.filename]  # 沒選檔案時瀏覽器還是會送一個空的
    if len(papers) > MAX_PAPERS:
        raise HTTPException(400, f"論文最多 {MAX_PAPERS} 篇")
    today = date.today().isoformat()
    custom = safe_name(name)
    d = unique_dir(f"{today} {custom or '產生中'}")
    d.mkdir()
    try:
        (d / "options.json").write_text(json.dumps({"model": model, "effort": effort}), encoding="utf-8")
        await save_pdf(slides, d / "slides.pdf")
        for i, p in enumerate(papers, 1):
            await save_pdf(p, d / ("paper.pdf" if i == 1 else f"paper-{i}.pdf"))
    except HTTPException:
        shutil.rmtree(d)
        raise
    start(d.name, rename=not custom)
    return {"id": d.name}


@app.get("/api/reports/{rid}")
def get_report(rid: str):
    if rid in moved:
        return {"moved": moved[rid]}
    d = report_dir(rid)
    res = {"id": rid, "status": status_of(d), "papers": [p.name for p in paper_pdfs(d)]}
    if res["status"] == "done":
        res["guide"] = json.loads((d / "guide.json").read_text(encoding="utf-8"))
        res["qa"] = load_qa(d)
    elif res["status"] == "generating":
        res["progress"] = {**progress.get(rid, {"step": 0, "pct": 0, "note": ""}), "steps": STEPS}
        res["partial"] = live.get(rid, {"slides": []})
    elif res["status"] == "error":
        e = d / "error.txt"
        res["error"] = e.read_text(encoding="utf-8") if e.exists() else "產生被中斷了（可能 server 重開過）"
    return res


@app.post("/api/reports/{rid}/retry")
def retry(rid: str):
    d = report_dir(rid)
    if status_of(d) != "error":
        raise HTTPException(409, "這個報告不需要重試")
    start(rid, rename="產生中" in rid)
    return {"ok": True}


def load_qa(d: Path) -> dict:
    f = d / "qa.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def stream_claude(system: str, prompt: str, effort: str):
    """逐行讀 claude 的串流輸出。產生 ("reset", "")（新一段回覆開始，例如讀完檔之後）、("delta", 新的字）、
    最後一個 ("done", 完整答案或 None)。呼叫端中途不讀了，claude_events 會把 claude 關掉。"""
    cmd = CLAUDE + ["--model", "sonnet", "--effort", effort, "--system-prompt", system, prompt,
                    "--output-format", "stream-json", "--include-partial-messages", "--verbose"] + tools("Read")
    answer = None
    for ev in claude_events(cmd, QA_TIMEOUT):
        if ev.get("type") == "stream_event":
            e = ev["event"]
            if e["type"] == "message_start":
                yield "reset", ""
            elif e["type"] == "content_block_delta" and e["delta"].get("type") == "text_delta":
                yield "delta", e["delta"]["text"]
        elif ev.get("type") == "result" and not ev.get("is_error") and ev.get("result"):
            answer = ev["result"].strip()
    yield "done", answer


class Question(BaseModel):
    page: int = Field(ge=1)
    question: str = Field(min_length=1, max_length=2000)
    effort: str = "low"


@app.post("/api/reports/{rid}/qa")
def ask(rid: str, q: Question):
    if q.effort not in EFFORTS:
        raise HTTPException(400, "effort 不對")
    d = report_dir(rid)
    if status_of(d) != "done":
        raise HTTPException(409, "導讀還沒產生好")
    guide = json.loads((d / "guide.json").read_text(encoding="utf-8"))
    slide = next((s["guide"] for s in guide["slides"] if s["page"] == q.page), "（這頁沒有導讀）")
    history = "\n\n".join(f"問：{x['q']}\n答：{x['a']}" for x in load_qa(d).get(str(q.page), [])) or "（還沒有）"
    terms = "、".join(t["term"] for t in guide.get("glossary", [])) or "（沒有）"
    # 論文全文放在系統指示裡：同一份報告每題開頭都一樣，第 2 題起會走快取（約 1 小時內），
    # 新算的 token 從約 2.3 萬降到約 2 千；也省掉 Claude 自己去讀檔的那一輪
    pdfs, parts = paper_pdfs(d), []
    for i, pdf in enumerate(pdfs, 1):
        txt = paper_text(pdf)
        parts.append(f'<paper name="{paper_label(i, len(pdfs))}" original="{rid}/{pdf.name}">\n{txt.read_text(encoding="utf-8")}\n</paper>'
                     if txt else f"- {paper_label(i, len(pdfs))}：抽不到文字，需要時用 Read 讀 `{rid}/{pdf.name}`。")
    paper = "\n\n".join(parts) + "\n\n" + TXT_HINT if pdfs else "這次沒有論文，只能根據簡報回答。"
    system = f"{SYSTEM}\n\n這份報告的論文（可能有好幾篇）：\n\n{paper}"
    prompt = fill(QA_PROMPT, dir=rid, page=str(q.page), slide_guide=slide,
                  history=history, terms=terms, question=q.question.strip())

    def events():
        for kind, val in stream_claude(system, prompt, q.effort):
            if kind != "done":
                yield json.dumps({"t": kind, "text": val}, ensure_ascii=False) + "\n"
            elif not val:
                yield json.dumps({"t": "error", "msg": "Claude 沒有回答，請再試一次"}, ensure_ascii=False) + "\n"
            else:
                entry = {"q": q.question.strip(), "a": val, "at": datetime.now().isoformat(timespec="seconds")}
                with qa_lock:
                    qa = load_qa(d)
                    qa.setdefault(str(q.page), []).append(entry)
                    tmp = d / "qa.tmp.json"
                    tmp.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
                    tmp.replace(d / "qa.json")
                yield json.dumps({"t": "done", "entry": entry}, ensure_ascii=False) + "\n"

    # 一行一個 JSON（ndjson）：reset / delta / done / error
    return StreamingResponse(events(), media_type="application/x-ndjson")


app.mount("/files", StaticFiles(directory=REPORTS), name="files")
app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
