#!/usr/bin/env python3
"""PreToolUse hook：阻擋遠端寫入與高風險破壞性 Bash 指令。

流程：讀 stdin JSON → 取出 Bash 完整 command → 比對禁止清單 → 輸出決策。
  命中 → stderr 印出原因，exit code 2（Claude Code 會擋下該次工具呼叫）
  其餘 → exit code 0，交回正常 permission flow（不放寬、也不額外收緊）

本腳本不執行收到的 command，不讀寫檔案，不連網，不使用
subprocess / os.system / eval / exec / shell=True，只讀 stdin、只寫 stderr。
"""
from __future__ import annotations

import json
import re
import shlex
import sys

SPLIT = re.compile(r"\|\||&&|[;\n|&]")           # shell 運算子
SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")  # 命令替換
ENVVAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
WRAPPERS = {"sudo", "env", "nohup", "time", "command", "exec", "timeout", "xargs"}
GIT_OPT_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--exec-path"}
# 部署／發佈：執行檔 -> 封鎖的子命令開頭
DEPLOY = {
    "railway": [("up",), ("redeploy",), ("run",)], "vercel": [("deploy",)],
    "netlify": [("deploy",)], "fly": [("deploy",)], "flyctl": [("deploy",)],
    "serverless": [("deploy",)], "heroku": [("run",)], "docker": [("push",)],
    "terraform": [("apply",), ("destroy",)], "npm": [("publish",)],
    "kubectl": [("apply",), ("delete",)], "gcloud": [("app", "deploy")],
    "twine": [("upload",)],
}
GH_DENY = [("pr", "create"), ("pr", "merge"), ("release", "create"), ("repo", "delete")]
ALWAYS_DENY = {"glab": "對遠端 GitLab 寫入", "dropdb": "刪除資料庫"}
DANGEROUS_RM = {"/", "/*", "~", "~/*", ".", "./*", "..", "../*", "*", "$HOME", "${HOME}"}


def segments(cmd: str, depth: int = 0) -> list[str]:
    """拆成獨立 segment，並把 $(...) / `...` 內容也展開成 segment。"""
    out: list[str] = []
    if depth <= 3:
        for match in SUBST.finditer(cmd):
            inner = match.group(1) or match.group(2) or ""
            if inner.strip():
                out += segments(inner, depth + 1)
    return out + [s.strip() for s in SPLIT.split(SUBST.sub(" ", cmd)) if s.strip()]


def argv(segment: str) -> list[str]:
    """切 token，去掉 FOO=bar 前綴與 sudo/env/timeout 等包裝。"""
    try:
        tokens = shlex.split(segment, comments=True)
    except ValueError:
        tokens = segment.split()
    index = 0
    while index < len(tokens) and (
        ENVVAR.match(tokens[index]) or tokens[index].rsplit("/", 1)[-1] in WRAPPERS
    ):
        index += 1
    return tokens[index:]


def git_sub(tokens: list[str]) -> tuple[str | None, list[str]]:
    """取出 git 真正的子命令，跳過 -C/-c 等全域選項。"""
    index = 1
    while index < len(tokens):
        if tokens[index] in GIT_OPT_VALUE:
            index += 2
        elif tokens[index].startswith("-"):
            index += 1
        else:
            break
    return (tokens[index], tokens[index + 1:]) if index < len(tokens) else (None, [])


def deny_reason(segment: str, depth: int = 0) -> str | None:
    """命中封鎖規則回傳理由，否則 None。"""
    tokens = argv(segment)
    if not tokens:
        return None
    exe = tokens[0].rsplit("/", 1)[-1]
    args = tokens[1:]
    pos = [a.lower() for a in args if not a.startswith("-")]

    if "--dangerously-skip-permissions" in args or "bypassPermissions" in segment:
        return "不得使用跳過權限／繞過安全確認的模式"
    if exe in ("bash", "sh", "zsh") and "-c" in args and depth < 3:  # bash -c "git push"
        return next((r for a in pos if (r := deny_reason(a, depth + 1))), None)
    if exe in ALWAYS_DENY:
        return ALWAYS_DENY[exe]
    if exe == "git":
        sub, rest = git_sub(tokens)
        if sub == "push":
            if any(a in ("--delete", "-d") for a in rest) or any(a.startswith(":") for a in rest):
                return "git push 刪除遠端分支（遠端寫入）"
            if any(a in ("-f", "--force", "--force-with-lease") for a in rest):
                return "git push --force/-f 會覆寫遠端歷史"
            return "git push 屬於遠端寫入操作"
    if exe == "gh":
        for prefix in GH_DENY:
            if tuple(pos[:len(prefix)]) == prefix:
                return f"gh {' '.join(prefix)} 屬於 GitHub 遠端寫入"
    for prefix in DEPLOY.get(exe, []):
        if tuple(pos[:len(prefix)]) == prefix:
            return f"{exe} {' '.join(prefix)} 屬於部署／發佈操作"
    if exe == "rm":
        short = "".join(a for a in args if a.startswith("-") and not a.startswith("--"))
        recursive = "r" in short.lower() or "--recursive" in args
        for target in [a for a in args if not a.startswith("-")]:
            if recursive and (target in DANGEROUS_RM or target.rstrip("/") in DANGEROUS_RM):
                return f"rm -r 目標 {target!r} 會刪除根目錄／家目錄／上層目錄"
    return None


def main() -> None:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        sys.exit(0)                       # 解析失敗保持中立
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        sys.exit(0)
    command = (event.get("tool_input") or {}).get("command") or ""
    if not command.strip():
        sys.exit(0)

    for part in segments(command):        # 任一 segment 危險就整條拒絕
        why = deny_reason(part)
        if why:
            print(f"[block-remote-writes] 已阻擋：{why}\n  指令片段：{part}\n"
                  f"  完整指令：{command.strip()}\n"
                  f"  如確實需要執行，請由使用者手動執行並明確授權。",
                  file=sys.stderr)
            sys.exit(2)                   # exit 2 = 擋下工具呼叫並把 stderr 給模型
    sys.exit(0)                           # 其餘交回正常 permission flow


if __name__ == "__main__":
    main()
