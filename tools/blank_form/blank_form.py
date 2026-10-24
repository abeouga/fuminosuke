"""読み取り済みの帳票の写真から手書きだけを消して、書き込み試験用の画像を作る。

試験用の独立した道具。製品のコード（backend/app・frontend）からは読み込まれず、
このファイルも製品のコードを読み込まない。フォルダ tools/blank_form ごと消せば跡は残らない。
DBは読み取り専用で開き、データ・ファイルは書き換えない。出力は --out のフォルダだけ。

方法：同じテンプレートの読み取り済みの位置合わせ済み画像（alignedSource）と
テンプレートの元画像を重ね、「ほとんどの画像にある濃い部分」を印刷とみなす（テンプレートの元画像でも確かめる）。
各画像で印刷以外の濃い部分（手書き）を周りの紙の色で埋める。

使い方（backend の仮想環境の python で実行）:
  backend/.venv/Scripts/python.exe tools/blank_form/blank_form.py --template-name 繁殖台帳 --out <出力先>
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def load_images(data_dir: Path, template_name: str) -> tuple[list[tuple[str, np.ndarray]], list[np.ndarray]]:
    """(書類, 位置合わせ済み画像) の一覧と、テンプレートの元画像の一覧。"""
    db = sqlite3.connect(f"file:{data_dir / 'app.db'}?mode=ro", uri=True)
    versions = db.execute(
        "select v.id, v.clean_template_artifact_id from template_versions v "
        "join templates t on t.id = v.template_id where t.name like ?",
        (f"%{template_name}%",),
    ).fetchall()
    if not versions:
        sys.exit(f"テンプレート「{template_name}」が見つかりません")
    marks = ",".join("?" * len(versions))
    rows = db.execute(
        "select a.job_id, a.relative_path, a.sha256, a.attempt_number from job_artifacts a "
        "join processing_jobs j on j.id = a.job_id "
        f"where a.artifact_name = 'alignedSource' and j.template_version_id in ({marks}) "
        "order by a.job_id, a.attempt_number",
        [v[0] for v in versions],
    ).fetchall()
    templates = db.execute(
        f"select distinct relative_path, sha256 from job_artifacts where id in ({marks})",
        [v[1] for v in versions],
    ).fetchall()
    latest: dict[str, tuple[str, str]] = {}
    for job_id, path, sha, _ in rows:
        latest[job_id] = (path, sha)
    documents: list[tuple[str, np.ndarray]] = []
    seen: set[str] = set()
    for job_id, (path, sha) in latest.items():
        # 同じ画像を何度も読み取った書類は1枚として扱う（印刷の推定が偏らないように）
        digest = sha or hashlib.sha256((data_dir / path).read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        documents.append((job_id, cv2.imread(str(data_dir / path))))
    size = documents[0][1].shape[1::-1] if documents else None
    sources: list[np.ndarray] = []
    for path, sha in templates:
        # 複数の版が同じ元画像を使う
        if sha in seen:
            continue
        seen.add(sha)
        image = cv2.imread(str(data_dir / path))
        if image is not None and size and image.shape[1::-1] == size:
            sources.append(image)
    return documents, sources


def paper(gray: np.ndarray) -> np.ndarray:
    """文字を除いた紙の明るさ（照明のむら込み）。"""
    closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))
    return cv2.GaussianBlur(closed, (0, 0), 8)


def ink(image: np.ndarray, ratio: float) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return (gray < paper(gray).astype(np.float32) * ratio).astype(np.uint8) * 255


def votes(images: list[np.ndarray], slack: int) -> np.ndarray:
    """各画素が（位置のずれ slack px 以内で）濃い画像の割合。"""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * slack + 1, 2 * slack + 1))
    count = np.zeros(images[0].shape[:2], np.float32)
    for image in images:
        count += cv2.dilate(ink(image, 0.82), kernel) > 0
    return count / len(images)


def printed(documents: list[np.ndarray], sources: list[np.ndarray], slack: int, share: float) -> np.ndarray:
    """多くの書類（share 以上の割合）にあり、テンプレートのどの元画像にもある濃い部分。
    どの書類にも同じ場所に書く欄（統一耳標など）を印刷と取り違えないよう、元画像でも確かめる。
    元画像に残った消し跡は薄いので ink の閾値では拾わない。"""
    common = votes(documents, slack) >= share
    if sources:
        common &= votes(sources, slack) >= 1
    return common.astype(np.uint8) * 255


def erase(image: np.ndarray, printed_mask: np.ndarray, core: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    strokes = ink(image, 0.92)
    handwriting = cv2.bitwise_and(strokes, cv2.bitwise_not(printed_mask))
    # 小さな点（紙の汚れ・ノイズ）は残す
    count, labels, stats, _ = cv2.connectedComponentsWithStats(handwriting, 8)
    keep = np.zeros_like(handwriting)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= 6:
            keep[labels == label] = 255
    keep = cv2.dilate(keep, np.ones((5, 5), np.uint8))
    # 印刷の上には塗らない（線・文字が欠けないように）
    keep = cv2.bitwise_and(keep, cv2.bitwise_not(cv2.erode(printed_mask, np.ones((3, 3), np.uint8))))
    # 周りの紙の色で埋める
    filled = cv2.inpaint(image, keep, 7, cv2.INPAINT_TELEA)
    # 埋めた跡・閾値に届かない薄い消し跡が残るので、印刷から離れた所は
    # なめらかな紙の色（照明のむらは残す）に置き換え、境目はぼかしてつなぐ
    smooth = cv2.GaussianBlur(cv2.medianBlur(filled, 31), (0, 0), 6)
    near_print = cv2.dilate(core, np.ones((3, 3), np.uint8))
    weight = cv2.GaussianBlur(near_print.astype(np.float32) / 255.0, (0, 0), 1.5)[..., None]
    result = filled.astype(np.float32) * weight + smooth.astype(np.float32) * (1 - weight)
    return np.clip(result, 0, 255).astype(np.uint8), keep


# どの書類にも同じ所に書かれる印（♀の丸・毛色の丸など）は、重ね合わせでは印刷と区別できない。
# テンプレートごとに、同じ印刷の空いている所から上書きする（座標は位置合わせ済み画像の画素）。
# copy: (x, y, 幅, 高さ, 移す先のx, 移す先のy) 同じ画像の中で写す
# clean: (x, y, 幅, 高さ) その範囲がいちばん空いている書類から、紙の明るさをそろえて写す
PRESETS: dict[str, dict[str, list[tuple[int, ...]]]] = {
    "繁殖台帳": {
        "copy": [
            # 繁殖関係の分娩状況（♂双早♀流死）1〜9行目を10行目から
            *[(352, 1581, 204, 45, 352, 1581 - 50 * k) for k in range(1, 10)],
            # 売却子牛一覧の1行目を8行目から
            (669, 489, 513, 24, 669, 280),
        ],
        "clean": [
            (418, 320, 137, 26),  # 黒 白 黒 褐
            (208, 408, 88, 26),   # 登録No. の 黒原、黒
        ],
    },
}


def apply_preset(result: np.ndarray, others: list[np.ndarray], preset: dict[str, list[tuple[int, ...]]]) -> np.ndarray:
    result = result.copy()
    for x, y, w, h, dx, dy in preset.get("copy", []):
        result[dy:dy + h, dx:dx + w] = result[y:y + h, x:x + w].copy()
    for x, y, w, h in preset.get("clean", []):
        def amount(image: np.ndarray) -> int:
            return int((ink(image, 0.85)[y:y + h, x:x + w] > 0).sum())
        best = min(others, key=amount)
        if amount(best) >= amount(result):
            continue
        # 紙の明るさの比で写す（照明が違っても浮かないように）
        own = np.dstack([paper(result[:, :, c]) for c in range(3)]).astype(np.float32)
        theirs = np.dstack([paper(best[:, :, c]) for c in range(3)]).astype(np.float32)
        patch = best[y:y + h, x:x + w].astype(np.float32) / np.maximum(theirs[y:y + h, x:x + w], 1)
        result[y:y + h, x:x + w] = np.clip(patch * own[y:y + h, x:x + w], 0, 255).astype(np.uint8)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template-name", required=True, help="テンプレート名の一部（例：繁殖台帳）")
    parser.add_argument("--out", required=True, type=Path, help="出力先のフォルダ")
    parser.add_argument("--data", type=Path, default=BACKEND / "data", help="backend/data の場所")
    parser.add_argument("--slack", type=int, default=3, help="位置のずれの許容（px）")
    parser.add_argument("--core-slack", type=int, default=1, help="印刷の文字・線そのものとみなすずれの許容（px）")
    parser.add_argument("--share", type=float, default=0.7, help="印刷とみなす画像の割合")
    parser.add_argument("--debug", action="store_true", help="消した部分の確認画像も出す")
    args = parser.parse_args()

    documents, sources = load_images(args.data, args.template_name)
    if len(documents) + len(sources) < 3:
        sys.exit("印刷の推定に使う画像が少なすぎます（3枚以上必要）")
    images = [image for _, image in documents]
    preset = next((value for key, value in PRESETS.items() if key in args.template_name), {})
    printed_mask = printed(images, sources, args.slack, args.share)
    # 印刷の文字・線そのもの（ずれの許容を狭くした分を少し広げる）。これ以外は紙の色にする
    core = cv2.dilate(printed(images, sources, args.core_slack, args.share), np.ones((3, 3), np.uint8))
    erased = [erase(image, printed_mask, core) for image in images]
    cleaned = [result for result, _ in erased]
    args.out.mkdir(parents=True, exist_ok=True)
    for (job_id, image), (result, mask) in zip(documents, erased):
        result = apply_preset(result, cleaned, preset)
        cv2.imwrite(str(args.out / f"blank_{job_id[:8]}.png"), result)
        if args.debug:
            overlay = image.copy()
            overlay[mask > 0] = (40, 30, 230)
            cv2.imwrite(str(args.out / f"erased_{job_id[:8]}.jpg"), overlay)
        print(f"{job_id[:8]}: 消した画素 {int((mask > 0).sum())}")
    print(f"{len(documents)}枚（テンプレートの元画像 {len(sources)}枚）→ {args.out}")


if __name__ == "__main__":
    main()
