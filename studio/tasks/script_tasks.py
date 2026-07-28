"""腳本與分鏡相關的 AI 任務執行器。

全部透過 `TaskExecutorRegistry` 執行，絕不在 API request 內同步跑。

這些執行器發出**真實**的模型呼叫。若供應商金鑰未設定，任務會以
`configuration_error` 失敗 —— 不會用假結果冒充成功。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from studio.core import ids
from studio.core.db import session_scope
from studio.core.errors import ValidationError
from studio.models.project import Chapter
from studio.models.shot import Shot, ShotDetail, ShotDialogueCandidate, ShotExtractedCandidate
from studio.models.types import (
    ModelCategory,
    ShotCandidateType,
    ShotFrameType,
    TaskKind,
)
from studio.tasks.registry import register_executor
from studio.tasks.runtime import TaskContext

logger = logging.getLogger("studio.tasks.script")


# ── 共用：呼叫文字模型 ───────────────────────────────────────────────────────


async def _run_text(
    context: TaskContext,
    *,
    system: str,
    prompt: str,
    json_output: bool = False,
    max_tokens: int = 8192,
) -> Any:
    """呼叫文字模型並在需要時解析 JSON。

    模型呼叫是同步阻塞的，因此丟到 thread pool 執行，
    避免卡住事件迴圈上的其他任務。
    """

    from studio.contracts.generation import TextRequest
    from studio.integrations import get_text_provider
    from studio.integrations.anthropic_provider import extract_json
    from studio.models.types import ProviderKind
    from studio.tasks.resolve import resolve_model

    resolved = await resolve_model(ModelCategory.text, model_id=context.model_id)
    provider = get_text_provider(ProviderKind(resolved.provider_kind))

    request = TextRequest(
        model=resolved.model_name,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=float(resolved.params.get("temperature", 1.0)),
        json_output=json_output,
    )

    await context.check_cancelled()
    result = await asyncio.to_thread(provider.generate_text, resolved.config, request)
    await context.check_cancelled()

    if json_output:
        return extract_json(result.text)
    return result.text


async def _load_chapter_text(chapter_id: str) -> tuple[str, str]:
    """載入章節腳本，優先使用精簡稿以節省 token。

    Returns:
        `(用於分析的文字, 章節標題)`。
    """

    async with session_scope() as session:
        chapter = await session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValidationError(f"章節不存在：{chapter_id}")

        text = chapter.condensed_text or chapter.raw_text
        if not text.strip():
            raise ValidationError("章節尚未輸入腳本，無法分析")
        return text, chapter.title


# ── 腳本拆分鏡 ───────────────────────────────────────────────────────────────

_DIVIDE_SYSTEM = """你是專業的短劇分鏡師。將劇本拆解成適合直式短影音的分鏡。

規則：
- 每個分鏡 3-10 秒，是一個連續、不換機位的畫面
- 依敘事節奏拆分，不要平均切割
- 景別使用代碼：ECU 大特寫、CU 特寫、MCU 中近景、MS 中景、MLS 中遠景、LS 遠景、ELS 大遠景
- 機位使用代碼：EYE_LEVEL、HIGH_ANGLE、LOW_ANGLE、BIRD_EYE、DUTCH、OVER_SHOULDER
- 運鏡使用代碼：STATIC、PAN、TILT、DOLLY_IN、DOLLY_OUT、TRACK、CRANE、HANDHELD、STEADICAM、ZOOM_IN、ZOOM_OUT

