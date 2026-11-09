"""Merge verified auction-roster images and mirror the pool into anime_bot."""

import argparse
import csv
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from pymongo import MongoClient, UpdateOne

import config


BASE_DIR = Path(__file__).resolve().parent
ROSTER_CSV = BASE_DIR / "auction_roster.csv"
IMAGE_RESULTS_CSV = BASE_DIR / "auction_roster_image_results.csv"
MISSING_IMAGES_CSV = BASE_DIR / "auction_roster_without_images.csv"
BATCH_SIZE = 200
VERIFIED_STATUSES = {"confirmed", "confirmed_alias", "related_confirmed"}


def _identity(character):
    return (
        str(character.get("Character Name") or character.get("name") or "").strip(),
        str(character.get("Series") or character.get("anime") or "Unknown").strip(),
    )


def _chunks(items):
    for start in range(0, len(items), BATCH_SIZE):
        yield items[start:start + BATCH_SIZE]


def _read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def _verified_image_urls():
    results = _read_csv(IMAGE_RESULTS_CSV)
    urls = {}
    for row in results:
        url = (row.get("Image URL") or "").strip()
        if row.get("Status") not in VERIFIED_STATUSES:
            continue
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"Verified image result has an invalid URL: {row.get('Character Name')}")
        if row.get("Series Match") != "yes" and row.get("Status") != "related_confirmed":
            raise ValueError(f"Image series is not confirmed: {row.get('Character Name')}")
        urls[(row.get("Character Name", "").strip(), row.get("Series", "").strip())] = url
    return urls


def _prepare_roster():
    rows = _read_csv(ROSTER_CSV)
    if not rows:
        raise ValueError(f"{ROSTER_CSV.name} is empty.")
    image_urls = _verified_image_urls()
    identities = [_identity(row) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError(f"{ROSTER_CSV.name} contains duplicate name/series pairs.")

    updated = 0
    for row in rows:
        identity = _identity(row)
        if identity in image_urls and not row["Image URL"].startswith(("http://", "https://")):
            row["Image URL"] = image_urls[identity]
            updated += 1
    missing = [row for row in rows if not row["Image URL"].startswith(("http://", "https://"))]
    return rows, image_urls, updated, missing


def _write_roster(rows):
    backup = ROSTER_CSV.with_name(
        f"{ROSTER_CSV.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    )
    shutil.copy2(ROSTER_CSV, backup)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=ROSTER_CSV.parent,
            prefix=f".{ROSTER_CSV.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temp_name = output.name
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, ROSTER_CSV)
    except Exception:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
        raise
    return backup


def _write_missing_report(rows):
    with MISSING_IMAGES_CSV.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=("Character Name", "Series", "Total Power"))
        writer.writeheader()
        writer.writerows(
            {
                "Character Name": row["Character Name"],
                "Series": row["Series"],
                "Total Power": row["Total Power"],
            }
            for row in rows
        )


def _load_source_pool():
    client = MongoClient(config.GAME_MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("ping")
        collection = client[config.GAME_MONGO_DB_NAME]["auction_character_pool"]
        documents = list(collection.find({}))
        if not documents:
            raise RuntimeError(
                f"Source pool {config.GAME_MONGO_DB_NAME}.auction_character_pool is empty."
            )
        return documents
    finally:
        client.close()


def _apply_image_urls(documents, image_urls):
    indexed = {}
    for document in documents:
        if document.get("roster") != "auction_roster_v1":
            continue
        identity = _identity(document)
        if identity in indexed:
            raise ValueError(f"Duplicate roster record in source pool: {identity}")
        indexed[identity] = document

    missing = set(image_urls) - set(indexed)
    if missing:
        raise ValueError(f"Image search results do not map to source roster records: {sorted(missing)}")
    for identity, url in image_urls.items():
        indexed[identity]["image_url"] = url

    pool_ids = [document.get("pool_id") for document in documents]
    if any(not pool_id for pool_id in pool_ids) or len(pool_ids) != len(set(pool_ids)):
        raise ValueError("Source auction pool has missing or duplicate pool_id values.")


def _upsert_pool(target, documents):
    target.create_index("pool_id", unique=True, name="pool_id_unique")
    operations = []
    for document in documents:
        clean_document = {key: value for key, value in document.items() if key != "_id"}
        operations.append(
            UpdateOne(
                {"pool_id": clean_document["pool_id"]},
                {"$set": clean_document},
                upsert=True,
            )
        )
    for batch in _chunks(operations):
        target.bulk_write(batch, ordered=False)


def main():
    parser = argparse.ArgumentParser(
        description="Add auction pool documents and verified image URLs to anime_bot.auction_character_pool."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the roster CSV and MongoDB collection (default is a dry run).",
    )
    args = parser.parse_args()

    roster, image_urls, updated, missing = _prepare_roster()
    documents = _load_source_pool()
    _apply_image_urls(documents, image_urls)

    print(f"Source pool: {config.GAME_MONGO_DB_NAME}.auction_character_pool ({len(documents)} records)")
    print(f"Mongo target: {config.IMAGE_MONGO_DB_NAME}.auction_character_pool")
    print(f"Roster rows: {len(roster)}")
    print(f"Verified image URLs to add to roster: {updated}")
    print(f"Roster characters still without images: {len(missing)}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to update the CSV and target collection.")
        return 0

    target_client = MongoClient(config.IMAGE_MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        target_client.admin.command("ping")
        target = target_client[config.IMAGE_MONGO_DB_NAME]["auction_character_pool"]
        _upsert_pool(target, documents)

        pool_ids = [document["pool_id"] for document in documents]
        saved = target.count_documents({"pool_id": {"$in": pool_ids}})
        if saved != len(pool_ids):
            raise RuntimeError(f"Only {saved} of {len(pool_ids)} auction pool records were saved.")
        roster_backup = _write_roster(roster) if updated else None
        _write_missing_report(missing)
        image_count = sum(
            bool(target.find_one({"pool_id": pool_id}, {"image_url": 1, "_id": 0}).get("image_url"))
            for pool_id in pool_ids
        )
        print(f"Mongo records upserted: {len(pool_ids)}")
        print(f"Pool records with image URLs: {image_count}/{len(pool_ids)}")
        if roster_backup:
            print(f"Updated {ROSTER_CSV.name}; backup: {roster_backup}")
        print(f"Unresolved roster images: {MISSING_IMAGES_CSV}")
        return 0
    finally:
        target_client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Auction roster import failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
