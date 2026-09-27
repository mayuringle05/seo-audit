#!/usr/bin/env python3
"""Identify SiteOne page-weight offenders from an exact URL list with bounded bisection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit


def read_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for raw in path.read_text().splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"Invalid public HTTP(S) URL: {value}")
        urls.append(value)
    if not urls:
        raise ValueError("URL list is empty")
    if len(urls) > 100:
        raise ValueError("Diagnostic is capped at 100 pages")
    hosts = {urlsplit(url).netloc.lower() for url in urls}
    if len(hosts) != 1:
        raise ValueError("All diagnostic URLs must use the same host")
    return list(dict.fromkeys(urls))


def overweight_count(report: dict) -> int:
    for category in report.get("qualityScores", {}).get("categories", []):
        if category.get("code") != "performance":
            continue
        for deduction in category.get("deductions", []):
            reason = str(deduction.get("reason", ""))
            match = re.search(r"(\d+) page\(s\) over the page-weight budget", reason)
            if match:
                return int(match.group(1))

    for item in report.get("summary", {}).get("items", []):
        if item.get("aplCode") != "pages-weight-exceeded":
            continue
        match = re.search(r"(\d+) page\(s\) exceed", str(item.get("text", "")))
        if match:
            return int(match.group(1))
        if item.get("status") == "OK":
            return 0

    raise ValueError("SiteOne report did not expose the page-weight finding")


def run_group(
    binary: str,
    urls: list[str],
    work: Path,
    cache: Path,
    sequence: int,
    requests_per_second: float,
) -> int:
    group = work / f"group-{sequence:02d}.txt"
    report = work / f"group-{sequence:02d}.json"
    group.write_text("\n".join(urls) + "\n")

    command = [
        binary,
        f"--url-list={group}",
        "--single-page",
        "--workers=1",
        f"--max-reqs-per-sec={requests_per_second}",
        f"--http-cache-dir={cache}",
        "--hide-progress-bar",
        "--no-color",
        f"--output-json-file={report}",
    ]
    result = subprocess.run(command, cwd=work, capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"SiteOne failed for diagnostic group {sequence}: {detail}")

    return overweight_count(json.loads(report.read_text()))


def diagnose(
    binary: str,
    urls: list[str],
    requests_per_second: float,
) -> tuple[list[str], int]:
    sequence = 0
    with tempfile.TemporaryDirectory(prefix="siteone-page-weight-") as raw:
        work = Path(raw)
        cache = work / "http-cache"
        cache.mkdir()

        def measure(group: list[str]) -> int:
            nonlocal sequence
            sequence += 1
            count = run_group(
                binary,
                group,
                work,
                cache,
                sequence,
                requests_per_second,
            )
            print(
                f"[{sequence:02d}] {len(group)} page(s) checked -> {count} over budget",
                flush=True,
            )
            return count

        total = measure(urls)
        offenders: list[str] = []

        def split(group: list[str], known_count: int) -> None:
            if known_count <= 0:
                return
            if known_count > len(group):
                raise RuntimeError("SiteOne returned an impossible page-weight count")
            if len(group) == 1:
                offenders.append(group[0])
                return

            midpoint = len(group) // 2
            left = group[:midpoint]
            right = group[midpoint:]
            left_count = measure(left)
            right_count = known_count - left_count
            if right_count < 0 or right_count > len(right):
                raise RuntimeError(
                    "Page-weight counts changed during diagnosis; rerun after the site is stable"
                )
            split(left, left_count)
            split(right, right_count)

        split(urls, total)
        return offenders, sequence


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find which exact pages exceed SiteOne's page-weight budget without "
            "running every page separately."
        )
    )
    parser.add_argument("url_list", type=Path)
    parser.add_argument(
        "--max-reqs-per-sec",
        type=float,
        default=1.0,
        help="SiteOne request rate; default 1.0",
    )
    args = parser.parse_args()

    if not 0.2 <= args.max_reqs_per_sec <= 2.0:
        raise ValueError("--max-reqs-per-sec must be between 0.2 and 2.0")

    binary = shutil.which("siteone-crawler")
    if not binary:
        raise ValueError("siteone-crawler not found in PATH")

    urls = read_urls(args.url_list)
    offenders, runs = diagnose(binary, urls, args.max_reqs_per_sec)

    print("\nPAGE-WEIGHT OFFENDERS")
    if offenders:
        for url in offenders:
            print(url)
    else:
        print("none")
    print(f"\nDiagnostic crawls: {runs}")
    print(
        "A fresh private cache is used for the first requests and then reused only "
        "inside this diagnostic, so subgroup checks do not reuse an older global crawl."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"page-weight diagnosis failed: {error}", file=__import__("sys").stderr)
        raise SystemExit(2)