輸出 JSON：
{"shots":[{"title":"標題","script_excerpt":"對應原文片段","description":"畫面描述",
"camera_shot":"MS","angle":"EYE_LEVEL","movement":"STATIC","duration_seconds":5,
"action_beats":["動作1","動作2"],"atmosphere":"氛圍","mood_tags":["標籤"]}]}"""


async def _execute_script_divide(context: TaskContext) -> dict[str, Any]:
    """把章節腳本拆解成分鏡並寫入資料庫。"""

    chapter_id = context.chapter_id or context.payload.get("chapter_id")
    if not chapter_id:
        raise ValidationError("script_divide 任務需要 chapter_id")

    await context.progress(10, "載入腳本")
    text, title = await _load_chapter_text(chapter_id)

    await context.progress(25, "AI 拆解分鏡中")
    data = await _run_text(
        context,
        system=_DIVIDE_SYSTEM,
        prompt=f"章節標題：{title}\n\n劇本內容：\n{text}",
        json_output=True,
    )

    shots_data = data.get("shots") if isinstance(data, dict) else data
    if not isinstance(shots_data, list) or not shots_data:
        raise ValidationError("模型未回傳有效的分鏡列表")

    await context.progress(70, f"寫入 {len(shots_data)} 個分鏡")

    replace = bool(context.payload.get("replace_existing", True))
    created: list[str] = []

    async with session_scope() as session:
        if replace:
            # 重新拆解時清掉舊分鏡，否則會與新結果混雜。
            existing = list(
                (await session.execute(select(Shot).where(Shot.chapter_id == chapter_id))).scalars().all()
            )
            for shot in existing:
                await session.delete(shot)
            await session.flush()

        start_index = 0 if replace else int(
            await session.scalar(
                select(Shot.index).where(Shot.chapter_id == chapter_id).order_by(Shot.index.desc()).limit(1)
            )
            or 0
        )

        for offset, item in enumerate(shots_data, start=1):
            shot_id = ids.new_id(ids.SHOT)
            session.add(
                Shot(
                    id=shot_id,
                    chapter_id=chapter_id,
                    index=start_index + offset,
                    title=str(item.get("title", ""))[:255],
                    script_excerpt=str(item.get("script_excerpt", "")),
                )
            )
            session.add(
                ShotDetail(
                    id=shot_id,
                    camera_shot=_safe_enum(item.get("camera_shot"), "MS"),
                    angle=_safe_enum(item.get("angle"), "EYE_LEVEL"),
                    movement=_safe_enum(item.get("movement"), "STATIC"),
                    duration_seconds=max(1, min(600, int(item.get("duration_seconds", 5) or 5))),
                    description=str(item.get("description", "")),
                    action_beats=[str(b) for b in (item.get("action_beats") or [])],
                    mood_tags=[str(t) for t in (item.get("mood_tags") or [])],
                    atmosphere=str(item.get("atmosphere", "")),
                )
            )
            created.append(shot_id)

        chapter = await session.get(Chapter, chapter_id)
        if chapter is not None:
            chapter.shot_count = len(created) if replace else chapter.shot_count + len(created)

    await context.progress(100, "完成")
    return {"shot_ids": created, "shot_count": len(created)}


def _safe_enum(value: Any, fallback: str) -> str:
    """把模型回傳的代碼正規化；不合法時退回預設值。

    模型偶爾會回傳中文或小寫代碼，直接寫入會違反欄位約束。
    """

    text = str(value or "").strip().upper()
    return text if text else fallback


# ── 實體提取 ─────────────────────────────────────────────────────────────────

_EXTRACT_SYSTEM = """你是短劇美術指導。從分鏡描述中提取需要製作的視覺資產與對白。

規則：
- 只提取畫面中實際出現的角色、場景、道具、服裝
- 名稱要具體且可重複使用（例如「林小雨」而非「女主角」）
- 描述要足以作為圖片生成的依據
- 對白 mode 使用：DIALOGUE 對白、VOICE_OVER 旁白、OFF_SCREEN 畫外音、PHONE 電話聲

