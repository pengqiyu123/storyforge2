from __future__ import annotations

import re
from dataclasses import dataclass

from engine.providers.llm_provider import LLMProvider
from engine.utils.chinese_text import count_chinese_chars


DEFAULT_WRITER_GENERATION_CONFIG = {
    "temperature": 0.85,
    "top_p": 0.92,
    "frequency_penalty": 0.15,
    "presence_penalty": 0.1,
}

FORBIDDEN_STYLE_WORDS = "突然、原来如此、一股、本章、下一章、修订稿里、这一章必须"


def _summarize_truth_slice(truth_context_slice: dict) -> str:
    canon_facts = truth_context_slice.get("canon", {}).get("facts", [])
    characters = truth_context_slice.get("characters", {}).get("characters", [])
    hooks = truth_context_slice.get("hook_ledger", {}).get("hooks", [])
    canon_lines = [str(item.get("statement", "")).strip() for item in canon_facts[:5] if isinstance(item, dict)]
    character_lines = []
    for item in characters[:5]:
        if not isinstance(item, dict):
            continue
        display_name = str(item.get("display_name", "")).strip()
        location = str(item.get("current_location", "")).strip()
        if display_name:
            character_lines.append(f"{display_name}:{location or '位置未明'}")
    hook_lines = [str(item.get("label", "")).strip() for item in hooks[:5] if isinstance(item, dict)]
    sections = []
    if canon_lines:
        sections.append("已提交事实：" + "；".join(line for line in canon_lines if line))
    if character_lines:
        sections.append("角色状态：" + "；".join(character_lines))
    if hook_lines:
        sections.append("未完钩子：" + "；".join(line for line in hook_lines if line))
    return "\n".join(sections)


