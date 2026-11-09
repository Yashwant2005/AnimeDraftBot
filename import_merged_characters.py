"""Import the merged character catalog into MongoDB and the JSON export."""

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from bson import json_util
from pymongo import MongoClient, UpdateOne

import config
import database


BASE_DIR = Path(__file__).resolve().parent
SOURCE_JSON = BASE_DIR / "merged_characters_with_img.json"
EXPORT_JSON = BASE_DIR / "anime_bot.characters.json"
MISSING_IMAGES_CSV = BASE_DIR / "characters_without_images.csv"
BATCH_SIZE = 200


def _identity(character):
    return (
        str(character.get("name") or "").strip(),
        str(character.get("series") or "Unknown").strip(),
    )


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _fetch_existing(collection, characters):
    existing = {}
    identities = {_identity(character) for character in characters}
    for batch in _chunks(list(identities), BATCH_SIZE):
        query = {
            "$or": [
                {"name": name, "series": series}
                for name, series in batch
            ]
        }
        for document in collection.find(query):
            existing[_identity(document)] = document
    return existing


def _load_source():
    with SOURCE_JSON.open(encoding="utf-8-sig") as source:
        data = json.load(source)
    if not isinstance(data, dict) or not isinstance(data.get("characters"), list):
        raise ValueError(f"{SOURCE_JSON.name} must contain a 'characters' list.")
    characters = data["characters"]
    if any(not isinstance(item, dict) or not _identity(item)[0] for item in characters):
        raise ValueError("Every source character must be an object with a non-empty name.")
    identities = [_identity(item) for item in characters]
    if len(identities) != len(set(identities)):
        raise ValueError("Source JSON contains duplicate (name, series) identities.")
    return characters


def _load_export():
    with EXPORT_JSON.open(encoding="utf-8-sig") as source:
        data = json_util.loads(source.read())
    if not isinstance(data, list):
        raise ValueError(f"{EXPORT_JSON.name} must contain a JSON array.")
    return data


def _write_missing_image_report(characters):
    fields = ("name", "series")
    with MISSING_IMAGES_CSV.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {"name": character["name"], "series": character.get("series", "Unknown")}
            for character in characters
            if not str(character.get("img") or "").strip()
        )


def _save_export(documents):
    EXPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    backup = EXPORT_JSON.with_name(
        f"{EXPORT_JSON.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    )
    shutil.copy2(EXPORT_JSON, backup)
    serialized = json_util.dumps(documents, indent=2, ensure_ascii=False) + "\n"
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=EXPORT_JSON.parent,
            prefix=f".{EXPORT_JSON.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_name = temp_file.name
            temp_file.write(serialized)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, EXPORT_JSON)
    except Exception:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
        raise
    return backup


def _import_documents(collection, characters, existing):
    operations = []
    for character in characters:
        identity = _identity(character)
        if identity in existing:
            continue
        document = dict(character)
        document["name"], document["series"] = identity
        operations.append(
            UpdateOne(
                {"name": identity[0], "series": identity[1]},
                {"$setOnInsert": document},
                upsert=True,
            )
        )
    if operations:
        for batch in _chunks(operations, BATCH_SIZE):
            collection.bulk_write(batch, ordered=False)
    return len(operations)


def main():
    parser = argparse.ArgumentParser(
        description="Add merged characters to a Mongo character collection and anime_bot.characters.json."
    )
    parser.add_argument(
        "--target-database",
        choices=("game", "image"),
        default="game",
        help="Mongo database target: game uses GAME_MONGO_URI; image uses IMAGE_MONGO_URI.",
    )
    parser.add_argument(
        "--collection",
        default="characters",
        help="Mongo collection name (default: characters).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the Mongo/JSON writes (default is a dry run).",
    )
    args = parser.parse_args()

    characters = _load_source()
    export_documents = _load_export()
    export_identities = {_identity(document) for document in export_documents}
    json_additions = [
        character for character in characters
        if _identity(character) not in export_identities
    ]
    missing_count = sum(not str(character.get("img") or "").strip() for character in characters)

    if args.target_database == "image":
        mongo_uri = config.IMAGE_MONGO_URI
        database_name = config.IMAGE_MONGO_DB_NAME
    else:
        mongo_uri = config.MONGO_URI
        database_name = config.MONGO_DB_NAME

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("ping")
        collection = client[database_name][args.collection]
        if args.apply:
            if args.target_database == "game" and args.collection == "characters":
                database._ensure_character_identity_index(collection)
            elif args.target_database == "image":
                collection.create_index(
                    [("name", 1), ("series", 1)],
                    name="name_series_lookup",
                )
        existing = _fetch_existing(collection, characters)
        to_insert = [character for character in characters if _identity(character) not in existing]

        print(f"Mongo target: {database_name}.{args.collection}")
        print(f"Source characters: {len(characters)}")
        print(f"Already in Mongo: {len(existing)}")
        print(f"New Mongo records: {len(to_insert)}")
        print(f"New records for {EXPORT_JSON.name}: {len(json_additions)}")
        print(f"Characters with blank image field: {missing_count}")
        if not args.apply:
            print("Dry run only. Re-run with --apply to write MongoDB and JSON.")
            return 0

        inserted = _import_documents(collection, characters, existing)
        saved = _fetch_existing(collection, characters)
        if len(saved) != len(characters):
            raise RuntimeError(
                f"MongoDB contains only {len(saved)} of {len(characters)} requested character identities."
            )

        current_export = _load_export()
        current_identities = {_identity(document) for document in current_export}
        additions = [
            saved[_identity(character)]
            for character in characters
            if _identity(character) not in current_identities
        ]
        backup = None
        if additions:
            backup = _save_export(current_export + additions)
        _write_missing_image_report(characters)

        print(f"Mongo records inserted: {inserted}")
        print(f"JSON records appended: {len(additions)}")
        if backup:
            print(f"JSON backup: {backup}")
        print(f"Missing-image report: {MISSING_IMAGES_CSV}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
