"""Search AniList for character artwork by character name and series."""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


ANILIST_URL = "https://graphql.anilist.co"
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUTS = (
    BASE_DIR / "merged_characters_with_img.json",
    BASE_DIR / "merged_characters.json",
)
DEFAULT_OUTPUT = BASE_DIR / "character_image_search_results.csv"
REQUEST_DELAY_SECONDS = 1.5
QUERY = """
query ($search: String) {
  Page(page: 1, perPage: 10) {
    characters(search: $search, sort: SEARCH_MATCH) {
      id
      name { full alternative }
      image { large }
      siteUrl
      media(perPage: 50, sort: POPULARITY_DESC) {
        nodes {
          title { romaji english native }
          siteUrl
        }
      }
    }
  }
}
"""


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def search_characters(name):
    payload = json.dumps({
        "query": QUERY,
        "variables": {"search": name},
    }).encode("utf-8")
    request = Request(
        ANILIST_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AnimeDraftBotCharacterImageSearch/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AniList returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach AniList: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("AniList search timed out.") from exc

    if result.get("errors"):
        messages = "; ".join(error.get("message", "Unknown GraphQL error") for error in result["errors"])
        raise RuntimeError(f"AniList search failed: {messages}")
    return result.get("data", {}).get("Page", {}).get("characters", [])


def series_titles(character):
    titles = set()
    media = character.get("media", {}).get("nodes", [])
    for item in media:
        for title in (item.get("title") or {}).values():
            if title:
                titles.add(title)
    return titles


def series_match(character, requested_series):
    target = normalize(requested_series)
    return any(normalize(title) == target for title in series_titles(character))


def display_results(name, series, results):
    if not results:
        print(f"No AniList character results found for: {name}")
        return

    ranked = sorted(
        enumerate(results),
        key=lambda item: (not series_match(item[1], series), item[0]),
    )
    print(f"\nSearch: {name} | {series}\n")
    for index, (_, character) in enumerate(ranked, start=1):
        names = character.get("name") or {}
        candidate_name = names.get("full") or name
        titles = sorted(series_titles(character))
        matching = series_match(character, series)
        print(f"{index}. {candidate_name} — {'SERIES MATCH' if matching else 'series not confirmed'}")
        if titles:
            print(f"   Related titles: {', '.join(titles[:8])}")
        print(f"   Image: {character.get('image', {}).get('large') or 'not available'}")
        print(f"   AniList: {character.get('siteUrl') or 'not available'}")


def load_pending_characters(input_path):
    with input_path.open(encoding="utf-8-sig") as source:
        data = json.load(source)
    if not isinstance(data, dict) or not isinstance(data.get("characters"), list):
        raise ValueError(f"{input_path} must contain a 'characters' list.")
    return [
        character for character in data["characters"]
        if isinstance(character, dict) and not str(character.get("img") or "").strip()
    ]


def best_match(name, series, results):
    ranked = sorted(
        enumerate(results),
        key=lambda item: (
            not series_match(item[1], series),
            normalize((item[1].get("name") or {}).get("full") or "") != normalize(name),
            item[0],
        ),
    )
    for _, character in ranked:
        image_url = (character.get("image") or {}).get("large")
        if image_url:
            return character, series_match(character, series)
    return None, False


def search_json(input_path, output_path, limit=None):
    pending = load_pending_characters(input_path)
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        print(f"No characters with a blank img field found in {input_path}.")
        return

    fields = ("name", "series", "series_match", "image_url", "anilist_url", "error")
    searched = 0
    with output_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for index, character in enumerate(pending, start=1):
            name = str(character.get("name") or "").strip()
            series = str(character.get("series") or "").strip()
            print(f"[{index}/{len(pending)}] Searching {name} | {series}")
            row = {
                "name": name,
                "series": series,
                "series_match": "no",
                "image_url": "",
                "anilist_url": "",
                "error": "",
            }
            if name:
                try:
                    results = search_characters(name)
                    candidate, matched = best_match(name, series, results)
                    if candidate:
                        row["series_match"] = "yes" if matched else "no"
                        row["image_url"] = (candidate.get("image") or {}).get("large") or ""
                        row["anilist_url"] = candidate.get("siteUrl") or ""
                        searched += bool(row["image_url"])
                        print(
                            f"  {'SERIES MATCH' if matched else 'series not confirmed'}: "
                            f"{row['image_url'] or 'no image'}"
                        )
                    else:
                        print("  No image result found.")
                except RuntimeError as exc:
                    row["error"] = str(exc)
                    print(f"  Search failed: {exc}", file=sys.stderr)
            else:
                row["error"] = "Character name is empty."
                print("  Skipped: character name is empty.", file=sys.stderr)

            writer.writerow(row)
            output.flush()
            if index < len(pending):
                time.sleep(REQUEST_DELAY_SECONDS)

    no_result = len(pending) - searched
    print(
        f"\nSearch complete: checked {len(pending)} characters, "
        f"found {searched} image URLs, no image for {no_result}."
    )
    print(f"Results saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Search AniList for images of characters with blank img fields in the merged JSON."
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Merged JSON file (defaults to merged_characters_with_img.json, then merged_characters.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"CSV file for search results (default: {DEFAULT_OUTPUT.name})",
    )
    parser.add_argument("--limit", type=int, help="Search only the first N characters (useful for testing)")
    parser.add_argument("--name", help="Search one character instead of reading the JSON")
    parser.add_argument("--series", help="Series name for a one-character search")
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1.")
    if bool(args.name) != bool(args.series):
        parser.error("--name and --series must be provided together.")

    try:
        if args.name:
            name, series = args.name.strip(), args.series.strip()
            if not name or not series:
                parser.error("--name and --series must not be empty.")
            display_results(name, series, search_characters(name))
        else:
            input_path = args.input or next((path for path in DEFAULT_INPUTS if path.exists()), None)
            if input_path is None:
                raise FileNotFoundError("Could not find merged_characters_with_img.json or merged_characters.json.")
            search_json(input_path, args.output, args.limit)
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
