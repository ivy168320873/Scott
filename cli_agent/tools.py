"""工具集：讀檔、寫檔、執行 shell 命令。

每個工具都有：
  1. 一份給 Claude 的 JSON schema（``TOOL_SCHEMAS``）。
  2. 一個實際執行的 Python 函式（``TOOL_FUNCTIONS``）。

執行器 (``execute_tool``) 會把 Claude 的工具呼叫對應到函式，
並永遠回傳字串，方便包進 tool_result block。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from stock_tools import STOCK_TOOL_FUNCTIONS, STOCK_TOOL_SCHEMAS

# 給 Claude 看的工具定義。描述要明確說明「何時」該用這個工具。
_CORE_TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": (
            "讀取本機檔案的文字內容。當你需要查看檔案目前內容、"
            "在修改前先確認、或回答關於某個檔案的問題時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要讀取的檔案路徑（相對或絕對）。",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "把文字內容寫入本機檔案，若檔案存在則覆蓋，必要時會建立上層目錄。"
            "當你需要建立新檔案或完整改寫檔案內容時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要寫入的檔案路徑（相對或絕對）。",
                },
                "content": {
                    "type": "string",
                    "description": "要寫入檔案的完整內容。",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_shell",
        "description": (
            "在 shell 中執行命令並回傳 stdout、stderr 與結束碼。"
            "當你需要列出目錄、跑測試、用 git、或其他命令列操作時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要執行的 shell 命令。",
                }
            },
            "required": ["command"],
        },
    },
]


def read_file(path: str) -> str:
    """回傳檔案文字內容，或錯誤訊息。"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"錯誤：找不到檔案 '{path}'。"
    except UnicodeDecodeError:
        return f"錯誤：'{path}' 不是 UTF-8 文字檔，無法讀取。"
    except OSError as e:
        return f"錯誤：讀取 '{path}' 失敗：{e}"


def write_file(path: str, content: str) -> str:
    """把內容寫入檔案，回傳成功或錯誤訊息。"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已寫入 {len(content)} 個字元到 '{path}'。"
    except OSError as e:
        return f"錯誤：寫入 '{path}' 失敗：{e}"


def run_shell(command: str, timeout: int = 60) -> str:
    """執行 shell 命令，回傳 stdout/stderr/結束碼。"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"錯誤：命令超過 {timeout} 秒未完成，已中止。"
    except OSError as e:
        return f"錯誤：無法執行命令：{e}"

    parts = [f"結束碼：{result.returncode}"]
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    return "\n".join(parts)


# 工具名稱 -> 實作函式（核心工具）。
_CORE_TOOL_FUNCTIONS = {
    "read_file": read_file,
    "write_file": write_file,
    "run_shell": run_shell,
}

# 對外公開：核心工具 + 股票工具。
TOOL_SCHEMAS = _CORE_TOOL_SCHEMAS + STOCK_TOOL_SCHEMAS
TOOL_FUNCTIONS = {**_CORE_TOOL_FUNCTIONS, **STOCK_TOOL_FUNCTIONS}


def execute_tool(name: str, tool_input: dict) -> str:
    """依名稱執行工具，永遠回傳字串。"""
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        return f"錯誤：未知的工具 '{name}'。"
    try:
        return func(**tool_input)
    except TypeError as e:
        # 參數不符（缺少必要欄位等）。
        return f"錯誤：呼叫 '{name}' 的參數有誤：{e}"
