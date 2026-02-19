import json
from pathlib import Path
from datetime import datetime, timezone

RAW_DIR = Path("data/raw")
OUT_PATH = Path("data/ug_top.json")

def load_rows(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))

def main() -> None:
    files = sorted(RAW_DIR.glob("*.json"))
    if not files:
        raise SystemExit(f"No JSON files found in {RAW_DIR.resolve()}")

    all_rows: list[dict] = []
    for fp in files:
        rows = load_rows(fp)
        if not isinstance(rows, list):
            raise SystemExit(f"{fp.name} is not a JSON array")
        all_rows.extend(rows)

    # Dedupe by URL (best unique key). If duplicates exist, keep the one with higher hits.
    by_url: dict[str, dict] = {}
    for r in all_rows:
        url = r.get("url")
        if not url:
            continue

        current = by_url.get(url)
        if current is None:
            by_url[url] = r
            continue

        if (r.get("hits") or 0) > (current.get("hits") or 0):
            by_url[url] = r

    rows = list(by_url.values())

    # Optional: sort for stability
    rows.sort(key=lambda r: (r.get("type") or "", -(r.get("hits") or 0), r.get("artist") or "", r.get("song") or ""))

    payload = {
        "meta": {
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "source_files": [f.name for f in files],
            "row_count": len(rows),
        },
        "rows": rows,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH} with {len(rows)} rows (from {len(files)} files).")

if __name__ == "__main__":
    main()
