"""Studio 認證：與既有股票系統的 Flask session 相容。

設計目標：使用者在股票系統登入後，同一個瀏覽器 session 就能直接使用
Studio API —— 不需要第二套登入。

如何做到而**不破壞隔離**：
本模組自行以 `itsdangerous` 驗證 Flask 的 session cookie 簽章，
**不 import `app.py`**。因此 Studio 仍可在股票模組不存在時獨立運作
（此時退回「未認證」而非崩潰）。

相容細節（與 Flask `SecureCookieSessionInterface` 一致）：
- serializer：`TaggedJSONSerializer`
- salt：`cookie-session`
- key_derivation：`hmac`
- digest_method：`sha1`

授權規則刻意與股票系統完全相同：
- `ACCESS_CODE` 未設定 → 認證關閉（與 `app.py::_require_auth` 一致）
- 已設定 → session 中的 `auth` 必須等於 `sha256(ACCESS_CODE)`
"""

from __future__ import annotations

import hashlib
import os
from typing import Annotated, Any

from fastapi import Depends, Request

from studio.core.errors import StudioError

_SESSION_COOKIE_NAME = "session"
_SESSION_SALT = "cookie-session"


class UnauthorizedError(StudioError):
    """未認證。

    Studio API 一律回傳 JSON，絕不回傳 HTML 登入頁 —— API 呼叫端
    （前端 fetch、腳本）無法處理 HTML 轉址。
    """

    code = "unauthorized"
    status_code = 401


class ForbiddenError(StudioError):
    """已認證但權限不足。"""

    code = "forbidden"
    status_code = 403


class Identity:
    """目前請求的身分。

    Attributes:
        authenticated: 是否已通過認證。
        auth_disabled: 是否因未設定 `ACCESS_CODE` 而停用認證。
        session: 解出的 session 內容（僅供診斷，不含金鑰）。
    """

    __slots__ = ("authenticated", "auth_disabled", "session")

    def __init__(self, *, authenticated: bool, auth_disabled: bool, session: dict[str, Any] | None = None) -> None:
        self.authenticated = authenticated
        self.auth_disabled = auth_disabled
        self.session = session or {}

    @property
    def is_admin(self) -> bool:
        """是否具備管理權限。

        目前股票系統只有單一存取碼、沒有角色分級，因此通過認證即等同管理者。
        保留這個屬性，讓未來加入角色時 route 不需改寫。
        """

        return self.authenticated or self.auth_disabled


def access_code() -> str:
    """讀取存取碼。

    每次呼叫都重新讀取環境變數（而非模組載入時快取一次），
    讓測試能以 monkeypatch 切換認證開關。
    """

    return os.environ.get("ACCESS_CODE", "")


def _secret_key() -> str:
    """讀取 Flask session 簽章金鑰。

    必須與股票系統使用同一把 `SECRET_KEY`，否則無法驗證其 cookie。
    """

    return os.environ.get("SECRET_KEY", "")


def expected_auth_value(code: str) -> str:
    """計算 session 中應存放的認證值。

    與 `app.py::_hash` 相同：認識碼的 SHA-256 十六進位字串。
    """

    return hashlib.sha256(code.encode()).hexdigest()


def decode_flask_session(cookie_value: str) -> dict[str, Any] | None:
    """驗證並解出 Flask session cookie。

    Args:
        cookie_value: 原始 cookie 字串。

    Returns:
        解出的 session dict；簽章不符、過期或相依缺失時回傳 None。
    """

    secret = _secret_key()
    if not secret or not cookie_value:
        return None

    try:
        from itsdangerous import URLSafeTimedSerializer
        from itsdangerous.exc import BadSignature
    except ImportError:  # pragma: no cover - itsdangerous 為必要相依，僅作保險
        return None

    try:
        from flask.json.tag import TaggedJSONSerializer

        serializer: Any = TaggedJSONSerializer()
    except ImportError:
        # 股票模組不存在時退回標準 JSON：仍能驗證簽章，
        # 只是無法還原 Flask 專屬的標記型別（本用途不需要）。
        import json

        serializer = json

    signer = URLSafeTimedSerializer(
        secret,
        salt=_SESSION_SALT,
        serializer=serializer,
        signer_kwargs={"key_derivation": "hmac", "digest_method": hashlib.sha1},
    )

    try:
        data = signer.loads(cookie_value)
    except (BadSignature, Exception):  # noqa: B014 - BadSignature 為 Exception 子類，明列以表意圖
        return None

    return data if isinstance(data, dict) else None


def identify(request: Request) -> Identity:
    """由請求解析身分。

    不拋錯 —— 供需要「知道有沒有登入」但不強制的端點使用。
    """

    code = access_code()
    if not code:
        # 與股票系統一致：未設定 ACCESS_CODE 即代表認證關閉。
        return Identity(authenticated=False, auth_disabled=True)

    session = decode_flask_session(request.cookies.get(_SESSION_COOKIE_NAME, ""))
    if session is None:
        return Identity(authenticated=False, auth_disabled=False)

    authenticated = session.get("auth") == expected_auth_value(code)
    return Identity(authenticated=authenticated, auth_disabled=False, session=session)


def require_auth(request: Request) -> Identity:
    """要求請求已通過認證，否則拋出 401。

    這是 Studio 所有業務端點的預設相依。
    """

    identity = identify(request)
    if identity.auth_disabled or identity.authenticated:
        return identity
    raise UnauthorizedError("需要登入才能使用 Studio API")


def require_admin(request: Request) -> Identity:
    """要求管理權限，否則拋出 403。

    供供應商／模型等設定類端點使用。
    """

    identity = require_auth(request)
    if not identity.is_admin:
        raise ForbiddenError("需要管理權限")
    return identity


CurrentUser = Annotated[Identity, Depends(require_auth)]
AdminUser = Annotated[Identity, Depends(require_admin)]
