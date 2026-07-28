"""分鏡與其子資源 Service。

狀態語意（本檔的核心規則）：
- `Shot.status` 只表示資訊提取確認狀態，由 `recompute_status()` 從候選資料推導，
  **不接受外部直接寫入**。
- 「是否可以生成影片」由 `readiness()` 另行計算，與 `status` 分離。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from studio.core import ids
from studio.core.errors import ValidationError
from studio.models.shot import (
    Shot,
    ShotDetail,
    ShotDialogue,
    ShotDialogueCandidate,
    ShotExtractedCandidate,
    ShotFrame,
)
from studio.models.types import (
    AssetKind,
    ShotCandidateStatus,
    ShotDialogueCandidateStatus,
    ShotFrameType,
    ShotStatus,
)
from studio.repositories import (
    ChapterRepository,
    ShotAssetLinkRepository,
    ShotCandidateRepository,
    ShotDialogueCandidateRepository,
    ShotDialogueRepository,
    ShotFrameRepository,
    ShotRepository,
)
from studio.schemas.shot import (
    CandidateResolve,
    DialogueCandidateResolve,
    ShotAssetLinkCreate,
    ShotCreate,
    ShotDetailPayload,
    ShotDialogueCreate,
    ShotDialogueUpdate,
    ShotFrameCreate,
    ShotFrameUpdate,
    ShotUpdate,
)
from studio.services.base import apply_updates, ensure_found, translate_integrity_error


class ShotService:
    """分鏡業務邏輯。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shots = ShotRepository(session)
        self.chapters = ChapterRepository(session)
        self.frames = ShotFrameRepository(session)
        self.dialogues = ShotDialogueRepository(session)
        self.candidates = ShotCandidateRepository(session)
        self.dialogue_candidates = ShotDialogueCandidateRepository(session)
        self.asset_links = ShotAssetLinkRepository(session)

    # ── 分鏡 ──────────────────────────────────────────────────────────────────

    async def list_shots(
        self,
        chapter_id: str,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
        status: ShotStatus | None = None,
    ) -> tuple[list[Shot], int]:
        """分頁列出某章節的分鏡（預設依鏡頭序號遞增）。"""

        ensure_found(await self.chapters.get(chapter_id), resource="章節", entity_id=chapter_id)
        return await self.shots.list_page(
            offset=offset,
            limit=limit,
            search=search,
            chapter_id=chapter_id,
            status=status,
        )

    async def get_shot(self, shot_id: str) -> Shot:
        """取得分鏡，不存在時拋出 404。"""

        return ensure_found(await self.shots.get(shot_id), resource="分鏡", entity_id=shot_id)

    async def create_shot(self, chapter_id: str, payload: ShotCreate) -> Shot:
        """建立分鏡並一併建立細節列。

        `index` 未指定時自動接續章節內的下一個序號。
        `ShotDetail` 一律建立（而非等到需要時才補），
        讓後續讀取不必處理「細節可能不存在」的分支。
        """

        chapter = ensure_found(await self.chapters.get(chapter_id), resource="章節", entity_id=chapter_id)

        index = payload.index
        if index is None:
            index = await self.shots.max_index(chapter_id=chapter_id) + 1

        shot = Shot(
            id=ids.new_id(ids.SHOT),
            chapter_id=chapter_id,
            index=index,
            title=payload.title,
            script_excerpt=payload.script_excerpt,
        )
        try:
            shot = await self.shots.add(shot)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="分鏡") from exc

        detail = ShotDetail(id=shot.id)
        if payload.detail is not None:
            self._apply_detail(detail, payload.detail)
        self.session.add(detail)
        await self.session.flush()

        chapter.shot_count = await self.shots.count(chapter_id=chapter_id)
        await self.session.flush()

        # 重新取回：細節是在分鏡建立之後才加入的，直接回傳原物件時
        # `detail` 關聯尚未載入，序列化時會觸發 async 下無法執行的延遲載入。
        return await self.get_shot(shot.id)

    async def update_shot(self, shot_id: str, payload: ShotUpdate) -> Shot:
        """更新分鏡；未傳入的欄位保留原值。

        `status` 不在可寫欄位中 —— 它由提取確認進度推導。
        """

        shot = await self.get_shot(shot_id)
        apply_updates(shot, payload, allowed={"title", "index", "script_excerpt", "skip_extraction"})

        if payload.detail is not None:
            detail = await self._ensure_detail(shot)
            self._apply_detail(detail, payload.detail)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="分鏡") from exc

        # 跳過提取的設定會直接影響 ready 判定，需重算。
        if payload.skip_extraction is not None:
            await self.recompute_status(shot_id)
        return await self.get_shot(shot_id)

    async def delete_shot(self, shot_id: str) -> None:
        """刪除分鏡（細節、幀、對白、候選由外鍵級聯清除）。"""

        shot = await self.get_shot(shot_id)
        chapter_id = shot.chapter_id
        await self.shots.remove(shot)

        chapter = await self.chapters.get(chapter_id)
        if chapter is not None:
            chapter.shot_count = await self.shots.count(chapter_id=chapter_id)
            await self.session.flush()

    async def _ensure_detail(self, shot: Shot) -> ShotDetail:
        """取得分鏡細節，必要時補建。"""

        detail = await self.session.get(ShotDetail, shot.id)
        if detail is None:
            detail = ShotDetail(id=shot.id)
            self.session.add(detail)
            await self.session.flush()
        return detail

    @staticmethod
    def _apply_detail(detail: ShotDetail, payload: ShotDetailPayload) -> None:
        """把細節 payload 套用到 ORM 物件（未傳入的欄位保留原值）。"""

        for field, value in payload.model_dump(exclude_unset=True).items():
            if value is not None and hasattr(detail, field):
                setattr(detail, field, value)

    # ── 狀態與準備度 ──────────────────────────────────────────────────────────

    async def recompute_status(self, shot_id: str) -> Shot:
        """依提取確認進度重算分鏡狀態。

        規則：
        - 明確跳過提取 → `ready`
        - 尚有 pending 候選（資產或對白）→ `pending`
        - 從未執行過提取 → `pending`（區別於「提取後結果為空」）
        - 其餘 → `ready`
        """

        shot = await self.get_shot(shot_id)

        pending_assets = await self.candidates.count(shot_id=shot_id, status=ShotCandidateStatus.pending)
        pending_dialogues = await self.dialogue_candidates.count(
            shot_id=shot_id, status=ShotDialogueCandidateStatus.pending
        )

        if shot.skip_extraction:
            shot.status = ShotStatus.ready
        elif pending_assets or pending_dialogues:
            shot.status = ShotStatus.pending
        elif shot.last_extracted_at is None:
            shot.status = ShotStatus.pending
        else:
            shot.status = ShotStatus.ready

        await self.session.flush()
        return shot

    async def readiness(self, shot_id: str) -> dict[str, object]:
        """計算分鏡的三種狀態。

        刻意與 `status` 分離：`status == ready` 只代表資訊確認完成，
        不代表已具備生成影片所需的素材與參數。
        """

        shot = await self.get_shot(shot_id)
        detail = await self.session.get(ShotDetail, shot.id)

        pending_assets = await self.candidates.count(shot_id=shot_id, status=ShotCandidateStatus.pending)
        pending_dialogues = await self.dialogue_candidates.count(
            shot_id=shot_id, status=ShotDialogueCandidateStatus.pending
        )

        blocking: list[str] = []
        if shot.status is not ShotStatus.ready:
            blocking.append("分鏡尚未完成資訊提取確認")
        if detail is None or not detail.description:
            blocking.append("缺少鏡頭描述")
        if detail is None or detail.duration_seconds <= 0:
            blocking.append("缺少鏡頭時長")

        first_frames = await self.frames.count(shot_id=shot_id, frame_type=ShotFrameType.first)
        if not first_frames:
            blocking.append("缺少首幀")

        return {
            "shot_id": shot_id,
            "status": shot.status,
            "pending_candidate_count": pending_assets,
            "pending_dialogue_count": pending_dialogues,
            "video_ready": not blocking,
            "blocking_reasons": blocking,
        }

    # ── 關鍵幀 ────────────────────────────────────────────────────────────────

    async def list_frames(self, shot_id: str) -> list[ShotFrame]:
        """列出分鏡的所有關鍵幀。"""

        await self.get_shot(shot_id)
        items, _ = await self.frames.list_page(offset=0, limit=500, order_by="index", shot_id=shot_id)
        return items

    async def create_frame(self, shot_id: str, payload: ShotFrameCreate) -> ShotFrame:
        """為分鏡新增關鍵幀。"""

        await self.get_shot(shot_id)
        frame = ShotFrame(id=ids.new_id(ids.SHOT_FRAME), shot_id=shot_id, **payload.model_dump())
        try:
            return await self.frames.add(frame)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="關鍵幀") from exc

    async def update_frame(self, frame_id: str, payload: ShotFrameUpdate) -> ShotFrame:
        """更新關鍵幀；未傳入的欄位保留原值。"""

        frame = ensure_found(await self.frames.get(frame_id), resource="關鍵幀", entity_id=frame_id)
        apply_updates(frame, payload)
        await self.session.flush()
        return frame

    async def delete_frame(self, frame_id: str) -> None:
        """刪除關鍵幀。"""

        await self.frames.remove(ensure_found(await self.frames.get(frame_id), resource="關鍵幀", entity_id=frame_id))

    # ── 對白 ──────────────────────────────────────────────────────────────────

    async def list_dialogues(self, shot_id: str) -> list[ShotDialogue]:
        """列出分鏡的對白（依順序）。"""

        await self.get_shot(shot_id)
        items, _ = await self.dialogues.list_page(offset=0, limit=500, order_by="index", shot_id=shot_id)
        return items

    async def create_dialogue(self, shot_id: str, payload: ShotDialogueCreate) -> ShotDialogue:
        """新增對白；`index` 未指定時附加於最後。"""

        await self.get_shot(shot_id)

        index = payload.index
        if index is None:
            index = await self.dialogues.max_index(shot_id=shot_id) + 1

        data = payload.model_dump(exclude={"index"})
        dialogue = ShotDialogue(id=ids.new_id(ids.SHOT_DIALOGUE), shot_id=shot_id, index=index, **data)
        try:
            return await self.dialogues.add(dialogue)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="對白") from exc

    async def update_dialogue(self, dialogue_id: str, payload: ShotDialogueUpdate) -> ShotDialogue:
        """更新對白；未傳入的欄位保留原值。"""

        dialogue = ensure_found(await self.dialogues.get(dialogue_id), resource="對白", entity_id=dialogue_id)
        apply_updates(dialogue, payload)
        await self.session.flush()
        return dialogue

    async def delete_dialogue(self, dialogue_id: str) -> None:
        """刪除對白。"""

        await self.dialogues.remove(
            ensure_found(await self.dialogues.get(dialogue_id), resource="對白", entity_id=dialogue_id)
        )

    # ── 提取候選 ──────────────────────────────────────────────────────────────

    async def list_candidates(
        self,
        shot_id: str,
        *,
        status: ShotCandidateStatus | None = None,
    ) -> list[ShotExtractedCandidate]:
        """列出資產提取候選。"""

        await self.get_shot(shot_id)
        items, _ = await self.candidates.list_page(offset=0, limit=500, shot_id=shot_id, status=status)
        return items

    async def resolve_candidate(self, candidate_id: str, payload: CandidateResolve) -> ShotExtractedCandidate:
        """確認或忽略一筆資產候選。

        已處理過的候選不可重複處理 —— 重複確認會產生重複的資產關聯。
        """

        candidate = ensure_found(await self.candidates.get(candidate_id), resource="候選", entity_id=candidate_id)

        if candidate.status is not ShotCandidateStatus.pending:
            raise ValidationError(
                f"候選已處理過（目前狀態：{candidate.status.value}）",
                details={"candidate_id": candidate_id, "status": candidate.status.value},
            )

        if payload.action == "ignore":
            candidate.status = ShotCandidateStatus.ignored
        else:
            if not payload.linked_entity_id:
                raise ValidationError("action=link 時必須提供 linked_entity_id")
            candidate.status = ShotCandidateStatus.linked
            candidate.linked_entity_id = payload.linked_entity_id

            # 確認後同步建立分鏡↔資產關聯，讓生成流程能直接取用。
            await self.link_asset(
                candidate.shot_id,
                ShotAssetLinkCreate(
                    asset_kind=AssetKind(candidate.candidate_type.value),
                    asset_id=payload.linked_entity_id,
                ),
                ignore_duplicate=True,
            )

        await self.session.flush()
        await self.recompute_status(candidate.shot_id)
        return candidate

    async def list_dialogue_candidates(self, shot_id: str) -> list[ShotDialogueCandidate]:
        """列出對白提取候選。"""

        await self.get_shot(shot_id)
        items, _ = await self.dialogue_candidates.list_page(offset=0, limit=500, order_by="index", shot_id=shot_id)
        return items

    async def resolve_dialogue_candidate(
        self,
        candidate_id: str,
        payload: DialogueCandidateResolve,
    ) -> ShotDialogueCandidate:
        """接受或忽略一筆對白候選。

        接受時同步建立正式對白紀錄。
        """

        candidate = ensure_found(
            await self.dialogue_candidates.get(candidate_id), resource="對白候選", entity_id=candidate_id
        )

        if candidate.status is not ShotDialogueCandidateStatus.pending:
            raise ValidationError(f"對白候選已處理過（目前狀態：{candidate.status.value}）")

        if payload.action == "ignore":
            candidate.status = ShotDialogueCandidateStatus.ignored
        else:
            candidate.status = ShotDialogueCandidateStatus.accepted
            await self.create_dialogue(
                candidate.shot_id,
                ShotDialogueCreate(
                    content=candidate.content,
                    speaker_name=candidate.speaker_name,
                    character_id=payload.character_id,
                    mode=candidate.mode,
                ),
            )

        await self.session.flush()
        await self.recompute_status(candidate.shot_id)
        return candidate

    async def mark_extracted(self, shot_id: str) -> Shot:
        """標記分鏡已完成一次提取。

        區分「從未提取」與「提取後結果為空」—— 後者才可能進入 ready。
        """

        shot = await self.get_shot(shot_id)
        shot.last_extracted_at = datetime.now(UTC)
        await self.session.flush()
        return await self.recompute_status(shot_id)

    # ── 分鏡資產關聯 ──────────────────────────────────────────────────────────

    async def list_asset_links(self, shot_id: str, *, asset_kind: AssetKind | None = None):
        """列出分鏡引用的資產。"""

        await self.get_shot(shot_id)
        items, _ = await self.asset_links.list_page(
            offset=0, limit=500, order_by="index", shot_id=shot_id, asset_kind=asset_kind
        )
        return items

    async def link_asset(
        self,
        shot_id: str,
        payload: ShotAssetLinkCreate,
        *,
        ignore_duplicate: bool = False,
    ):
        """建立分鏡↔資產關聯。

        Args:
            ignore_duplicate: 為 True 時，重複關聯視為成功（供候選確認流程使用，
                避免使用者重按造成 409）。
        """

        from studio.models.asset import ShotAssetLink

        await self.get_shot(shot_id)

        existing, _ = await self.asset_links.list_page(
            offset=0,
            limit=1,
            shot_id=shot_id,
            asset_kind=payload.asset_kind,
            asset_id=payload.asset_id,
        )
        if existing:
            if ignore_duplicate:
                return existing[0]
            raise translate_integrity_error(IntegrityError("duplicate", None, Exception()), resource="分鏡資產關聯")

        link = ShotAssetLink(shot_id=shot_id, **payload.model_dump())
        try:
            return await self.asset_links.add(link)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="分鏡資產關聯") from exc

    async def unlink_asset(self, shot_id: str, asset_kind: AssetKind, asset_id: str) -> None:
        """移除分鏡↔資產關聯。"""

        removed = await self.asset_links.remove_where(
            shot_id=shot_id, asset_kind=asset_kind, asset_id=asset_id
        )
        if not removed:
            ensure_found(None, resource="分鏡資產關聯", entity_id=f"{shot_id}/{asset_kind.value}/{asset_id}")
