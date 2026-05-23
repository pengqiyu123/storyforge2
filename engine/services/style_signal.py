from __future__ import annotations

import re

from engine.schemas.artifact import StyleSignalRecord
from engine.utils.chinese_text import (
    count_chinese_chars,
    list_sentence_start_repetition,
    paragraph_stats,
    surprise_word_density,
    vague_word_density,
)


class StyleSignalAdapter:
    def __init__(
        self,
        *,
        uniformity_tolerance: float = 0.5,
        vague_tolerance: float = 5.0,
        surprise_tolerance: float = 5.0,
        drift_threshold: float = 0.4,
    ) -> None:
        self.uniformity_tolerance = uniformity_tolerance
        self.vague_tolerance = vague_tolerance
        self.surprise_tolerance = surprise_tolerance
        self.drift_threshold = drift_threshold

    def evaluate(
        self,
        *,
        batch_run: object,
        checkpoint: object,
        slice_payload: dict,
    ) -> StyleSignalRecord | None:
        reference_text = slice_payload.get("reference_text")
        current_text = slice_payload.get("current_text")
        if not reference_text or not current_text:
            return None

        ref_profile = self._profile(reference_text)
        cur_profile = self._profile(current_text)
        drift_score, drift_axes = self._compute_drift(ref_profile, cur_profile)

        if drift_score < self.drift_threshold:
            return None

        return StyleSignalRecord(
            chapter_no=slice_payload.get("chapter_no"),
            reference_profile_ref=slice_payload.get("reference_profile_ref"),
            drift_score=round(drift_score, 4),
            dominant_drift_axes=drift_axes,
            recommended_action="review",
        )

    def evaluate_pairwise(
        self,
        *,
        baseline_text: str,
        candidate_text: str,
        chapter_no: int,
        reference_profile_ref: str | None = None,
    ) -> StyleSignalRecord | None:
        if not baseline_text or not candidate_text:
            return None
        ref_profile = self._profile(baseline_text)
        cur_profile = self._profile(candidate_text)
        drift_score, drift_axes = self._compute_drift(ref_profile, cur_profile)
        if drift_score < self.drift_threshold:
            return None
        return StyleSignalRecord(
            chapter_no=chapter_no,
            reference_profile_ref=reference_profile_ref,
            drift_score=round(drift_score, 4),
            dominant_drift_axes=drift_axes,
            recommended_action="review",
        )

    def _profile(self, text: str) -> dict:
        stats = paragraph_stats(text)
        repetition = list_sentence_start_repetition(text)
        sentence_lengths = self._sentence_lengths(text)
        dialogue_density = self._dialogue_density(text)
        metaphor_density = self._metaphor_density(text)
        discourse_particle_presence = self._discourse_particle_presence(text)
        return {
            "avg_paragraph_chars": stats["avg_paragraph_chars"],
            "uniformity": stats["uniformity"],
            "vague_density": vague_word_density(text),
            "surprise_density": surprise_word_density(text),
            "repetition_detected": repetition["detected"],
            "char_count": count_chinese_chars(text),
            "sentence_lengths": sentence_lengths,
            "dialogue_density": dialogue_density,
            "metaphor_density": metaphor_density,
            "discourse_particle_presence": discourse_particle_presence,
        }

    def _compute_drift(self, ref: dict, cur: dict) -> tuple[float, list[str]]:
        axes: list[str] = []
        diffs: list[float] = []

        for key in ("avg_paragraph_chars", "uniformity"):
            ref_val = ref.get(key, 0.0)
            cur_val = cur.get(key, 0.0)
            diff = abs(cur_val - ref_val) / max(abs(ref_val), 0.01)
            diffs.append(diff)
            if diff > 0.3:
                axes.append(key)

        vague_diff = abs(cur["vague_density"] - ref["vague_density"])
        diffs.append(vague_diff / max(ref["vague_density"], 0.01))
        if vague_diff > self.vague_tolerance:
            axes.append("vague_density")

        surprise_diff = abs(cur["surprise_density"] - ref["surprise_density"])
        diffs.append(surprise_diff / max(ref["surprise_density"], 0.01))
        if surprise_diff > self.surprise_tolerance:
            axes.append("surprise_density")

        sentence_uniformity_diff = abs(
            self._sentence_length_uniformity(cur["sentence_lengths"]) - self._sentence_length_uniformity(ref["sentence_lengths"])
        )
        diffs.append(sentence_uniformity_diff)
        if self._sentence_length_uniformity(cur["sentence_lengths"]):
            axes.append("sentence_length_uniformity")

        dialogue_density_diff = abs(cur["dialogue_density"] - ref["dialogue_density"])
        diffs.append(dialogue_density_diff)
        if dialogue_density_diff > 0.2:
            axes.append("dialogue_density")

        metaphor_density_diff = abs(cur["metaphor_density"] - ref["metaphor_density"])
        diffs.append(metaphor_density_diff)
        if cur["metaphor_density"] < 0.5:
            axes.append("metaphor_density")

        particle_diff = abs(cur["discourse_particle_presence"] - ref["discourse_particle_presence"])
        diffs.append(particle_diff)
        if cur["discourse_particle_presence"] < 0.34:
            axes.append("discourse_particle_presence")

        avg_drift = sum(diffs) / len(diffs) if diffs else 0.0
        deduped_axes = list(dict.fromkeys(axes))
        return avg_drift, deduped_axes

    @staticmethod
    def _sentence_lengths(text: str) -> list[int]:
        sentences = [item.strip() for item in re.split(r"[。！？；!?;]+", text) if item.strip()]
        return [count_chinese_chars(sentence) for sentence in sentences]

    @staticmethod
    def _sentence_length_uniformity(lengths: list[int]) -> float:
        if len(lengths) < 5:
            return 0.0
        for start in range(0, len(lengths) - 4):
            window = lengths[start : start + 5]
            if max(window) - min(window) < 3:
                return 1.0
        return 0.0

    @staticmethod
    def _dialogue_density(text: str) -> float:
        paragraphs = [item.strip() for item in text.split("\n\n") if item.strip()]
        if not paragraphs:
            return 0.0
        dialogue_paragraphs = sum(1 for paragraph in paragraphs if "“" in paragraph or "\"" in paragraph)
        return round(dialogue_paragraphs / len(paragraphs), 4)

    @staticmethod
    def _metaphor_density(text: str) -> float:
        patterns = ("像", "仿佛", "如同", "宛如")
        char_count = max(count_chinese_chars(text), 1)
        hits = sum(text.count(pattern) for pattern in patterns)
        return round(hits / (char_count / 100.0), 4)

    @staticmethod
    def _discourse_particle_presence(text: str) -> float:
        paragraphs = [item.strip() for item in text.split("\n\n") if item.strip()]
        if not paragraphs:
            return 0.0
        patterns = ("啊", "呢", "吧", "嘛")
        particle_paragraphs = sum(1 for paragraph in paragraphs if any(pattern in paragraph for pattern in patterns))
        return round(particle_paragraphs / len(paragraphs), 4)
