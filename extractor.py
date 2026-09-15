from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz
from PIL import Image, ImageOps


@dataclass
class ExtractionResult:
    applicant_id: str
    values: list[int]
    status: str
    detail: str
    filename: str
    diagnostic_path: str = ""


def _clusters(values: list[int], gap: int = 2) -> list[int]:
    if not values:
        return []
    groups = [[values[0]]]
    for value in values[1:]:
        if value - groups[-1][-1] <= gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [round(sum(group) / len(group)) for group in groups]


def _longest_dark_run(image: Image.Image, y: int, threshold: int = 185) -> int:
    px = image.load()
    best = current = 0
    for x in range(image.width):
        if px[x, y] < threshold:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _find_attendance_grid(image: Image.Image) -> tuple[list[int], list[int]]:
    """Return 16 x-boundaries and horizontal grid lines for the attendance table."""
    gray = ImageOps.grayscale(image)
    w, h = gray.size
    horizontal_candidates = _clusters([
        y for y in range(int(h * .58), int(h * .93))
        if _longest_dark_run(gray, y) > w * .66
    ])

    px = gray.load()
    best = None
    for top in horizontal_candidates:
        if top > h * .88:
            continue
        # Attendance grids are shallow and have 16 vertical boundaries.
        bottom_limit = min(h - 1, top + int(h * .11))
        vertical_scores = []
        for x in range(w):
            dark = sum(px[x, y] < 185 for y in range(top, bottom_limit))
            if dark > (bottom_limit - top) * .24:
                vertical_scores.append(x)
        xs = _clusters(vertical_scores, gap=3)
        # Ignore narrow false detections and retain plausible full table spans.
        xs = [x for x in xs if w * .03 < x < w * .97]
        if len(xs) < 15:
            continue
        # Find the best consecutive run of 16 boundaries with table-like spacing.
        for start in range(max(1, len(xs) - 15)):
            run = xs[start:start + 16]
            if len(run) < 16:
                continue
            widths = [b - a for a, b in zip(run, run[1:])]
            if run[-1] - run[0] < w * .65 or min(widths) < w * .018:
                continue
            metric = widths[2:14]
            regularity = max(metric) / max(1, min(metric))
            if regularity > 2.6:
                continue
            # Prefer the lowest matching grid: attendance is the final numbered
            # section on these first pages. Earlier academic tables can otherwise
            # see the attendance grid through the look-ahead window.
            score = (run[-1] - run[0]) - regularity * 100 + top * .25
            if best is None or score > best[0]:
                best = (score, run, top)

    if best is None:
        raise ValueError("출결 표의 세로선을 찾지 못했습니다.")

    _, xs, top = best
    left, right = xs[0], xs[-1]
    full_lines = []
    for y in range(int(h * .58), min(h, int(h * .93))):
        best_run = current = 0
        for x in range(left, right + 1):
            if px[x, y] < 185:
                current += 1
                best_run = max(best_run, current)
            else:
                current = 0
        if best_run > (right - left) * .70:
            full_lines.append(y)
    all_ys = _clusters(full_lines, gap=3)
    # Table pattern: a tall two-level header followed by 1-3 shorter year rows.
    # Select the latest valid sequence so unrelated sections above are excluded.
    sequences = []
    for i in range(len(all_ys) - 2):
        header_height = all_ys[i + 1] - all_ys[i]
        first_row_height = all_ys[i + 2] - all_ys[i + 1]
        if h * .032 <= header_height <= h * .065 and h * .013 <= first_row_height <= h * .035:
            seq = all_ys[i:i + 3]
            for y in all_ys[i + 3:]:
                gap = y - seq[-1]
                if h * .013 <= gap <= h * .035 and len(seq) < 5:
                    seq.append(y)
                else:
                    break
            sequences.append(seq)
    if not sequences:
        raise ValueError("출결 표의 가로선을 찾지 못했습니다.")
    ys = sequences[-1]
    return xs, ys


