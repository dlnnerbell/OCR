from pathlib import Path

from config import load_config
from extractor import extract_pdf


EXPECTED = {
    "234645-000039": [0] * 12,
    "234645-000733": [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0],
    "234645-000467": [0] * 12,
    "234645-000093": [0] * 12,
}


def main():
    cfg = load_config()
    pdfs = sorted(Path("../upload").glob("*.pdf"))
    assert len(pdfs) == 4, f"샘플 PDF 4건이 필요합니다: {len(pdfs)}건 발견"
    failed = 0
    for pdf in pdfs:
        result = extract_pdf(pdf, cfg, Path("../tmp/test_diagnostics"))
        expected = EXPECTED[result.applicant_id]
        ok = result.values == expected and result.status == "정상"
        print("PASS" if ok else "FAIL", result.applicant_id, result.values, result.status, result.detail)
        failed += not ok
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

