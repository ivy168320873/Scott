"""
Multi-Persona Financial Analyst Debate Engine

Three AI analysts (甲/乙/丙) independently analyze industry reports and
謝孟恭 Podcast content, then cross-debate, producing a final investment verdict.

Pipeline:
  Round 1 — Each analyst gives their independent analysis
  Round 2 — Each analyst cross-debates the other two
  Round 3 — Committee chair delivers final verdict

Progress events are pushed to a Queue so the SSE endpoint can stream them live.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import uuid

from anthropic import Anthropic

# ── Singleton client ──────────────────────────────────────────────────────────

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY 未設定")
        _client = Anthropic(api_key=key)
    return _client


# ── Analyst Personas ──────────────────────────────────────────────────────────

ANALYSTS: dict[str, dict] = {
    "甲": {
        "name": "甲分析師",
        "title": "成長多頭派",
        "emoji": "📈",
        "color": "#3fb950",
        "system": (
            "你是甲分析師，定位為成長股多頭派金融分析師。\n\n"
            "核心投資哲學：\n"
            "- 聚焦產業結構性趨勢、技術突破與市場擴張機會\n"
            "- 相信優質公司長期向上，在合理估值下可承擔短期波動\n"
            "- 特別關注：市占率提升、毛利率擴張、新產品周期、訂單能見度\n"
            "- 論點積極樂觀，但必須有理有據，絕不盲目追漲\n"
            "- 慣用詞：「結構性機會」「長線布局」「估值相對合理」「護城河加深」\n\n"
            "請用繁體中文回答，條理清晰，語氣自信且有說服力。"
        ),
    },
    "乙": {
        "name": "乙分析師",
        "title": "風控空頭派",
        "emoji": "🛡️",
        "color": "#f85149",
        "system": (
            "你是乙分析師，定位為風險管理空頭派金融分析師。\n\n"
            "核心投資哲學：\n"
            "- 首要任務是識別風險、過度樂觀假設與潛在陷阱\n"
            "- 關注估值泡沫、競爭加劇、庫存週期、總經壓力與護城河侵蝕\n"
            "- 特別關注：毛利壓縮、客戶集中度、本益比 vs 成長性匹配、法說會措辭變化\n"
            "- 論點保守審慎，但非悲觀主義——而是理性量化風險\n"
            "- 慣用詞：「市場已過度定價」「風險被低估」「需更好的安全邊際」\n\n"
            "請用繁體中文回答，條理清晰，語氣審慎且具穿透力。"
        ),
    },
    "丙": {
        "name": "丙分析師",
        "title": "量化中性派",
        "emoji": "📊",
        "color": "#58a6ff",
        "system": (
            "你是丙分析師，定位為量化中性派金融分析師。\n\n"
            "核心投資哲學：\n"
            "- 以數據、統計規律、歷史案例說話，避免情緒化判斷\n"
            "- 同時建立多空情境，計算期望值與風報比\n"
            "- 特別關注：技術面訊號、資金流向、相對強弱、估值百分位、動能因子\n"
            "- 提供平衡視角，最終給出機率加權的操作建議\n"
            "- 慣用詞：「數據顯示」「歷史統計」「正期望值」「風報比 X:1」\n\n"
            "請用繁體中文回答，善用數字與百分比，語氣客觀且有說服力。"
        ),
    },
}

# ── Prompt Templates ──────────────────────────────────────────────────────────

_ANALYSIS_PROMPT = """\
請分析以下材料，從你的分析師角色出發給出專業見解。

---
**【產業報告摘要】**
{report}

---
**【謝孟恭 Podcast 內容重點】**
{podcast}
---

請依照以下格式回答：

### 核心洞察（3-4 點最重要的發現）

### 支撐論據（數據、產業邏輯或歷史案例）

### 風險 / 機會評估（從你的角色視角出發）

### 初步立場
[偏多 / 偏空 / 中性觀望]　信心指數：X/10\
"""

_DEBATE_PROMPT = """\
三位分析師對相同材料完成了第一輪獨立分析，現在進行交叉辯論。

---
**甲分析師（成長多頭派）的分析：**
{a}

---
**乙分析師（風控空頭派）的分析：**
{b}

---
**丙分析師（量化中性派）的分析：**
{c}
---

現在輪到你（{name}，{title}）進行交叉辯論：

### 對其他分析師的挑戰與補充
（針對其他兩位各提出 1-2 個反駁點或質疑，點出其論點的不足之處）

### 強化我的核心立場
（用新論點或對方弱點鞏固你的觀點，不能重複第一輪已說過的內容）

