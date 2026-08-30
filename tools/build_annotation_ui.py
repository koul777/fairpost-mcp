from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import load_ruleset  # noqa: E402


def _is_holdout_path(path: Path) -> bool:
    return "holdout" in {part.casefold() for part in path.parts}


def _paths_alias(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except OSError:
        pass
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def validate_paths(input_path: Path, manifest_path: Path, output_path: Path) -> None:
    for name, source in (("--input", input_path), ("--manifest", manifest_path)):
        if _paths_alias(source, output_path):
            raise ValueError(f"--output은 {name}과 다른 파일이어야 합니다")
    if output_path.exists() and output_path.is_dir():
        raise ValueError("--output은 파일 경로여야 합니다")


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def load_records(input_path: Path, manifest_path: Path) -> list[dict[str, Any]]:
    if not _is_holdout_path(input_path) or not _is_holdout_path(manifest_path):
        raise ValueError("라벨링 화면은 holdout 경로만 읽을 수 있습니다")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_hashes = manifest.get("content_hashes")
    if not isinstance(manifest_hashes, list):
        raise ValueError(f"{manifest_path}: content_hashes가 필요합니다")
    expected_hashes = {str(value) for value in manifest_hashes}

    records: list[dict[str, Any]] = []
    ids: set[str] = set()
    hashes: set[str] = set()
    for line_number, line in enumerate(
        input_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        record_id = value.get("id")
        text = value.get("text")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"{input_path}:{line_number}: id가 필요합니다")
        if not isinstance(text, str):
            raise ValueError(f"{input_path}:{line_number}: text가 필요합니다")
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        declared_hash = value.get("content_hash")
        if declared_hash is not None and declared_hash != content_hash:
            raise ValueError(
                f"{input_path}:{line_number}: text와 content_hash가 일치하지 않습니다"
            )
        if record_id in ids or content_hash in hashes:
            raise ValueError(f"{input_path}:{line_number}: 중복 공고입니다")
        ids.add(record_id)
        hashes.add(content_hash)
        records.append(
            {
                "id": record_id,
                "content_hash": content_hash,
                "text": text,
                "sector": value.get("sector"),
                "occupation": value.get("occupation"),
                "employment_type": value.get("employment_type"),
            }
        )

    if hashes != expected_hashes:
        missing = len(expected_hashes - hashes)
        extra = len(hashes - expected_hashes)
        raise ValueError(
            f"holdout manifest와 공고가 일치하지 않습니다: 누락 {missing}건, 추가 {extra}건"
        )
    return records


def build_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    ruleset = load_ruleset()
    law_rules = [
        {
            "id": rule["id"],
            "dimension": rule["dimension"],
            "message": rule["message"],
            "law": rule["basis"]["law"],
            "article": rule["basis"]["article"],
        }
        for rule in ruleset.rules
        if rule["layer"] == "law"
    ]
    slots = [
        {"id": slot_id, "label": definition["label"]}
        for slot_id, definition in sorted(ruleset.slots.items())
    ]
    return {
        "records": records,
        "law_rules": law_rules,
        "slots": slots,
        "ruleset_version": ruleset.version,
        "matching_version": ruleset.matching_version,
        "evaluation_phase": "sealed_holdout_final",
        "metric_scope": {
            "g1_g2": ["law_expression_detection", "required_slot_absence"],
            "question_cards": "pilot_only_not_g1_g2",
        },
    }


def build_html(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    encoded = base64.b64encode(serialized).decode("ascii")
    return HTML_TEMPLATE.replace("__PAYLOAD__", encoded)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="봉인 홀드아웃을 네트워크 없는 사람 라벨링 HTML로 변환합니다."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(".corpus-prd/holdout/records.jsonl"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(".corpus-prd/holdout/manifest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".corpus-prd/holdout/labeler.html"),
    )
    args = parser.parse_args()

    if not _is_holdout_path(args.output):
        raise SystemExit("라벨링 HTML은 holdout 경로에만 생성할 수 있습니다")
    try:
        validate_paths(args.input, args.manifest, args.output)
        records = load_records(args.input, args.manifest)
        html = build_html(build_payload(records))
        _atomic_write_text(args.output, html)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    print(f"{len(records)}건 라벨링 화면 생성: {args.output}")
    return 0


HTML_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; connect-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; form-action 'none'">
  <title>fairpost 홀드아웃 라벨링</title>
  <style>
    :root{font-family:"Malgun Gothic",Arial,sans-serif;color:#17211d;background:#eef2ef;letter-spacing:0}
    *{box-sizing:border-box}body{margin:0;min-width:320px}button,input,select{font:inherit;letter-spacing:0}
    button{cursor:pointer;border:1px solid #aebbb4;border-radius:6px;background:#fff;padding:8px 12px;font-weight:700}
    button:disabled{cursor:default;opacity:.45}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #9fc1d4;outline-offset:2px}
    header{min-height:68px;padding:13px 24px;display:flex;align-items:center;justify-content:space-between;gap:20px;background:#fff;border-bottom:1px solid #d5ddd8}
    h1,h2,h3,p{margin:0}h1{font-size:19px}header p,.muted{color:#66716c;font-size:11px}
    .toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.primary{background:#176b4d;color:#fff;border-color:#176b4d}
    main{width:min(1500px,100%);margin:auto;padding:18px 24px;display:grid;grid-template-columns:minmax(360px,1fr) minmax(430px,.9fr);gap:16px}
    .panel{min-width:0;background:#fff;border:1px solid #d5ddd8;border-radius:8px;overflow:hidden}
    .panel-head{padding:15px 17px;border-bottom:1px solid #d5ddd8;display:flex;align-items:center;justify-content:space-between;gap:12px}
    .panel-head h2{font-size:15px}.meta{padding:12px 17px;background:#f6f8f6;color:#59655f;font-size:11px;line-height:1.6;border-bottom:1px solid #d5ddd8}
    pre{margin:0;padding:18px;min-height:650px;max-height:calc(100vh - 190px);overflow:auto;white-space:pre-wrap;word-break:break-word;font:12px/1.75 "Malgun Gothic",monospace}
    .form{max-height:calc(100vh - 105px);overflow:auto;padding:16px}.group{margin-bottom:20px}.group h3{font-size:13px;margin-bottom:5px}.group>p{color:#66716c;font-size:10px;margin-bottom:10px;line-height:1.5}
    .choice{display:grid;grid-template-columns:20px 1fr 58px;gap:8px;align-items:start;padding:9px 8px;border-bottom:1px solid #edf1ee;font-size:11px;line-height:1.45}
    .choice input[type=number]{width:54px;padding:4px;border:1px solid #c7d0cb;border-radius:4px}
    .choice strong{display:block;font-size:10px;color:#176b4d}.slot-choice{grid-template-columns:20px 1fr}
    .uncovered{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px;background:#fff8e9;border-left:3px solid #c28a32;font-size:11px}
    .uncovered input{width:70px;padding:5px}.reviewed{padding:13px;border:1px solid #9eb9aa;background:#edf7f1;border-radius:6px;font-size:12px;font-weight:700}
    .progress{font-size:11px;font-weight:700;color:#435149}.file-label{display:inline-flex;align-items:center;border:1px solid #aebbb4;border-radius:6px;background:#fff;padding:8px 12px;font-size:11px;font-weight:700;cursor:pointer}
    #import-file{position:absolute;width:1px;height:1px;opacity:0}.status{position:fixed;right:18px;bottom:18px;padding:10px 13px;background:#17211d;color:#fff;border-radius:6px;font-size:11px;opacity:0;pointer-events:none}.status.show{opacity:1}
    @media(max-width:900px){main{grid-template-columns:1fr;padding:12px}pre{min-height:400px;max-height:600px}.form{max-height:none}header{align-items:flex-start;flex-direction:column}}
  </style>
</head>
<body>
  <header>
    <div><h1>fairpost 홀드아웃 라벨링</h1><p>로컬 파일 · 네트워크 요청 차단 · 규칙 사전 <span id="version"></span></p></div>
    <div class="toolbar">
      <span class="progress" id="progress"></span>
      <button id="prev" type="button" aria-label="이전 공고">←</button>
      <button id="next" type="button" aria-label="다음 공고">→</button>
      <label class="file-label" for="import-file">라벨 불러오기</label>
      <input id="import-file" type="file" accept=".jsonl,application/json">
      <button id="export" class="primary" type="button">검토 완료분 내보내기</button>
    </div>
  </header>
  <main>
    <section class="panel">
      <div class="panel-head"><h2 id="record-title"></h2><span class="muted" id="review-state"></span></div>
      <div class="meta" id="record-meta"></div>
      <pre id="record-text"></pre>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>사람 검토 라벨</h2><span class="muted">판정 결과가 아닌 평가용 정답</span></div>
      <div class="form">
        <div class="group">
          <h3>법령 관련 표현</h3>
          <p>공고문에 실제로 해당하는 표현만 선택하고, 같은 규칙 표현이 여러 개면 개수를 조정합니다.</p>
          <div id="finding-options"></div>
        </div>
        <div class="group">
          <h3>실제로 확인되지 않은 절차·정보</h3>
          <p>표현이 단순히 다르더라도 내용이 안내되어 있다면 선택하지 않습니다.</p>
          <div id="slot-options"></div>
        </div>
        <div class="group">
          <h3>현재 사전에 없는 문제 표현</h3>
          <div class="uncovered"><span>사람이 확인했지만 연결할 법령 규칙 ID가 없는 표현 수</span><input id="uncovered" type="number" min="0" step="1" value="0"></div>
        </div>
        <label class="reviewed"><input id="reviewed" type="checkbox"> 이 공고의 표현과 11개 슬롯을 모두 검토했습니다</label>
      </div>
    </section>
  </main>
  <div id="status" class="status" role="status" aria-live="polite"></div>
  <script id="payload" type="application/octet-stream">__PAYLOAD__</script>
  <script>
    "use strict";
    const bytes=Uint8Array.from(atob(document.getElementById("payload").textContent.trim()),c=>c.charCodeAt(0));
    const data=JSON.parse(new TextDecoder().decode(bytes));
    const states=Object.fromEntries(data.records.map(r=>[r.id,{reviewed:false,findings:{},slots:[],uncovered:0}]));
    let index=0;
    const $=id=>document.getElementById(id);
    const esc=value=>String(value??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
    function notify(message){$("status").textContent=message;$("status").classList.add("show");setTimeout(()=>$("status").classList.remove("show"),1800)}
    function persistCurrent(){
      const record=data.records[index],state=states[record.id];
      state.findings={};
      document.querySelectorAll("[data-finding]").forEach(box=>{if(box.checked){const count=Number(document.querySelector(`[data-count="${box.dataset.finding}"]`).value)||1;state.findings[box.dataset.finding]=Math.max(1,count)}});
      state.slots=[...document.querySelectorAll("[data-slot]:checked")].map(box=>box.dataset.slot);
      state.uncovered=Math.max(0,Number($("uncovered").value)||0);
      state.reviewed=$("reviewed").checked;
    }
    function render(){
      const record=data.records[index],state=states[record.id];
      $("version").textContent=data.ruleset_version;
      $("record-title").textContent=`${index+1}. ${record.id}`;
      $("record-meta").textContent=[record.sector,record.occupation,record.employment_type,record.content_hash].filter(Boolean).join(" · ");
      $("record-text").textContent=record.text;
      $("finding-options").innerHTML=data.law_rules.map(rule=>{const count=state.findings[rule.id]||1,checked=Boolean(state.findings[rule.id]);return `<label class="choice"><input type="checkbox" data-finding="${esc(rule.id)}" ${checked?"checked":""}><span><strong>${esc(rule.id)} · ${esc(rule.dimension)}</strong>${esc(rule.message)}<br><span class="muted">${esc(rule.law)} ${esc(rule.article)}</span></span><input type="number" min="1" step="1" value="${count}" data-count="${esc(rule.id)}" ${checked?"":"disabled"} aria-label="${esc(rule.id)} 표현 수"></label>`}).join("");
      $("slot-options").innerHTML=data.slots.map(slot=>`<label class="choice slot-choice"><input type="checkbox" data-slot="${esc(slot.id)}" ${state.slots.includes(slot.id)?"checked":""}><span><strong>${esc(slot.id)}</strong>${esc(slot.label)}</span></label>`).join("");
      $("uncovered").value=state.uncovered;$("reviewed").checked=state.reviewed;
      $("review-state").textContent=state.reviewed?"검토 완료":"검토 중";
      const reviewed=Object.values(states).filter(s=>s.reviewed).length;
      $("progress").textContent=`${index+1}/${data.records.length} · 완료 ${reviewed}`;
      $("prev").disabled=index===0;$("next").disabled=index===data.records.length-1;
      document.querySelectorAll("[data-finding]").forEach(box=>box.addEventListener("change",()=>{document.querySelector(`[data-count="${box.dataset.finding}"]`).disabled=!box.checked}));
      window.scrollTo(0,0);
    }
    function move(delta){persistCurrent();index=Math.max(0,Math.min(data.records.length-1,index+delta));render()}
    $("prev").addEventListener("click",()=>move(-1));$("next").addEventListener("click",()=>move(1));
    $("reviewed").addEventListener("change",()=>{$("review-state").textContent=$("reviewed").checked?"검토 완료":"검토 중"});
    $("export").addEventListener("click",()=>{
      persistCurrent();
      const output=data.records.filter(record=>states[record.id].reviewed).map(record=>{
        const state=states[record.id],expressions=[];
        Object.entries(state.findings).sort().forEach(([ruleId,count])=>{for(let i=0;i<count;i+=1)expressions.push({rule_id:ruleId})});
        for(let i=0;i<state.uncovered;i+=1)expressions.push({rule_id:null});
        return {id:record.id,content_hash:record.content_hash,text:record.text,expected_findings:Object.keys(state.findings).sort(),expected_absent_slots:[...state.slots].sort(),expected_expressions:expressions};
      });
      if(!output.length){notify("검토 완료된 공고가 없습니다.");return}
      const blob=new Blob([output.map(item=>JSON.stringify(item)).join("\n")+"\n"],{type:"application/x-ndjson;charset=utf-8"});
      const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download="annotations.jsonl";link.click();URL.revokeObjectURL(link.href);
      notify(`${output.length}건을 내보냈습니다.`);
    });
    $("import-file").addEventListener("change",async event=>{
      const file=event.target.files[0];if(!file)return;
      try{
        const lines=(await file.text()).split(/\r?\n/).filter(line=>line.trim());
        const ruleIds=new Set(data.law_rules.map(rule=>rule.id)),slotIds=new Set(data.slots.map(slot=>slot.id));
        for(const line of lines){const item=JSON.parse(line),record=data.records.find(r=>r.id===item.id);if(!record||record.content_hash!==item.content_hash)throw new Error("현재 홀드아웃과 일치하지 않는 라벨입니다");const findings={};for(const expression of item.expected_expressions||[]){if(expression.rule_id)findings[expression.rule_id]=(findings[expression.rule_id]||0)+1}for(const ruleId of item.expected_findings||[]){if(!findings[ruleId])findings[ruleId]=1}for(const ruleId of Object.keys(findings)){if(!ruleIds.has(ruleId))throw new Error(`현재 사전에 없는 규칙 ID: ${ruleId}`)}for(const slotId of item.expected_absent_slots||[]){if(!slotIds.has(slotId))throw new Error(`현재 사전에 없는 슬롯 ID: ${slotId}`)}states[item.id]={reviewed:true,findings,slots:[...(item.expected_absent_slots||[])],uncovered:(item.expected_expressions||[]).filter(x=>x.rule_id===null).length}}
        render();notify(`${lines.length}건을 불러왔습니다.`);
      }catch(error){notify(error.message||"라벨 파일을 읽지 못했습니다.")}
      event.target.value="";
    });
    render();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
