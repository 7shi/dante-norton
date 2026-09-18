"""LLM呼び出しのトークン使用量（llm7shiの`Usage`）をリポジトリ直下のusage.jsonlに記録・集計するモジュール。

hypercomplexのsrc/usage（https://github.com/7shi/hypercomplex）と同じAPIをdante_norton内に移植したもの。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Iterator

from llm7shi.usage import Usage

USAGE_PATH = Path(__file__).resolve().parents[1] / "usage.jsonl"

# usage.jsonlの排他制御: LOCK_RETRY_INTERVAL秒おきに、最大LOCK_TIMEOUT秒まで再試行する
LOCK_RETRY_INTERVAL = 0.5
LOCK_TIMEOUT = 5.0


@contextmanager
def _locked(path: Path, mode: str) -> Iterator[IO[str]]:
    """`path`自体をflockで排他制御しつつ`mode`で開き、開いたファイルオブジェクトを返す。

    別途ロックファイルは作らない。LOCK_RETRY_INTERVAL秒おきにLOCK_TIMEOUT秒まで
    非ブロッキングでロック取得を試み、取得できなければTimeoutErrorを送出する。
    """
    with open(path, mode, encoding="utf-8") as f:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{path}: ロックを{LOCK_TIMEOUT}秒待っても取得できませんでした")
                time.sleep(LOCK_RETRY_INTERVAL)
        try:
            yield f
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def format_usage_line(model: str, usage: Usage) -> str:
    """モデル名とUsageを`model|input:N|output:N|...`形式の1行に整形する（3桁区切り付き）。"""
    parts = [model]
    for key, value in usage.to_dict().items():
        parts.append(f"{key.removesuffix('_tokens')}:{value:,}")
    return "|".join(parts)


def today() -> str:
    """今日の日付をUTCで`YYYY/MM/DD`形式で返す。"""
    return datetime.now(timezone.utc).strftime("%Y/%m/%d")


def parse_usage_file(path: Path = USAGE_PATH) -> dict[str, dict[str, Usage]]:
    """usage.jsonlをパースし、日付をkey、モデル名をkeyとする合計Usageのdictをvalueとするdictを返す。

    日付は各レコードの`timestamp`をUTCに変換して求める。日付・モデル名はファイル内での出現順を保つ。
    ファイルが存在しない場合は空のdictを返す。読み込みはロックで排他制御される。
    """
    if not path.exists():
        return {}

    with _locked(path, "r") as f:
        text = f.read()

    totals: dict[str, dict[str, Usage]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        timestamp = datetime.fromisoformat(record["timestamp"]).astimezone(timezone.utc)
        date = timestamp.strftime("%Y/%m/%d")
        model = record["model"]
        usage = Usage(raw={k: v for k, v in record.items() if k not in ("timestamp", "model")})
        by_model = totals.setdefault(date, {})
        by_model[model] = usage if model not in by_model else by_model[model] + usage
    return totals


def append_usage(usage: Usage, model: str, path: Path = USAGE_PATH, timestamp: datetime | None = None) -> None:
    """Usageをモデル名・タイムゾーン付きの生成日時とともにusage.jsonlに1行追記する。

    timestampを省略した場合は現在時刻（ローカルのタイムゾーン付き）を使う。追記はロックで排他制御される。
    """
    timestamp = timestamp or datetime.now().astimezone()
    record = {"timestamp": timestamp.isoformat(), "model": model, **usage.to_dict()}
    with _locked(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def merge_usage(path: Path = USAGE_PATH) -> tuple[int, int]:
    """usage.jsonlのレコードをUTC日付・モデル単位で1行に統合し、ファイルを書き換える。

    各レコードの`timestamp`をUTCに変換した日付とモデル名が同じもの同士を合算する。
    統合後のtimestampはその日のUTC 00:00とする。日付・モデルはファイル内での出現順を保つ。
    読み込みと書き込みを同一ロックのもとで行う。統合前後のレコード数を返す。
    """
    with _locked(path, "r+") as f:
        lines = [line for line in f.read().splitlines() if line.strip()]

        merged: dict[tuple, Usage] = {}
        order: list[tuple] = []
        for line in lines:
            record = json.loads(line)
            timestamp = datetime.fromisoformat(record["timestamp"]).astimezone(timezone.utc)
            date = timestamp.date()
            model = record["model"]
            usage = Usage(raw={k: v for k, v in record.items() if k not in ("timestamp", "model")})
            key = (date, model)
            if key not in merged:
                merged[key] = usage
                order.append(key)
            else:
                merged[key] = merged[key] + usage

        new_lines = []
        for date, model in order:
            timestamp = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
            record = {"timestamp": timestamp.isoformat(), "model": model, **merged[(date, model)].to_dict()}
            new_lines.append(json.dumps(record, ensure_ascii=False))

        f.seek(0)
        f.write("".join(line + "\n" for line in new_lines))
        f.truncate()

    return len(lines), len(new_lines)


def main(argv: list[str] | None = None) -> int:
    """usage.jsonlに記録されたトークン使用量を集計して表示するCLI（`uv run python -m dante_norton.usage`）。"""
    parser = argparse.ArgumentParser(description="usage.jsonlに記録されたトークン使用量を集計して表示する")
    parser.add_argument("-a", "--all", action="store_true",
                        help="日付ごとの合計をすべて表示する（デフォルト: 今日の分のみ）")
    parser.add_argument("-f", "--file", type=Path, default=USAGE_PATH,
                        help="対象のusage.jsonlのパス（デフォルト: リポジトリ直下のusage.jsonl）")
    parser.add_argument("-m", "--merge", action="store_true",
                        help="UTC基準で日ごと・モデルごとにレコードを統合してファイルを書き換える")
    args = parser.parse_args(argv)

    if args.merge:
        if not args.file.exists():
            print(f"{args.file}: 記録がありません")
            return 1
        before, after = merge_usage(args.file)
        print(f"{args.file}: {before}行 → {after}行に統合しました")
        return 0

    totals = parse_usage_file(args.file)
    if not totals:
        print(f"{args.file}: 記録がありません")
        return 1

    if not args.all:
        date = today()
        if date not in totals:
            print(f"{date}の記録がありません")
            return 1
        print(f"# {date}")
        for model, usage in totals[date].items():
            print(format_usage_line(model, usage))
        return 0

    model_totals: dict[str, Usage] = {}
    sections: list[str] = []
    for date, by_model in totals.items():
        lines = [f"# {date}"]
        for model, usage in by_model.items():
            lines.append(format_usage_line(model, usage))
            model_totals[model] = usage if model not in model_totals else model_totals[model] + usage
        sections.append("\n".join(lines))

    total_lines = ["===== Total ====="]
    for model, usage in model_totals.items():
        total_lines.append(format_usage_line(model, usage))
    sections.append("\n".join(total_lines))

    print("\n\n".join(sections))
    return 0


if __name__ == "__main__":
    exit(main())
