"""Opt-in document input for ``fairpost check``: DOCX, HWPX and text-layer PDF.

The default text path never reaches this module. A file with one of the
supported extensions is converted to plain text by kordoc (an npm CLI), and the
converted text then goes through the same engine call as a ``.txt`` file.

Isolation rules for the kordoc child process:
- ``node`` runs kordoc's ``dist/cli.js`` directly. A ``.cmd``/``.ps1`` shim is
  never executed and no shell is involved.
- ``KORDOC_OFFLINE=1`` forbids model downloads and ``KORDOC_MODEL_CACHE`` points
  at a fresh empty directory, so a cached OCR model cannot be picked up.
- No OCR option is passed, and the watch/setup/models subcommands are never used.
- Standard input is closed, there is a timeout, and no console window opens on
  Windows.

The experiment behind these choices is summarized in docs/document-input.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import zipfile


KORDOC_CLI_ENV = "FAIRPOST_KORDOC_CLI"
INSTALL_HINT = "npm install -g kordoc"
DEFAULT_TIMEOUT_SECONDS = 120.0

CONVERTIBLE_EXTENSIONS = {".docx": "docx", ".hwpx": "hwpx", ".pdf": "pdf"}
ACCEPTED_SOURCE_FORMATS = frozenset(CONVERTIBLE_EXTENSIONS.values())
IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
)

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MAX_ERROR_CHARS = 300


class DocumentInputError(Exception):
    """A document could not be converted. The message is Korean and user-facing."""


@dataclass(frozen=True)
class KordocRuntime:
    node: str
    cli_js: Path
    version: str


# --- extension classification ---------------------------------------------------


def classify_input(path: str) -> str | None:
    """Return ``"convert"``, ``"hwp"``, ``"doc"``, ``"image"``, or None for any other file."""

    suffix = Path(path).suffix.lower()
    if suffix in CONVERTIBLE_EXTENSIONS:
        return "convert"
    if suffix == ".hwp":
        return "hwp"
    if suffix == ".doc":
        return "doc"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    return None


def unsupported_message(kind: str) -> str:
    if kind == "hwp":
        return (
            "바이너리 HWP(.hwp) 파일은 아직 지원하지 않습니다. "
            "한글에서 HWPX 또는 PDF로 저장한 뒤 다시 실행하세요."
        )
    if kind == "doc":
        return (
            "구형 Word(.doc) 파일은 지원하지 않습니다. "
            "Word에서 DOCX 또는 PDF로 저장한 뒤 다시 실행하세요."
        )
    return (
        "그림 파일은 지원하지 않습니다. 이미지는 글자를 읽지 않으므로 점검할 수 없습니다. "
        "글자가 들어 있는 DOCX, HWPX, PDF나 텍스트 파일을 사용하세요."
    )


# --- kordoc JSON flattening -----------------------------------------------------

_MARKER = re.compile(r"^\s*(?:[-·○●■□▶▷◆◇※*ㆍ]|\(?\d+[.)]|[가-힣][.)]|[①-⑳])\s*")
_SENTENCE_END = re.compile(r"[.!?:;)」』]$|(?:다|요|음|함|임|됨)$")
_NUMBERED_HEADING = re.compile(r"[0-9가-힣]+[.)]\s*[^:]{1,14}")

_TEXT_BLOCKS = frozenset({"paragraph", "heading", "list"})
_CONTENTLESS_BLOCKS = frozenset({"image", "separator"})


def _is_heading_cell(text: str) -> bool:
    text = text.strip()
    short_label = (
        0 < len(text) <= 16
        and ":" not in text
        and not _SENTENCE_END.search(text.rstrip(")"))
    )
    return short_label or bool(_NUMBERED_HEADING.fullmatch(text))


def _smart_join(segments: list[str], source: str) -> list[str]:
    """Rebuild the lines of one table cell.

    In DOCX and HWPX a line break inside a cell is a real paragraph break. In a
    PDF it is every visual line, wraps included, so pieces are glued back
    together unless the previous piece ends a sentence or the next one starts a
    list item.
    """

    pieces = [piece.strip() for piece in segments if piece.strip()]
    if source != "pdf":
        return pieces
    out: list[str] = []
    for piece in pieces:
        if out and not _MARKER.match(piece) and not _SENTENCE_END.search(out[-1]):
            out[-1] = out[-1] + " " + piece
        else:
            out.append(piece)
    return out


def _row_lines(cells: list[str], source: str) -> list[str]:
    cells = [cell.strip() for cell in cells]
    if len(cells) == 2 and "\n" in cells[1] and _is_heading_cell(cells[0]):
        return [cells[0]] + _smart_join(cells[1].split("\n"), source)
    parts = [" ".join(x.strip() for x in cell.split("\n") if x.strip()) for cell in cells]
    parts = [part for part in parts if part]
    if len(parts) == 2:
        return [f"{parts[0]}: {parts[1]}"]
    return [" ".join(parts)]


def _fail_shape(what: str) -> DocumentInputError:
    return DocumentInputError(f"kordoc 변환 결과의 구조를 해석할 수 없습니다: {what}")


@dataclass
class _FlattenStats:
    skipped_images: int = 0


def _table_lines(block: dict[str, Any], source: str) -> list[str]:
    table = block.get("table")
    if not isinstance(table, dict) or not isinstance(table.get("cells"), list):
        raise _fail_shape("표 블록에 cells가 없습니다")
    lines: list[str] = []
    for row in table["cells"]:
        if not isinstance(row, list):
            raise _fail_shape("표 행이 목록이 아닙니다")
        texts: list[str] = []
        for cell in row:
            if not isinstance(cell, dict) or not isinstance(cell.get("text"), str):
                raise _fail_shape("표 칸에 text가 없습니다")
            texts.append(cell["text"])
        lines.extend(_row_lines(texts, source))
    caption = table.get("caption")
    if isinstance(caption, str) and caption.strip():
        if table.get("captionBelow") is True:
            lines.append(caption)
        else:
            lines.insert(0, caption)
    return lines


def _footnote_lines(block: dict[str, Any], source: str, stats: _FlattenStats) -> list[str]:
    note = block.get("footnoteText")
    if isinstance(note, str) and note.strip():
        return [f"(주: {note})"]
    nested = block.get("footnoteBlocks")
    if isinstance(nested, list) and nested:
        inner: list[str] = []
        _walk(nested, source, inner, stats)
        return [f"(주: {line})" for line in inner if line.strip()]
    return []


def _walk(blocks: Any, source: str, lines: list[str], stats: _FlattenStats) -> None:
    if not isinstance(blocks, list):
        raise _fail_shape("blocks가 목록이 아닙니다")
    for block in blocks:
        if not isinstance(block, dict):
            raise _fail_shape("블록이 객체가 아닙니다")
        kind = block.get("type")
        if kind in _TEXT_BLOCKS:
            if "text" in block:
                if not isinstance(block["text"], str):
                    raise _fail_shape(f"{kind} 블록의 text가 문자열이 아닙니다")
                lines.append(block["text"])
            lines.extend(_footnote_lines(block, source, stats))
        elif kind == "table":
            lines.extend(_table_lines(block, source))
            lines.extend(_footnote_lines(block, source, stats))
        elif kind == "image":
            # Only a file name is recorded; the picture itself is never read.
            stats.skipped_images += 1
        elif kind == "separator":
            pass
        else:
            raise DocumentInputError(
                f"kordoc가 알 수 없는 블록 종류({kind!r})를 반환했습니다. "
                "내용이 빠지지 않도록 변환을 중단합니다."
            )
        children = block.get("children")
        if children:
            _walk(children, source, lines, stats)


def _flatten(data: Any) -> tuple[str, _FlattenStats]:
    if not isinstance(data, dict):
        raise _fail_shape("최상위 값이 객체가 아닙니다")
    source = data.get("fileType")
    if not isinstance(source, str):
        raise _fail_shape("fileType이 없습니다")
    stats = _FlattenStats()
    lines: list[str] = []
    _walk(data.get("blocks", []), source, lines, stats)
    return "\n".join(lines) + "\n", stats


def flatten_kordoc_json(data: Any) -> str:
    """Turn the ``--format json`` output of kordoc into plain text.

    Paragraphs, headings and list items become one line each, nested list items
    follow their parent, and footnotes follow their block as ``(주: ...)``. A
    table row with a short heading cell and a multi-line body cell becomes the
    heading line plus the body lines. Any other two-column row becomes ``a: b``,
    and rows with three or more columns are joined with single spaces. A block
    type this function does not know raises DocumentInputError so content is
    never dropped silently. The result is deterministic.
    """

    return _flatten(data)[0]


# --- locating kordoc ------------------------------------------------------------


def _version_of(cli_js: Path) -> str:
    package_json = cli_js.parent.parent / "package.json"
    try:
        package = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DocumentInputError(
            "kordoc 설치 정보(package.json)를 읽을 수 없습니다. "
            f"`{INSTALL_HINT}` 명령으로 다시 설치하거나 {KORDOC_CLI_ENV}를 확인하세요."
        ) from exc
    version = package.get("version") if isinstance(package, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise DocumentInputError("kordoc 버전을 확인할 수 없습니다(package.json의 version).")
    return version.strip()


def _cli_from_shim(shim: str, *, windows: bool) -> Path | None:
    """Find kordoc's dist/cli.js from the ``kordoc`` launcher that PATH resolves to.

    On Windows npm installs a launcher next to ``node_modules``; its directory
    holds ``node_modules/kordoc/dist/cli.js``. On POSIX the launcher is a symlink
    to that file. Nothing is executed.
    """

    if windows:
        candidate = Path(os.path.abspath(shim)).parent / "node_modules" / "kordoc" / "dist" / "cli.js"
    else:
        candidate = Path(os.path.realpath(shim))
    if candidate.name == "cli.js" and candidate.is_file():
        return candidate
    return None


def locate_kordoc() -> KordocRuntime:
    node = shutil.which("node")
    configured = os.environ.get(KORDOC_CLI_ENV, "").strip()
    cli_js: Path | None
    if configured:
        cli_js = Path(configured)
        if not cli_js.is_file():
            raise DocumentInputError(
                f"{KORDOC_CLI_ENV}가 가리키는 파일을 찾을 수 없습니다. "
                "kordoc의 dist/cli.js 경로를 지정하세요."
            )
    else:
        shim = shutil.which("kordoc")
        cli_js = _cli_from_shim(shim, windows=sys.platform == "win32") if shim else None
        if cli_js is None:
            raise DocumentInputError(
                "kordoc을 찾을 수 없습니다. DOCX·HWPX·PDF 변환에는 kordoc이 필요합니다. "
                f"`{INSTALL_HINT}` 명령으로 설치하거나 {KORDOC_CLI_ENV}에 kordoc의 "
                "dist/cli.js 경로를 지정하세요."
            )
    if node is None:
        raise DocumentInputError(
            "Node.js(node)를 찾을 수 없습니다. kordoc은 Node.js 20 이상이 필요합니다. "
            f"Node.js를 설치한 뒤 `{INSTALL_HINT}` 명령을 실행하세요."
        )
    return KordocRuntime(node=node, cli_js=cli_js.resolve(), version=_version_of(cli_js))


# --- converting -----------------------------------------------------------------


def _refuse_unsafe_container(path: Path) -> None:
    """Refuse inputs that would make kordoc start Hancom through PowerShell.

    kordoc falls back to Hancom Office COM automation, via PowerShell on
    Windows, for a password-protected HWPX or a distribution-locked HWP. That
    path is not part of the checked conversion, so it is refused up front.
    """

    try:
        with path.open("rb") as handle:
            head = handle.read(len(_OLE2_MAGIC))
    except OSError:
        return
    if head == _OLE2_MAGIC:
        raise DocumentInputError(
            "내용이 바이너리 문서(HWP, DOC)이거나 암호가 걸린 Office 파일입니다. "
            "지원하지 않습니다. 한글이나 Word에서 HWPX, DOCX 또는 PDF로 저장한 뒤 다시 실행하세요."
        )
    if not head.startswith(b"PK"):
        return
    try:
        with zipfile.ZipFile(path) as archive:
            if "META-INF/manifest.xml" not in archive.namelist():
                return
            manifest = archive.read("META-INF/manifest.xml")
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError):
        return
    if b"encryption-data" in manifest:
        raise DocumentInputError(
            "암호화된(배포용) HWPX 파일은 지원하지 않습니다. "
            "암호를 풀고 저장한 파일이나 PDF를 사용하세요."
        )


def _one_line(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > _MAX_ERROR_CHARS:
        collapsed = collapsed[:_MAX_ERROR_CHARS]
    return collapsed


def _kordoc_error_line(stdout: bytes, stderr: bytes) -> str:
    """Pick kordoc's own one-line error: the JSON it prints on failure, else stderr."""

    text = stdout.decode("utf-8", errors="replace").strip()
    if text:
        try:
            failure = json.loads(text)
        except ValueError:
            failure = None
        if isinstance(failure, dict) and isinstance(failure.get("error"), str):
            return _one_line(failure["error"])
    lines = [
        line.strip().lstrip("→").strip()
        for line in stderr.decode("utf-8", errors="replace").splitlines()
    ]
    lines = [line for line in lines if line and line != "FAIL"]
    return _one_line(lines[-1]) if lines else ""


