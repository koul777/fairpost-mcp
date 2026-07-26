from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loader import load_ruleset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="검증된 fairpost 사전을 정적 웹용 JavaScript로 내보냅니다."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "data",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "web" / "data.js",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="번들을 수정하지 않고 현재 사전과 일치하는지 확인합니다.",
    )
    args = parser.parse_args()

    ruleset = load_ruleset(args.data_dir)
    payload = {
        "rules": list(ruleset.rules),
        "slots": ruleset.slots,
        "statutes": ruleset.statutes,
        "version": ruleset.version,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    output = f"window.FAIRPOST_DATA={serialized};\n"
    if args.check:
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"{args.output}: 읽을 수 없습니다: {exc}", file=sys.stderr)
            return 1
        if current != output:
            print(
                f"{args.output}: 현재 사전과 다릅니다. "
                "tools/export_web_bundle.py를 실행하십시오.",
                file=sys.stderr,
            )
            return 1
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8", newline="\n")
    print(f"{args.output} ({ruleset.version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
