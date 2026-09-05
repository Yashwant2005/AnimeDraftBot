import json
import logging
import re
import tempfile
import time
from pathlib import Path

import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


BASE_DIR = Path(__file__).resolve().parent
CHAR_JSON = BASE_DIR / "character.json"
DELAY_SECONDS = 1.0
CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"


logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(BASE_DIR / "downloader_fast.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


def sanitize_for_filename(value: str) -> str:
    value = (value or "").strip().replace(" ", "_")
    return re.sub(r"[^A-Za-z0-9_\-]", "", value)


def load_characters(path: Path) -> list:
    with path.open(encoding="utf-8-sig") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError("character.json must contain a list")
    return data


def save_characters(path: Path, data: list) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def setup_driver():
    options = Options()
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,800")
    options.add_argument("--no-sandbox")
    options.add_argument("--log-level=3")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def download_file(url: str, save_path: Path) -> bool:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code != 200 or not response.content:
            return False
        save_path.write_bytes(response.content)
        return True
    except Exception as exc:
        logging.warning("Download failed for %s: %s", url, exc)
        return False


def upload_to_catbox(file_path: Path) -> str | None:
    try:
        with file_path.open("rb") as file_obj:
            response = requests.post(
                CATBOX_UPLOAD_URL,
                data={"reqtype": "fileupload"},
                files={"fileToUpload": file_obj},
                timeout=60,
            )
        if response.ok:
            url = response.text.strip()
            if url.startswith("https://files.catbox.moe/"):
                return url
        logging.warning("Catbox upload failed: %s %s", response.status_code, response.text[:200])
    except Exception as exc:
        logging.warning("Catbox upload error for %s: %s", file_path, exc)
    return None


def search_bing(driver, query: str) -> str | None:
    try:
        driver.get(f"https://www.bing.com/images/search?q={query}")
        wait = WebDriverWait(driver, 5)
        thumbs = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "img.mimg")))
        for img in thumbs:
            if not img.is_displayed():
                continue
            for attr in ("src", "data-src"):
                src = img.get_attribute(attr)
                if src and src.startswith("http"):
                    return src
    except TimeoutException:
        logging.info("Bing timeout for query: %s", query)
    except Exception as exc:
        logging.warning("Bing search error for %s: %s", query, exc)
    return None


def search_google_backup(driver, query: str) -> str | None:
    try:
        driver.get(f"https://www.google.com/search?q={query}&tbm=isch")
        wait = WebDriverWait(driver, 5)
        thumbs = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "img")))
        for img in thumbs:
            src = img.get_attribute("src")
            if src and src.startswith("http"):
                return src
    except TimeoutException:
        logging.info("Google timeout for query: %s", query)
    except Exception as exc:
        logging.warning("Google search error for %s: %s", query, exc)
    return None


def find_image_url(driver, name: str, series: str) -> str | None:
    query = f"{name} {series}".strip()

    image_url = search_bing(driver, query)
    if image_url:
        return image_url

    logging.info("Bing failed for %s. Trying Google.", query)
    return search_google_backup(driver, query)


def main():
    if not CHAR_JSON.exists():
        raise FileNotFoundError(f"{CHAR_JSON} not found")

    characters = load_characters(CHAR_JSON)
    pending = [entry for entry in characters if isinstance(entry, dict) and not (entry.get("img") or "").strip()]

    if not pending:
        logging.info("No blank img entries found in %s", CHAR_JSON)
        return

    logging.info("Found %s characters with blank img fields.", len(pending))
    driver = setup_driver()

    try:
        for index, entry in enumerate(pending, 1):
            name = entry.get("name", "").strip()
            series = entry.get("series", "").strip()
            if not name:
                continue

            logging.info("[%s/%s] Processing %s | %s", index, len(pending), name, series)
            image_url = find_image_url(driver, name, series)
            if not image_url:
                logging.info("No image found for %s", name)
                time.sleep(DELAY_SECONDS)
                continue

            filename = sanitize_for_filename(f"{name}_{series}".lower()) or "character"
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir) / f"{filename}.jpg"
                if not download_file(image_url, temp_path):
                    logging.info("Download failed for %s", name)
                    time.sleep(DELAY_SECONDS)
                    continue

                catbox_url = upload_to_catbox(temp_path)
                if not catbox_url:
                    logging.info("Catbox upload failed for %s", name)
                    time.sleep(DELAY_SECONDS)
                    continue

                entry["img"] = catbox_url
                save_characters(CHAR_JSON, characters)
                logging.info("Saved %s -> %s", name, catbox_url)

            time.sleep(DELAY_SECONDS)
    finally:
        driver.quit()

    logging.info("Done.")


if __name__ == "__main__":
    main()