def _image_pdf_error() -> DocumentInputError:
    return DocumentInputError(
        "스캔본·이미지 PDF는 지원하지 않습니다. 글자층이 없어 내용을 읽을 수 없습니다. "
        "글자를 선택할 수 있는 PDF나 DOCX, HWPX를 사용하세요."
    )


def _ocr_needed_pages(data: dict[str, Any]) -> list[int]:
    pages: set[int] = set()
    warnings = data.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if (
                isinstance(warning, dict)
                and warning.get("code") == "NEEDS_OCR"
                and isinstance(warning.get("page"), int)
            ):
                pages.add(warning["page"])
    return sorted(pages)


def convert_document(
    path: str | os.PathLike[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str, dict[str, Any]]:
    """Convert a DOCX, HWPX or text-layer PDF file to plain text with kordoc.

    Returns ``(text, info)``. ``info`` holds ``source_format``, ``converter``
    (``kordoc@<version>``), ``text_sha256``, ``skipped_images`` (picture blocks
    whose text is not read) and ``ocr_needed_pages`` (PDF pages without a text
    layer). Raises FileNotFoundError for a missing file and DocumentInputError,
    with a Korean message, for every other failure.
    """

    source = Path(path).resolve(strict=True)
    if not source.is_file():
        raise DocumentInputError("파일이 아니라 폴더입니다.")
    if source.suffix.lower() not in CONVERTIBLE_EXTENSIONS:
        raise DocumentInputError("지원하는 문서 형식은 DOCX, HWPX, PDF입니다.")
    _refuse_unsafe_container(source)
    runtime = locate_kordoc()

    with tempfile.TemporaryDirectory(prefix="fairpost-kordoc-", ignore_cleanup_errors=True) as work:
        work_dir = Path(work)
        cache_dir = work_dir / "model-cache"
        cache_dir.mkdir()
        out_path = work_dir / "converted.json"
        command = [
            runtime.node,
            str(runtime.cli_js),
            str(source),
            "--format",
            "json",
            "--no-images",
            "--silent",
            "-o",
            str(out_path),
        ]
        environment = dict(os.environ)
        environment["KORDOC_OFFLINE"] = "1"
        environment["KORDOC_MODEL_CACHE"] = str(cache_dir)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=environment,
                timeout=timeout,
                creationflags=creationflags,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentInputError(
                f"kordoc 변환이 {timeout:g}초 안에 끝나지 않아 중단했습니다. "
                "파일이 크거나 손상되지 않았는지 확인하세요."
            ) from exc
        except OSError as exc:
            raise DocumentInputError(
                f"Node.js로 kordoc을 실행할 수 없습니다({type(exc).__name__}). "
                "Node.js 설치를 확인하세요."
            ) from exc
        if completed.returncode != 0:
            detail = _kordoc_error_line(completed.stdout, completed.stderr)
            raise DocumentInputError(
                f"kordoc 변환에 실패했습니다(종료 코드 {completed.returncode})"
                + (f": {detail}" if detail else ".")
            )
        try:
            raw = out_path.read_bytes()
        except OSError as exc:
            raise DocumentInputError(
                "kordoc이 변환 결과 파일을 만들지 않았습니다. "
                "빈 문서이거나 지원하지 않는 내용일 수 있습니다."
            ) from exc

    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError as exc:
        raise DocumentInputError("kordoc 변환 결과를 해석할 수 없습니다(JSON 형식 오류).") from exc
    if not isinstance(data, dict):
        raise _fail_shape("최상위 값이 객체가 아닙니다")
    if data.get("success") is not True:
        error = data.get("error")
        raise DocumentInputError(
            "kordoc 변환에 실패했습니다" + (f": {_one_line(error)}" if isinstance(error, str) else ".")
        )
    file_type = data.get("fileType")
    if data.get("isImageBased") is True:
        raise _image_pdf_error()
    if file_type not in ACCEPTED_SOURCE_FORMATS:
        raise DocumentInputError(
            f"파일 내용이 DOCX, HWPX, PDF가 아닙니다(kordoc 판별: {file_type}). "
            "확장자와 실제 형식이 다르거나 지원하지 않는 형식입니다."
        )

    text, stats = _flatten(data)
    if not text.strip():
        raise DocumentInputError(
            "변환한 글이 비어 있습니다. 글자가 없는 문서이거나 그림으로만 이루어진 문서일 수 있습니다."
        )
    info: dict[str, Any] = {
        "source_format": file_type,
        "converter": f"kordoc@{runtime.version}",
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "skipped_images": stats.skipped_images,
        "ocr_needed_pages": _ocr_needed_pages(data),
    }
    return text, info
