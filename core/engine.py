from __future__ import annotations

from pathlib import Path
from typing import Any

from .extractor import extract_slots, section_at, split_sections
from .loader import Ruleset, load_ruleset
from .morph import find_matches, is_excluded, normalize
from .schema import Basis, CheckResult, Finding, Question


DISCLAIMER = (
    "이 결과는 점검 참고자료이며 공정성 여부에 대한 판정이나 법률 자문이 아닙니다. "
    "확인되지 않은 항목은 해당 절차가 없다는 뜻이 아니라 이 공고문에서 발견되지 않았다는 뜻입니다."
)


class FairpostEngine:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        local_rules_path: str | Path | None = None,
    ) -> None:
        self.ruleset: Ruleset = load_ruleset(data_dir, local_rules_path)

    def _basis(self, rule: dict[str, Any]) -> Basis:
        payload = rule["basis"]
        if payload["type"] != "statute":
            return Basis(type=payload["type"])
        statute = self.ruleset.statutes[payload["statute_id"]]
        article = statute["articles"][payload["article"]]
        return Basis(
            type="statute",
            law=payload["law"],
            article=payload["article"],
            statute_id=payload["statute_id"],
            snapshot_date=statute["snapshot_date"],
            effective_date=article["effective_date"],
            title=article["title"],
            text=article["text"],
        )

    def check(
        self,
        text: str,
        *,
        saved_answers: dict[str, str] | None = None,
    ) -> CheckResult:
        source = normalize(text)
        sections = split_sections(source)
        slots = extract_slots(source, sections, self.ruleset.slots)
        slots_by_id = {slot.slot: slot for slot in slots}
        answers = saved_answers or {}
        findings: list[Finding] = []
        questions: list[Question] = []

        for rule in self.ruleset.rules:
            trigger = rule["trigger"]
            if trigger["type"] == "absence":
                matched = not slots_by_id[trigger["field"]].found
                match = None
            else:
                candidates = find_matches(source, trigger["patterns"])
                match = next(
                    (
                        candidate
                        for candidate in candidates
                        if not is_excluded(
                            source,
                            candidate.start(),
                            candidate.end(),
                            trigger.get("exclude", []),
                        )
                        and (
                            not trigger.get("section_scope")
                            or section_at(sections, candidate.start())
                            == trigger["section_scope"]
                        )
                    ),
                    None,
                )
                matched = bool(match)

            if not matched:
                continue

            if rule["layer"] == "law":
                assert match is not None
                findings.append(
                    Finding(
                        id=rule["id"],
                        dimension=rule["dimension"],
                        message=rule["message"],
                        matched_text=match.group(0),
                        offset=(match.start(), match.end()),
                        section=section_at(sections, match.start()),
                        severity=rule["severity"],
                        basis=self._basis(rule),
                        alternatives=list(rule.get("alternatives", [])),
                        provenance_method=rule["provenance"]["method"],
                        book_ref=rule["book_ref"],
                    )
                )
            else:
                questions.append(
                    Question(
                        id=rule["id"],
                        dimension=rule["dimension"],
                        question=rule["question"],
                        follow_up=list(rule.get("follow_up", [])),
                        basis_type=rule["basis"]["type"],
                        book_ref=rule["book_ref"],
                        saved_answer=answers.get(rule["id"]),
                    )
                )

        findings.sort(key=lambda item: item.id)
        questions.sort(key=lambda item: item.id)
        statute_snapshot_date = min(
            str(statute["snapshot_date"])
            for statute in self.ruleset.statutes.values()
        )
        return CheckResult(
            findings=findings,
            slots=slots,
            questions=questions,
            counts={
                "findings": len(findings),
                "not_found": sum(not slot.found for slot in slots),
                "questions": len(questions),
            },
            ruleset_version=self.ruleset.version,
            statute_snapshot_date=statute_snapshot_date,
            statute_notice=(
                f"법령 원문은 {statute_snapshot_date} 공식 대조 스냅샷을 기준으로 합니다. "
                "그 이후 개정은 배포본의 법령 감사 기록을 확인해야 합니다."
            ),
            disclaimer=DISCLAIMER,
        )
