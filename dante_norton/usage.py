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


def main(argv: list[str] | None = None) -> int:
    """usage.jsonlに記録されたトークン使用量を集計して表示するCLI（`uv run python -m dante_norton.usage`）。"""
    parser = argparse.ArgumentParser(description="usage.jsonlに記録されたトークン使用量を集計して表示する")
    parser.add_argument("-a", "--all", action="store_true",
                        help="日付ごとの合計をすべて表示する（デフォルト: 今日の分のみ）")
    args = parser.parse_args(argv)

    totals = parse_usage_file()
    if not totals:
        print(f"{USAGE_PATH}: 記録がありません")
        return 1

    if not args.all:
        date = today()
        if date not in totals:
            print(f"{date}の記録がありません")
            return 1
        print(date)
        for model, usage in totals[date].items():
            print(f"  {model} {usage.to_dict()}")
        return 0

    model_totals: dict[str, Usage] = {}
    for date, by_model in totals.items():
        print(date)
        for model, usage in by_model.items():
            print(f"  {model} {usage.to_dict()}")
            model_totals[model] = usage if model not in model_totals else model_totals[model] + usage
    print("=" * 10)
    for model, usage in model_totals.items():
        print(f"{model} {usage.to_dict()}")
    return 0


if __name__ == "__main__":
    exit(main())