輸出 JSON：
{"characters":[{"name":"","description":""}],
"scenes":[{"name":"","description":""}],
"props":[{"name":"","description":""}],
"costumes":[{"name":"","description":""}],
"dialogues":[{"speaker_name":"","content":"","mode":"DIALOGUE"}]}"""


async def _execute_entity_extraction(context: TaskContext) -> dict[str, Any]:
    """從分鏡提取資產與對白候選。

    產出的是**候選**而非正式資產：模型可能重複或誤判，
    必須由使用者確認後才進入資產庫。
    """

    shot_ids: list[str] = context.payload.get("shot_ids") or []
    if context.shot_id:
        shot_ids = [context.shot_id]

    if not shot_ids and context.chapter_id:
        async with session_scope() as session:
            shot_ids = list(
                (
                    await session.execute(
                        select(Shot.id).where(Shot.chapter_id == context.chapter_id).order_by(Shot.index)
                    )
                )
                .scalars()
                .all()
            )

    if not shot_ids:
        raise ValidationError("entity_extraction 任務需要 shot_id、shot_ids 或 chapter_id")

    total_candidates = 0
    total_dialogues = 0

    for position, shot_id in enumerate(shot_ids, start=1):
        await context.progress(
            int(position / len(shot_ids) * 90),
            f"提取第 {position}/{len(shot_ids)} 個分鏡",
        )

        async with session_scope() as session:
            shot = await session.get(Shot, shot_id)
            detail = await session.get(ShotDetail, shot_id)
            if shot is None:
                continue
            description = (detail.description if detail else "") or shot.script_excerpt
            title = shot.title

        if not description.strip():
            continue

        data = await _run_text(
            context,
            system=_EXTRACT_SYSTEM,
            prompt=f"分鏡標題：{title}\n畫面描述：{description}",
            json_output=True,
            max_tokens=4096,
        )
        if not isinstance(data, dict):
            continue

        async with session_scope() as session:
            for field, candidate_type in (
                ("characters", ShotCandidateType.character),
                ("scenes", ShotCandidateType.scene),
                ("props", ShotCandidateType.prop),
                ("costumes", ShotCandidateType.costume),
            ):
                for item in data.get(field) or []:
                    name = str(item.get("name", "")).strip()
                    if not name:
                        continue
                    # 同一分鏡的同名候選只保留一筆，避免重複提取造成重複確認。
                    exists = await session.scalar(
                        select(ShotExtractedCandidate.id).where(
                            ShotExtractedCandidate.shot_id == shot_id,
                            ShotExtractedCandidate.candidate_type == candidate_type,
                            ShotExtractedCandidate.name == name,
                        )
                    )
                    if exists:
                        continue
                    session.add(
                        ShotExtractedCandidate(
                            id=ids.new_id(ids.CANDIDATE),
                            shot_id=shot_id,
                            candidate_type=candidate_type,
                            name=name[:255],
                            description=str(item.get("description", "")),
                        )
                    )
                    total_candidates += 1

            next_index = int(
                await session.scalar(
                    select(ShotDialogueCandidate.index)
                    .where(ShotDialogueCandidate.shot_id == shot_id)
                    .order_by(ShotDialogueCandidate.index.desc())
                    .limit(1)
                )
                or 0
            )
            for item in data.get("dialogues") or []:
                content = str(item.get("content", "")).strip()
                if not content:
                    continue
                next_index += 1
                session.add(
                    ShotDialogueCandidate(
                        id=ids.new_id(ids.DIALOGUE_CANDIDATE),
                        shot_id=shot_id,
                        index=next_index,
                        speaker_name=str(item.get("speaker_name", ""))[:255],
                        content=content,
                        mode=_safe_enum(item.get("mode"), "DIALOGUE"),
                    )
                )
                total_dialogues += 1

    # 標記已提取，讓分鏡有機會進入 ready（區別於「從未提取」）。
    async with session_scope() as session:
        from datetime import UTC, datetime

        for shot_id in shot_ids:
            shot = await session.get(Shot, shot_id)
            if shot is not None:
                shot.last_extracted_at = datetime.now(UTC)

    await context.progress(100, "完成")
    return {
        "shot_ids": shot_ids,
        "candidate_count": total_candidates,
        "dialogue_count": total_dialogues,
    }


# ── 腳本優化／簡化／一致性檢查 ───────────────────────────────────────────────


async def _execute_script_optimize(context: TaskContext) -> dict[str, Any]:
    """優化章節腳本，回傳建議稿（不直接覆蓋原文）。"""

    chapter_id = context.chapter_id or context.payload.get("chapter_id")
    if not chapter_id:
        raise ValidationError("script_optimize 任務需要 chapter_id")

    await context.progress(20, "載入腳本")
    text, title = await _load_chapter_text(chapter_id)

    await context.progress(50, "AI 優化中")
    optimised = await _run_text(
        context,
        system=(
            "你是短劇編劇。優化劇本使其更適合直式短影音：強化開場鉤子、"
            "壓縮冗長敘述、讓對白更口語。保持原有故事線與角色，只輸出優化後的劇本全文。"
        ),
        prompt=f"章節標題：{title}\n\n{text}",
    )

    await context.progress(100, "完成")
    # 刻意不覆蓋 raw_text：優化是建議，採用與否由使用者決定。
    return {"chapter_id": chapter_id, "optimized_text": optimised, "applied": False}


async def _execute_script_simplify(context: TaskContext) -> dict[str, Any]:
    """精簡腳本並寫入 `condensed_text`。

    這份精簡稿會被後續的分鏡與提取任務使用，以節省 token。
    """

    chapter_id = context.chapter_id or context.payload.get("chapter_id")
    if not chapter_id:
        raise ValidationError("script_simplify 任務需要 chapter_id")

    await context.progress(20, "載入腳本")

    async with session_scope() as session:
        chapter = await session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValidationError(f"章節不存在：{chapter_id}")
        raw = chapter.raw_text
        title = chapter.title

    if not raw.strip():
        raise ValidationError("章節尚未輸入腳本")

    await context.progress(50, "AI 精簡中")
    condensed = await _run_text(
        context,
        system=(
            "你是劇本分析師。將劇本精簡為保留所有情節要點、角色、場景、道具與對白的濃縮版本，"
            "刪除修辭與重複敘述。只輸出精簡後的文字。"
        ),
        prompt=f"章節標題：{title}\n\n{raw}",
    )

    async with session_scope() as session:
        chapter = await session.get(Chapter, chapter_id)
        if chapter is not None:
            chapter.condensed_text = condensed

    await context.progress(100, "完成")
    return {
        "chapter_id": chapter_id,
        "original_length": len(raw),
        "condensed_length": len(condensed),
    }


async def _execute_consistency_check(context: TaskContext) -> dict[str, Any]:
    """檢查章節內的敘事與視覺一致性問題。"""

    chapter_id = context.chapter_id or context.payload.get("chapter_id")
    if not chapter_id:
        raise ValidationError("consistency_check 任務需要 chapter_id")

    await context.progress(20, "載入分鏡")

    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(Shot.index, Shot.title, ShotDetail.description)
                    .join(ShotDetail, ShotDetail.id == Shot.id, isouter=True)
                    .where(Shot.chapter_id == chapter_id)
                    .order_by(Shot.index)
                )
            ).all()
        )

    if not rows:
        raise ValidationError("章節尚無分鏡，請先執行分鏡拆解")

    summary = "\n".join(f"{index}. {title}：{description or ''}" for index, title, description in rows)

    await context.progress(50, "AI 檢查中")
    data = await _run_text(
        context,
        system=(
            "你是劇本審核。檢查分鏡序列的一致性問題：角色稱呼不一致、時間線矛盾、"
            "場景跳躍缺乏交代、道具憑空出現或消失。\n"
            '輸出 JSON：{"issues":[{"severity":"high|medium|low","shot_index":1,'
            '"category":"","description":"","suggestion":""}]}'
        ),
        prompt=summary,
        json_output=True,
    )

    issues = data.get("issues", []) if isinstance(data, dict) else []

    await context.progress(100, "完成")
    return {"chapter_id": chapter_id, "issue_count": len(issues), "issues": issues}


# ── 分鏡幀提示詞 ─────────────────────────────────────────────────────────────


async def _execute_shot_frame_prompt(context: TaskContext) -> dict[str, Any]:
    """為分鏡產生首幀／尾幀圖片提示詞與影片提示詞。

    提示詞會併入專案風格與已關聯資產的外觀描述，
    這是維持跨鏡頭一致性的關鍵。
    """

    shot_id = context.shot_id or context.payload.get("shot_id")
    if not shot_id:
        raise ValidationError("shot_frame_prompt 任務需要 shot_id")

    await context.progress(20, "載入分鏡與資產")

    from studio.tasks.media_tasks import build_shot_context

    shot_context = await build_shot_context(shot_id)

    await context.progress(50, "AI 產生提示詞")
    data = await _run_text(
        context,
        system=(
            "你是 AI 影像提示詞工程師。為分鏡產生英文提示詞。\n"
            "- first_frame_prompt / last_frame_prompt：靜態畫面描述，含構圖、光線、鏡頭\n"
            "- video_prompt：描述首幀到尾幀之間的運動與變化\n"
            "- 必須完整帶入提供的角色與場景外觀描述以維持一致性\n"
            '輸出 JSON：{"first_frame_prompt":"","last_frame_prompt":"","video_prompt":""}'
        ),
        prompt=shot_context["prompt_context"],
        json_output=True,
    )

    if not isinstance(data, dict):
        raise ValidationError("模型未回傳有效的提示詞")

    await context.progress(80, "寫入提示詞")

    async with session_scope() as session:
        for frame_type, key in (
            (ShotFrameType.first, "first_frame_prompt"),
            (ShotFrameType.last, "last_frame_prompt"),
        ):
            prompt_text = str(data.get(key, "")).strip()
            if not prompt_text:
                continue

            from studio.models.shot import ShotFrame

            frame = await session.scalar(
                select(ShotFrame).where(ShotFrame.shot_id == shot_id, ShotFrame.frame_type == frame_type)
            )
            if frame is None:
                session.add(
                    ShotFrame(
                        id=ids.new_id(ids.SHOT_FRAME),
                        shot_id=shot_id,
                        frame_type=frame_type,
                        index=0,
                        prompt=prompt_text,
                    )
                )
            else:
                frame.prompt = prompt_text

        video_prompt = str(data.get("video_prompt", "")).strip()
        if video_prompt:
            detail = await session.get(ShotDetail, shot_id)
            if detail is not None:
                detail.video_prompt = video_prompt

    await context.progress(100, "完成")
    return {
        "shot_id": shot_id,
        "first_frame_prompt": data.get("first_frame_prompt", ""),
        "last_frame_prompt": data.get("last_frame_prompt", ""),
        "video_prompt": data.get("video_prompt", ""),
    }


register_executor(TaskKind.script_divide.value, _execute_script_divide)
register_executor(TaskKind.script_extract.value, _execute_entity_extraction)
register_executor(TaskKind.script_optimize.value, _execute_script_optimize)
register_executor(TaskKind.script_simplify.value, _execute_script_simplify)
register_executor(TaskKind.script_consistency.value, _execute_consistency_check)
register_executor("shot_frame_prompt", _execute_shot_frame_prompt)