### 三方共識
（指出哪 1-2 點是大家都認同的事實基礎）\
"""

_VERDICT_PROMPT = """\
你是投資委員會最終裁決者。三位分析師已完成兩輪辯論，現在由你整合所有論點，給出最終投資裁決。

══ 第一輪：獨立分析 ══

【甲分析師（成長多頭派）】
{a1}

【乙分析師（風控空頭派）】
{b1}

【丙分析師（量化中性派）】
{c1}

══ 第二輪：交叉辯論 ══

【甲的辯論回應】
{a2}

【乙的辯論回應】
{b2}

【丙的辯論回應】
{c2}

---
請給出最終裁決（繁體中文，語氣專業、直接、有決斷力）：

## 🏆 最終投資裁決

### 方向裁決
**[偏多 / 偏空 / 中性觀望]**　信心度：XX%

### 裁決核心理由（整合三方最強論點，選出最重要的 3 點）

### 主要風險警示（乙分析師的關鍵提醒，2-3 點不能忽視的風險）

### 操作建議
- **進場時機：**（什麼條件下才進場）
- **建議部位大小：**（保守 / 標準 / 積極，並給出百分比參考）
- **止損條件：**（什麼情況觸發停損）
- **目標報酬：**（合理的獲利目標）

### 關鍵觀察指標（未來 1-3 個月需追蹤的 3 個指標）

### 三方共識（所有分析師都認同的 1-2 個核心事實）\
"""


# ── Claude API Call ───────────────────────────────────────────────────────────

def _call(client: Anthropic, system: str, user: str, model: str, max_tokens: int = 1024) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


# ── Worker Thread ─────────────────────────────────────────────────────────────

def _worker(job_id: str, report: str, podcast: str, model: str) -> None:
    q = _jobs[job_id]["queue"]
    try:
        client = _get_client()
        material = _ANALYSIS_PROMPT.format(
            report=report.strip() or "（未提供）",
            podcast=podcast.strip() or "（未提供）",
        )

        # ── Round 1: Independent Analysis ────────────────────────────────────
        analyses: dict[str, str] = {}
        for key, info in ANALYSTS.items():
            q.put({"step": "analysis_start", "analyst": key,
                   "name": info["name"], "title": info["title"], "emoji": info["emoji"]})
            text = _call(client, info["system"], material, model, max_tokens=900)
            analyses[key] = text
            q.put({
                "step": "analysis_done", "analyst": key,
                "name": info["name"], "title": info["title"],
                "emoji": info["emoji"], "color": info["color"], "content": text,
            })

        # ── Round 2: Cross-Debate ─────────────────────────────────────────────
        debates: dict[str, str] = {}
        for key, info in ANALYSTS.items():
            q.put({"step": "debate_start", "analyst": key,
                   "name": info["name"], "title": info["title"], "emoji": info["emoji"]})
            prompt2 = _DEBATE_PROMPT.format(
                a=analyses["甲"], b=analyses["乙"], c=analyses["丙"],
                name=info["name"], title=info["title"],
            )
            text = _call(client, info["system"], prompt2, model, max_tokens=700)
            debates[key] = text
            q.put({
                "step": "debate_done", "analyst": key,
                "name": info["name"], "title": info["title"],
                "emoji": info["emoji"], "color": info["color"], "content": text,
            })

        # ── Round 3: Final Verdict ────────────────────────────────────────────
        q.put({"step": "verdict_start"})
        verdict_text = _call(
            client,
            (
                "你是資深投資委員會最終裁決者。"
                "整合多方觀點，給出客觀、有決斷力的最終投資裁決。"
                "使用繁體中文，格式清晰，語氣直接。"
            ),
            _VERDICT_PROMPT.format(
                a1=analyses["甲"], b1=analyses["乙"], c1=analyses["丙"],
                a2=debates["甲"], b2=debates["乙"], c2=debates["丙"],
            ),
            model,
            max_tokens=1600,
        )
        q.put({"step": "verdict_done", "content": verdict_text})
        q.put({"step": "complete"})

    except Exception as exc:
        q.put({"step": "error", "error": str(exc)})


# ── Job Manager ───────────────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}


def start_job(report: str, podcast: str, model: str = "claude-haiku-4-5-20251001") -> str:
    jid = str(uuid.uuid4())[:8]
    q: queue.Queue = queue.Queue()
    _jobs[jid] = {"queue": q, "started_at": time.time()}
    threading.Thread(target=_worker, args=(jid, report, podcast, model), daemon=True).start()
    return jid


def get_queue(job_id: str) -> queue.Queue | None:
    job = _jobs.get(job_id)
    return job["queue"] if job else None