def _clean_text_response(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            cleaned = "\n".join(lines[1:-1]).strip()
    cleaned = re.sub(r"^\s*(以下是|下面是|这是|输出如下)[:：]\s*", "", cleaned)
    cleaned = re.sub(r"\n\s*(以上|希望这段|如果需要).*$", "", cleaned, flags=re.S)
    return cleaned.strip()


@dataclass(slots=True)
class PlaceholderChapterWriter:
    def generate_initial(self, *, chapter_no: int, **_: object) -> str:
        paragraph = (
            f"第{chapter_no}章里，林七压着呼吸潜进旧仓库，雨水沿着衣角往下淌。"
            "他知道账册一旦落进对手手里，城南整条线都会被顺藤摸瓜。"
            "仓库深处的灯忽明忽暗，像有人故意把脚步藏进黑里。"
            "林七没有急着闯进去，而是先听门后的呼吸，再判断谁在等他。"
            "这一章必须把局势往前推，但不能提前把真相全掀开。"
            "门缝里那丝铁锈味提醒他，今晚真正危险的还不是账册，而是等在账册后面的人。"
            "他贴着木箱慢慢移动，把每一次呼吸都压到最短，免得惊动二层铁梯后的埋伏。"
            "远处忽然传来金属轻碰的脆响，像谁不小心碰到了钥匙扣，短促得只够让人心口一紧。"
            "林七顺势停下，把目光压进更深的阴影里，先分辨敌意，再决定要不要提前亮出底牌。"
        )
        return "\n\n".join([paragraph for _ in range(4)])

    def generate_revision(self, *, chapter_no: int, revision_mode: str, **_: object) -> str:
        if revision_mode == "rework":
            paragraph = (
                f"第{chapter_no}章修订稿出现回退标记，林七在仓库门口反复解释局势，"
                "节奏被拖慢，关键动作没有推进，回退标记仍然挂在正文里。"
                "他一次次复述账册的重要性，却迟迟没有做出新的判断，连门后的脚步声都被解释段冲淡。"
                "原本应该在这一章里压出的危险感，被拖成长句和重复说明，读者只能看到信息回流，看不到局势真正变化。"
                "他不断重申风险评估和推进目标，把本该落在动作里的危机说成报告，句子越写越平。"
                "哪怕有人从铁梯后探头，他也没有立刻应对，而是继续解释自己为什么不能后退。"
            )
            return "\n\n".join([paragraph for _ in range(4)])
        paragraph = (
            f"第{chapter_no}章修订稿里，林七压下解释欲，只保留最关键的动作与判断。"
            "他借雨声遮住脚步，先锁定门后埋伏，再逼出对手真正想抢的旧账册。"
            "章尾钩子落在账册里缺失的一页，让悬念更集中，逻辑也更干净。"
            "沈砚留下的半句暗号也在这里第一次落地，既推动当前冲突，又把下一章真正的追查方向钉住。"
            "门后的第三个人终于出声，逼得林七当场做出选择。"
            "他没有再用解释填满空隙，而是让每一次停顿都服务于判断，让危险感落回动作本身。"
            "当账册边角露出来的时候，他先确认缺页编号，再意识到真正的线索不在纸页，而在替账册守门的人。"
        )
        return "\n\n".join([paragraph for _ in range(4)])


@dataclass(slots=True)
class RealChapterWriter:
    provider: LLMProvider
    generation_config: dict[str, float]

    def generate_initial(
        self,
        *,
        chapter_no: int,
        plan_payload: dict,
        compose_payload: dict,
        truth_context_slice: dict,
    ) -> str:
        prompt = self._build_initial_prompt(
            chapter_no=chapter_no,
            plan_payload=plan_payload,
            compose_payload=compose_payload,
            truth_context_slice=truth_context_slice,
        )
        return self._generate_and_validate(
            task_name="chapter_write_initial",
            system_prompt=prompt,
            user_payload={
                "chapter_no": chapter_no,
                "plan": plan_payload,
                "compose_context": compose_payload,
            },
        )

    def generate_revision(
        self,
        *,
        chapter_no: int,
        plan_payload: dict,
        compose_payload: dict,
        truth_context_slice: dict,
        revision_brief: dict,
        failed_rule_messages: list[str],
        top_audit_issues: list[str],
        low_dimensions: list[str],
        truth_conflict_messages: list[str],
        revision_mode: str,
    ) -> str:
        prompt = self._build_revision_prompt(
            chapter_no=chapter_no,
            plan_payload=plan_payload,
            compose_payload=compose_payload,
            truth_context_slice=truth_context_slice,
            revision_brief=revision_brief,
            failed_rule_messages=failed_rule_messages,
            top_audit_issues=top_audit_issues,
            low_dimensions=low_dimensions,
            truth_conflict_messages=truth_conflict_messages,
            revision_mode=revision_mode,
        )
        return self._generate_and_validate(
            task_name="chapter_write_revision",
            system_prompt=prompt,
            user_payload={
                "chapter_no": chapter_no,
                "plan": plan_payload,
                "compose_context": compose_payload,
                "revision_brief": revision_brief,
            },
        )

    def _generate_and_validate(self, *, task_name: str, system_prompt: str, user_payload: dict) -> str:
        result = self.provider.generate_text(
            task_name,
            system_prompt,
            user_payload,
            generation_config=self.generation_config,
        )
        if result.startswith("error:"):
            raise ValueError(f"draft_generation_failed:{result[6:].strip()}")
        cleaned = _clean_text_response(result)
        if not cleaned:
            raise ValueError("draft_generation_failed:empty_text")
        if count_chinese_chars(cleaned) < 800:
            raise ValueError("draft_generation_failed:below_min_chinese_char_count")
        if self._looks_like_wrapper(cleaned):
            raise ValueError("draft_generation_failed:not_story_text")
        return cleaned

    @staticmethod
    def _looks_like_wrapper(text: str) -> bool:
        wrapper_markers = ("以下是", "下面是", "这是一段", "说明", "修改建议", "输出如下")
        return any(text.startswith(marker) for marker in wrapper_markers)

    def _build_initial_prompt(self, *, chapter_no: int, plan_payload: dict, compose_payload: dict, truth_context_slice: dict) -> str:
        constraints = "；".join(str(item) for item in compose_payload.get("constraints", []) if str(item).strip())
        materials = "；".join(str(item) for item in compose_payload.get("materials", []) if str(item).strip())
        must_advance = "；".join(str(item) for item in plan_payload.get("must_advance", []) if str(item).strip())
        must_not_do = "；".join(str(item) for item in plan_payload.get("must_not_do", []) if str(item).strip())
        truth_summary = _summarize_truth_slice(truth_context_slice)
        return (
            "你是一位中文悬疑小说作家，擅长用动作、对话和环境细节推进冲突，避免解释腔和报告腔。\n"
            f"[STYLE: 比喻密度≥1.5/百字；对话占比25%-30%；句长15-25字/句；禁用词：{FORBIDDEN_STYLE_WORDS}]\n"
            f"章节：第{chapter_no}章。\n"
            f"章节指导：{plan_payload.get('guidance') or plan_payload.get('hook_target') or ''}\n"
            f"必须推进：{must_advance or '无'}\n"
            f"禁止事项：{must_not_do or '无'}\n"
            f"风格约束：{constraints or '中文小说口吻'}\n"
            f"素材摘要：{materials or '无'}\n"
            f"已提交真相摘要：{truth_summary or '当前无已提交真相负担。'}\n"
            "直接输出正文，不要写解释，不要写标题说明，不要使用作者口吻。"
        )

    def _build_revision_prompt(
        self,
        *,
        chapter_no: int,
        plan_payload: dict,
        compose_payload: dict,
        truth_context_slice: dict,
        revision_brief: dict,
        failed_rule_messages: list[str],
        top_audit_issues: list[str],
        low_dimensions: list[str],
        truth_conflict_messages: list[str],
        revision_mode: str,
    ) -> str:
        truth_summary = _summarize_truth_slice(truth_context_slice)
        fix_targets = "；".join(str(item) for item in revision_brief.get("fix_targets", []) if str(item).strip())
        must_not_touch = "；".join(str(item) for item in revision_brief.get("must_not_touch", []) if str(item).strip())
        risk_points = "；".join(str(item) for item in revision_brief.get("risk_points", []) if str(item).strip())
        failed_rules = "；".join(failed_rule_messages[:8]) or "无"
        audit_issues = "；".join(top_audit_issues[:8]) or "无"
        low_dims = "；".join(low_dimensions[:2]) or "无"
        truth_conflicts = "；".join(truth_conflict_messages[:6]) or "无"
        return (
            "你是一位中文悬疑小说修订作者，负责在不破坏章节 premise 和已提交真相的前提下，修复审计失败稿。\n"
            f"[STYLE: 比喻密度≥1.5/百字；对话占比25%-30%；句长15-25字/句；禁用词：{FORBIDDEN_STYLE_WORDS}]\n"
            f"章节：第{chapter_no}章。\n"
            f"修订模式：{revision_mode}。\n"
            f"章节指导：{plan_payload.get('guidance') or plan_payload.get('hook_target') or ''}\n"
            f"必须修复：{fix_targets or '无'}\n"
            f"不可触碰：{must_not_touch or '无'}\n"
            f"风险点：{risk_points or '无'}\n"
            f"机械告警：{failed_rules}\n"
            f"审计问题：{audit_issues}\n"
            f"最低维度：{low_dims}\n"
            f"真相冲突禁区：{truth_conflicts}\n"
            f"已提交真相摘要：{truth_summary or '当前无已提交真相负担。'}\n"
            "直接输出修订后的正文，不要写解释，不要写修订说明，不要写作者意图。"
        )
