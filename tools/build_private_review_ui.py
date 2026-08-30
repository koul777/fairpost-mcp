from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import FairpostEngine  # noqa: E402


INPUT_FIELDS = frozenset(
    {
        "review_id",
        "rule_id",
        "layer",
        "dimension",
        "context",
        "matched_text",
        "section",
        "label",
        "allowed_labels",
    }
)
ALLOWED_LABELS = ("true_positive", "false_positive", "uncertain")
ALL_LABELS = (*ALLOWED_LABELS, "unreviewed")
ALLOWED_LAYERS = frozenset({"law", "question"})
REVIEW_ID_RE = re.compile(r"[0-9a-f]{64}\Z")


class ReviewUIError(ValueError):
    """A privacy-safe queue validation or HTML publishing failure."""


def _path_key(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve(strict=False)
    except OSError as exc:
        raise ReviewUIError("input and output paths could not be checked") from exc
    return os.path.normcase(str(resolved)).casefold()


def validate_paths(input_path: Path, output_path: Path) -> None:
    """Reject input/output aliases before the queue is opened."""
    if _path_key(input_path) == _path_key(output_path):
        raise ReviewUIError("--input and --output must be different files")
    try:
        if input_path.exists() and output_path.exists():
            if os.path.samefile(input_path, output_path):
                raise ReviewUIError("--input and --output must be different files")
        if output_path.exists() and output_path.is_dir():
            raise ReviewUIError("--output must be a file path")
    except ReviewUIError:
        raise
    except OSError as exc:
        raise ReviewUIError("input and output paths could not be checked") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReviewUIError("JSON contains a duplicate object field")
        value[key] = item
    return value


def _invalid(line_number: int, detail: str) -> ReviewUIError:
    # Never reflect an input value, identifier, context, hash, or path.
    return ReviewUIError(f"line {line_number}: {detail}")


def _rule_metadata() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for rule in FairpostEngine().ruleset.rules:
        layer = str(rule.get("layer", ""))
        trigger = rule.get("trigger")
        if (
            layer in ALLOWED_LAYERS
            and isinstance(trigger, Mapping)
            and trigger.get("type") == "presence"
        ):
            criterion_field = "message" if layer == "law" else "question"
            criterion = rule.get(criterion_field)
            if not isinstance(criterion, str) or not criterion.strip():
                continue
            result[str(rule["id"])] = {
                "layer": layer,
                "dimension": str(rule["dimension"]),
                "criterion": criterion,
                "guidance": (
                    "정탐은 탐지 표현이 규칙의 검토 기준과 일치한다는 뜻이며, "
                    "법 위반 확정은 아닙니다."
                    if layer == "law"
                    else "정탐은 이 문맥이 사람 검토 질문과 관련 있다는 뜻이며, "
                    "차별 또는 법 위반 확정은 아닙니다."
                ),
            }
    return result


def _validate_row(
    value: object,
    *,
    line_number: int,
    rules: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(line_number, "a JSON object is required")
    if set(value) != INPUT_FIELDS:
        raise _invalid(line_number, "fields do not match the anonymous queue schema")

    review_id = value["review_id"]
    if not isinstance(review_id, str) or REVIEW_ID_RE.fullmatch(review_id) is None:
        raise _invalid(line_number, "review_id is invalid")

    rule_id = value["rule_id"]
    if not isinstance(rule_id, str) or rule_id not in rules:
        raise _invalid(line_number, "rule_id is invalid")
    expected_layer = rules[rule_id]["layer"]
    expected_dimension = rules[rule_id]["dimension"]

    layer = value["layer"]
    if not isinstance(layer, str) or layer != expected_layer:
        raise _invalid(line_number, "layer does not match rule_id")
    dimension = value["dimension"]
    if not isinstance(dimension, str) or dimension != expected_dimension:
        raise _invalid(line_number, "dimension does not match rule_id")

    context = value["context"]
    matched_text = value["matched_text"]
    if not isinstance(context, str) or not context:
        raise _invalid(line_number, "context must be a non-empty string")
    if not isinstance(matched_text, str) or not matched_text:
        raise _invalid(line_number, "matched_text must be a non-empty string")

    section = value["section"]
    if section is not None and not isinstance(section, str):
        raise _invalid(line_number, "section must be a string or null")
    if value["allowed_labels"] != list(ALLOWED_LABELS):
        raise _invalid(line_number, "allowed_labels is invalid")
    label = value["label"]
    if not isinstance(label, str) or label not in ALL_LABELS:
        raise _invalid(line_number, "label is invalid")

    # Copy only validated fields so no dict subclass or unexpected value survives.
    return {field: value[field] for field in INPUT_FIELDS}


def load_review_rows(path: Path) -> list[dict[str, Any]]:
    rules = _rule_metadata()
    rows: list[dict[str, Any]] = []
    seen_review_ids: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line, object_pairs_hook=_unique_object)
                except ReviewUIError as exc:
                    raise _invalid(line_number, str(exc)) from exc
                except json.JSONDecodeError as exc:
                    raise _invalid(line_number, "malformed JSON") from exc
                row = _validate_row(value, line_number=line_number, rules=rules)
                review_id = str(row["review_id"])
                if review_id in seen_review_ids:
                    raise _invalid(line_number, "duplicate review_id")
                seen_review_ids.add(review_id)
                rows.append(row)
    except ReviewUIError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ReviewUIError("--input could not be read") from exc
    return rows


HTML_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'">
  <title>Fairpost 민간 공고 로컬 검토</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; }
    body { margin: 0; background: #f5f7fa; color: #172033; }
    header { position: sticky; top: 0; z-index: 2; padding: 18px 22px; background: #fff; border-bottom: 1px solid #dce2eb; }
    h1 { margin: 0 0 6px; font-size: 20px; }
    .warning { margin: 0 0 14px; color: #8a3b12; font-size: 13px; }
    .toolbar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .progress { min-width: 190px; font-variant-numeric: tabular-nums; font-weight: 650; }
    select, button { font: inherit; }
    select { padding: 8px 10px; border: 1px solid #aab5c5; border-radius: 7px; background: #fff; }
    button { cursor: pointer; border: 1px solid #aab5c5; border-radius: 7px; background: #fff; padding: 8px 10px; }
    button:hover, button:focus-visible { outline: 2px solid #2667d8; outline-offset: 1px; }
    #download { margin-left: auto; background: #172f57; color: #fff; border-color: #172f57; }
    main { max-width: 980px; margin: 22px auto; padding: 0 18px 40px; }
    article { background: #fff; border: 1px solid #dce2eb; border-radius: 10px; padding: 18px; margin-bottom: 14px; box-shadow: 0 1px 2px rgba(20, 34, 56, .05); }
    .meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
    .chip { border-radius: 999px; background: #e9eef8; padding: 4px 9px; font-size: 12px; font-weight: 650; }
    .match { margin: 0 0 9px; color: #1d4d91; font-weight: 700; white-space: pre-wrap; overflow-wrap: anywhere; }
    .criterion { margin: 0 0 7px; padding: 10px 12px; background: #f3f6fb; border-left: 3px solid #6a87b8; line-height: 1.5; }
    .guidance { margin: 0 0 12px; color: #5f3c16; font-size: 13px; line-height: 1.5; }
    .context { margin: 0 0 15px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
    .labels { display: flex; gap: 8px; flex-wrap: wrap; }
    .label-button.selected { color: #fff; background: #2667d8; border-color: #2667d8; }
    .reset-button { margin-left: auto; color: #5d2a12; }
    .reset-button:disabled { cursor: default; color: #8a94a3; background: #f3f5f8; }
    .reset-button:disabled:hover { outline: none; }
    .empty { padding: 40px 0; text-align: center; color: #667085; }
  </style>
</head>
<body>
  <header>
    <h1>민간 공고 공정성 로컬 검토</h1>
    <p class="warning">비식별 자료도 재식별 위험이 있습니다. 이 파일과 다운로드 결과를 외부에 공유하지 마세요.</p>
    <p class="warning">정탐은 규칙 또는 검토 질문과의 관련성을 뜻하며, 채용 차별이나 법 위반의 확정 판정이 아닙니다.</p>
    <div class="toolbar">
      <span id="progress" class="progress" aria-live="polite"></span>
      <label for="rule-filter">규칙 필터</label>
      <select id="rule-filter"></select>
      <button id="download" type="button">검토 결과 JSONL 다운로드</button>
    </div>
  </header>
  <main id="rows"></main>
  <script>
    'use strict';
    const encodedQueue = '__DATA_BASE64__';
    const encodedRuleMetadata = '__RULE_METADATA_BASE64__';
    const bytes = Uint8Array.from(atob(encodedQueue), character => character.charCodeAt(0));
    const rows = JSON.parse(new TextDecoder().decode(bytes));
    const metadataBytes = Uint8Array.from(atob(encodedRuleMetadata), character => character.charCodeAt(0));
    const ruleMetadata = JSON.parse(new TextDecoder().decode(metadataBytes));
    const labels = ['true_positive', 'false_positive', 'uncertain'];
    const labelNames = {
      law: {
        true_positive: '정탐',
        false_positive: '오탐',
        uncertain: '불확실'
      },
      question: {
        true_positive: '질문 적합',
        false_positive: '질문 부적합',
        uncertain: '불확실'
      }
    };
    const filter = document.getElementById('rule-filter');
    const container = document.getElementById('rows');
    const progress = document.getElementById('progress');

    function appendText(parent, tag, className, value) {
      const element = document.createElement(tag);
      element.className = className;
      element.textContent = value;
      parent.appendChild(element);
      return element;
    }

    function updateProgress() {
      const reviewed = rows.filter(row => row.label !== 'unreviewed').length;
      progress.textContent = `진행률 ${reviewed} / ${rows.length}`;
    }

    function render() {
      container.replaceChildren();
      const visible = rows.filter(row => filter.value === 'all' || row.rule_id === filter.value);
      if (visible.length === 0) {
        appendText(container, 'p', 'empty', '표시할 검토 항목이 없습니다.');
      }
      visible.forEach(row => {
        const card = document.createElement('article');
        const meta = document.createElement('div');
        meta.className = 'meta';
        appendText(meta, 'span', 'chip', row.rule_id);
        appendText(meta, 'span', 'chip', row.layer);
        appendText(meta, 'span', 'chip', row.dimension);
        if (row.section) appendText(meta, 'span', 'chip', row.section);
        card.appendChild(meta);
        const metadata = ruleMetadata[row.rule_id];
        appendText(card, 'p', 'criterion', `검토 기준: ${metadata.criterion}`);
        appendText(card, 'p', 'guidance', metadata.guidance);
        appendText(card, 'p', 'match', `탐지 표현: ${row.matched_text}`);
        appendText(card, 'p', 'context', row.context);

        const controls = document.createElement('div');
        controls.className = 'labels';
        labels.forEach(label => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = `label-button${row.label === label ? ' selected' : ''}`;
          button.textContent = labelNames[metadata.layer][label];
          button.setAttribute('aria-pressed', String(row.label === label));
          button.addEventListener('click', () => {
            row.label = label;
            render();
          });
          controls.appendChild(button);
        });
        const resetButton = document.createElement('button');
        resetButton.type = 'button';
        resetButton.className = 'reset-button';
        resetButton.textContent = '미검토로 되돌리기';
        resetButton.disabled = row.label === 'unreviewed';
        resetButton.addEventListener('click', () => {
          row.label = 'unreviewed';
          render();
        });
        controls.appendChild(resetButton);
        card.appendChild(controls);
        container.appendChild(card);
      });
      updateProgress();
    }

    const allOption = document.createElement('option');
    allOption.value = 'all';
    allOption.textContent = '전체 규칙';
    filter.appendChild(allOption);
    [...new Set(rows.map(row => row.rule_id))].sort().forEach(ruleId => {
      const option = document.createElement('option');
      option.value = ruleId;
      option.textContent = ruleId;
      filter.appendChild(option);
    });
    filter.addEventListener('change', render);
    document.getElementById('download').addEventListener('click', () => {
      const jsonl = rows.map(row => JSON.stringify(row)).join('\\n') + (rows.length ? '\\n' : '');
      const url = URL.createObjectURL(new Blob([jsonl], {type: 'application/x-ndjson;charset=utf-8'}));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'private-review-labeled.jsonl';
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    });
    render();
  </script>
</body>
</html>
"""


def render_review_html(rows: Sequence[Mapping[str, Any]]) -> str:
    serialized = json.dumps(
        list(rows), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    encoded = base64.b64encode(serialized).decode("ascii")
    all_metadata = _rule_metadata()
    represented_rule_ids = sorted({str(row["rule_id"]) for row in rows})
    metadata = {rule_id: all_metadata[rule_id] for rule_id in represented_rule_ids}
    serialized_metadata = json.dumps(
        metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    encoded_metadata = base64.b64encode(serialized_metadata).decode("ascii")
    return HTML_TEMPLATE.replace("__DATA_BASE64__", encoded).replace(
        "__RULE_METADATA_BASE64__", encoded_metadata
    )


def _publish_html(path: Path, html: str) -> None:
    staged_path = path.with_name(f".{path.name}.fairpost-temp-{uuid4().hex}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_text(html, encoding="utf-8", newline="\n")
        os.replace(staged_path, path)
    except (OSError, UnicodeError) as exc:
        raise ReviewUIError("--output HTML could not be written") from exc
    finally:
        try:
            staged_path.unlink(missing_ok=True)
        except OSError:
            pass


def build_review_ui(input_path: Path, output_path: Path) -> dict[str, int]:
    input_path = Path(input_path)
    output_path = Path(output_path)
    validate_paths(input_path, output_path)
    rows = load_review_rows(input_path)
    html = render_review_html(rows)
    _publish_html(output_path, html)
    return {"rows": len(rows), "rules": len({str(row["rule_id"]) for row in rows})}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a completely offline, single-file HTML labeler for a "
            "de-identified private review queue. Privacy warning: even "
            "de-identified context can carry re-identification risk. Keep the "
            "HTML and downloaded JSONL in a gitignored local directory such as "
            ".private-review and never share either artifact externally."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = build_review_ui(args.input, args.output)
    except ReviewUIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # Anonymous aggregate only: no path, context, review ID, or content hash.
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
