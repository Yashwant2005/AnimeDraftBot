import argparse
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from pymongo import MongoClient


SYSTEM_DATABASES = {"admin", "config", "local"}


def _load_config_default(name, fallback=None):
    try:
        import config

        return getattr(config, name, fallback)
    except Exception:
        return fallback


def sanitize_filename(value, fallback="document"):
    text = str(value or fallback)
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", text).strip("._")
    return text[:120] or fallback


def get_export_root(output_root=None):
    base = Path(output_root) if output_root else Path(__file__).resolve().parent / "draf6t"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_utc")
    return base / stamp


def export_mongo_documents(
    mongo_uri,
    output_root=None,
    database_name=None,
    all_databases=False,
    include_system_databases=False,
):
    if not mongo_uri:
        raise ValueError("MongoDB URI is required. Set MONGO_URI or pass --mongo-uri.")

    export_dir = get_export_root(output_root)
    export_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(export_dir),
        "databases": {},
        "total_documents": 0,
    }

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("ping")

        if all_databases:
            database_names = client.list_database_names()
            if not include_system_databases:
                database_names = [name for name in database_names if name not in SYSTEM_DATABASES]
        else:
            database_names = [database_name or _load_config_default("MONGO_DB_NAME", "anime_bot")]

        for db_name in database_names:
            db = client[db_name]
            db_dir = export_dir / sanitize_filename(db_name, "database")
            db_dir.mkdir(parents=True, exist_ok=True)

            db_summary = {"collections": {}, "documents": 0}
            for collection_name in db.list_collection_names():
                collection = db[collection_name]
                collection_dir = db_dir / sanitize_filename(collection_name, "collection")
                collection_dir.mkdir(parents=True, exist_ok=True)

                count = 0
                for count, document in enumerate(collection.find({}), start=1):
                    doc_id = document.get("_id", count)
                    filename = f"{count:06d}_{sanitize_filename(doc_id)}.json"
                    file_path = collection_dir / filename
                    file_path.write_text(
                        json_util.dumps(document, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )

                db_summary["collections"][collection_name] = count
                db_summary["documents"] += count
                summary["total_documents"] += count

            summary["databases"][db_name] = db_summary

        summary_path = export_dir / "export_summary.json"
        summary_path.write_text(json_util.dumps(summary, indent=2), encoding="utf-8")
        return export_dir, summary
    finally:
        client.close()


def zip_export(export_dir):
    export_path = Path(export_dir)
    archive_base = export_path.with_suffix("")
    archive = shutil.make_archive(str(archive_base), "zip", export_path)
    return Path(archive)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Export MongoDB documents into one JSON file per document."
    )
    parser.add_argument("--mongo-uri", default=os.getenv("MONGO_URI") or _load_config_default("MONGO_URI"))
    parser.add_argument("--database", default=os.getenv("MONGO_DB_NAME") or _load_config_default("MONGO_DB_NAME"))
    parser.add_argument("--output", default=os.getenv("EXPORT_OUTPUT_DIR"))
    parser.add_argument("--all-databases", action="store_true")
    parser.add_argument("--include-system-databases", action="store_true")
    parser.add_argument("--zip", action="store_true", help="Also create a .zip archive of the export.")
    return parser


def main():
    args = build_arg_parser().parse_args()
    export_dir, summary = export_mongo_documents(
        mongo_uri=args.mongo_uri,
        output_root=args.output,
        database_name=args.database,
        all_databases=args.all_databases,
        include_system_databases=args.include_system_databases,
    )
    print(f"Exported {summary['total_documents']} document(s) to {export_dir}")

    if args.zip:
        archive = zip_export(export_dir)
        print(f"Created archive: {archive}")


if __name__ == "__main__":
    main()
