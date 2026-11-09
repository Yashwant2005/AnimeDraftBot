"""
imgbb_upload.py - fills the "img" field in merged_characters.json using imgbb.

1. Make images.csv with two columns (no header needed beyond the first row):
      name,source
      Aang,https://example.com/aang.jpg      <- direct image link
      Zuko,C:\\pics\\zuko.png                 <- or a local file
   Add "series" as a third column if two series share a character name.
2. Get a free API key from https://api.imgbb.com/
3. Set IMGBB_API_KEY in your environment, then run:  python imgbb_upload.py
Needs:  pip install requests
"""
import csv, json, base64, os, time, requests

KEY = os.environ.get("IMGBB_API_KEY")
if not KEY:
    raise SystemExit("Set the IMGBB_API_KEY environment variable before running.")
data = json.load(open("merged_characters.json", encoding="utf-8"))
rows = list(csv.DictReader(open("images.csv", encoding="utf-8-sig")))

def upload(src):
    if os.path.isfile(src):
        payload = {"image": base64.b64encode(open(src, "rb").read()).decode()}
    else:
        payload = {"image": src}
    r = requests.post("https://api.imgbb.com/1/upload", params={"key": KEY}, data=payload, timeout=60)
    r.raise_for_status()
    return r.json()["data"]["url"]

done = 0
for row in rows:
    name, src = row["name"].strip(), row["source"].strip()
    series = (row.get("series") or "").strip()
    targets = [c for c in data["characters"]
               if c["name"].lower() == name.lower() and (not series or c["series"].lower() == series.lower())]
    if not targets:
        print("not found:", name); continue
    try:
        url = upload(src)
    except Exception as e:
        print("failed:", name, e); continue
    for c in targets:
        c["img"] = url
    done += 1
    print("ok:", name, url)
    time.sleep(1)

json.dump(data, open("merged_characters_with_img.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("done", done, "-> merged_characters_with_img.json")