def _tesseract_command() -> str:
    bundled = Path(getattr(__import__('sys'), '_MEIPASS', '')) / "tesseract" / "tesseract.exe"
    if bundled.exists():
        return str(bundled)
    found = shutil.which("tesseract")
    if not found:
        raise RuntimeError("OCR 엔진(tesseract)을 찾지 못했습니다.")
    return found


def _read_cell(cell: Image.Image) -> tuple[int, str]:
    # Enlarge and remove grid-line remnants before recognizing digits and periods.
    cell = ImageOps.grayscale(cell)
    # A blank or the official record's centered period means zero. Detect this
    # geometrically before OCR because Tesseract can mistake a scan speck for 7.
    ink = cell.point(lambda p: 0 if p < 150 else 255).getbbox()
    if ink is None:
        return 0, "."
    bw, bh = ink[2] - ink[0], ink[3] - ink[1]
    if bw < cell.width * .35 and bh < cell.height * .35:
        return 0, "."
    cell = ImageOps.autocontrast(cell.resize((cell.width * 4, cell.height * 4)))
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "cell.png"
        cell.save(path)
        proc = subprocess.run(
            [_tesseract_command(), str(path), "stdout", "--psm", "10", "-l", "eng",
             "-c", "tessedit_char_whitelist=0123456789."],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    raw = proc.stdout.strip().replace(" ", "")
    digits = re.findall(r"\d+", raw)
    if digits:
        return int(digits[0]), raw
    if raw in {"", ".", "..", "..."}:
        return 0, raw or "."
    return 0, raw


def extract_pdf(pdf_path: Path, cfg: dict, diagnostics_dir: Path | None = None) -> ExtractionResult:
    match = re.search(cfg["applicant_id_pattern"], pdf_path.name)
    applicant_id = match.group(0) if match else ""
    if not applicant_id:
        return ExtractionResult("", [0] * 12, "확인필요", "지원자번호 추출 실패", pdf_path.name)

    diagnostic_path = ""
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        scale = cfg["render_dpi"] / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        xs, ys = _find_attendance_grid(image)
        # Full horizontal lines: top, header-bottom, then one line per school-year row.
        # A partially spanning mid-header line is intentionally excluded.
        data_top_idx = 1 if len(ys) >= 3 else 0
        # The first full line is table top, second is bottom of the two-level header.
        data_top = ys[data_top_idx]
        row_lines = [y for y in ys if y >= data_top]
        if len(row_lines) < 2:
            raise ValueError("출결 데이터 행을 찾지 못했습니다.")

        totals = [0] * 12
        ambiguous = []
        for row_no, (y1, y2) in enumerate(zip(row_lines, row_lines[1:]), start=1):
            if y2 - y1 < 12:
                continue
            for idx in range(12):
                x1, x2 = xs[idx + 2], xs[idx + 3]
                margin_x = max(2, int((x2 - x1) * .10))
                margin_y = max(2, int((y2 - y1) * .12))
                crop = image.crop((x1 + margin_x, y1 + margin_y, x2 - margin_x, y2 - margin_y))
                value, raw = _read_cell(crop)
                totals[idx] += value
                if raw not in {"", ".", "..", "..."} and not raw.isdigit():
                    ambiguous.append(f"{row_no}행 {idx + 1}열='{raw}'")

        if diagnostics_dir and cfg.get("save_diagnostic_images"):
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
            diagnostic = image.crop((xs[0], max(0, ys[0] - 20), xs[-1], min(image.height, ys[-1] + 20)))
            out = diagnostics_dir / f"{applicant_id}_출결표.png"
            diagnostic.save(out)
            diagnostic_path = str(out)

        status = "정상"
        detail = ""
        if ambiguous:
            status, detail = "확인필요", "; ".join(ambiguous)
        elif cfg.get("review_on_nonzero") and any(totals):
            status, detail = "확인필요", "0이 아닌 출결값 확인"
        return ExtractionResult(applicant_id, totals, status, detail, pdf_path.name, diagnostic_path)
    except Exception as exc:
        return ExtractionResult(applicant_id, [0] * 12, "확인필요", str(exc), pdf_path.name, diagnostic_path)
