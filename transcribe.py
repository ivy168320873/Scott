from __future__ import annotations
import os
import tempfile
from pathlib import Path


ALLOWED_EXTENSIONS = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".flac"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB (OpenAI Whisper limit)


def _whisper_transcribe(file_bytes: bytes, filename: str, openai_api_key: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=openai_api_key)

    suffix = Path(filename).suffix.lower() or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="text",
            )
        return response if isinstance(response, str) else response.text
    finally:
        os.unlink(tmp_path)


def _claude_analyze(transcript: str, api_key: str) -> str:
    import anthropic

    prompt = f"""你是專業的內容分析師。請仔細閱讀以下錄音轉文字的逐字稿，然後用**繁體中文**提供完整的重點分析。

---逐字稿開始---
{transcript}
---逐字稿結束---

請依照以下格式輸出分析結果：

**📋 內容摘要**
（用2-4句話概述整段內容的核心主旨）

**🔑 重點整理**
（條列出5-10個最重要的關鍵重點，每點簡潔說明）

**💡 關鍵觀點**
（提取出2-5個值得深思或特別重要的觀點/論點）

**✅ 行動事項**
（若有提到待辦事項、決策、或需要跟進的事項，請條列；若無則標示「無明確行動事項」）

**🏷️ 關鍵詞**
（列出8-12個代表本次內容的關鍵詞或專有名詞）"""

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def process(file_bytes: bytes, filename: str) -> dict:
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if not openai_key:
        raise ValueError("未設定 OPENAI_API_KEY，請在環境變數中設定後重試。")

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支援的檔案格式「{ext}」，請上傳 MP3、M4A、WAV、OGG、FLAC 或 WEBM 格式。")

    if len(file_bytes) > MAX_FILE_SIZE:
        raise ValueError("檔案過大，請上傳小於 25MB 的檔案。")

    transcript = _whisper_transcribe(file_bytes, filename, openai_key)

    if not transcript or not transcript.strip():
        raise ValueError("語音辨識結果為空，請確認錄音檔案有清晰的語音內容。")

    analysis = ""
    if anthropic_key:
        try:
            analysis = _claude_analyze(transcript, anthropic_key)
        except Exception as e:
            analysis = f"（AI 分析暫時無法使用：{e}）"
    else:
        analysis = "（未設定 ANTHROPIC_API_KEY，無法執行 AI 重點分析）"

    return {
        "transcript": transcript.strip(),
        "analysis": analysis,
        "char_count": len(transcript.strip()),
        "word_count": len(transcript.split()),
    }
