#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess
import requests
import json
import tempfile
import time
from pathlib import Path
import zipfile
import configparser
import re

# Load configuration from ini file
config = configparser.ConfigParser()
config.read('config.ini')

# Get folder paths from config or use default
WATCH_DIR = Path(config.get('folders', 'watch_dir', fallback="/WATCHED"))
DEST_DIR = Path(config.get('folders', 'dest_dir', fallback="/DEST"))
PROCESSED_JSON = WATCH_DIR / "processed_files.json"
PROCESSED_DIR = Path(config.get('folders', 'processed_dir', fallback="processed"))

# Ensure necessary directories exist
DEST_DIR.mkdir(parents=True, exist_ok=True)
(WATCH_DIR / PROCESSED_DIR).mkdir(parents=True, exist_ok=True)

# Load processed files list
def load_processed_files():
    if PROCESSED_JSON.exists():
        with open(PROCESSED_JSON, "r") as f:
            return set(json.load(f))
    return set()

# Save processed files list
def save_processed_files(processed_files):
    with open(PROCESSED_JSON, "w") as f:
        json.dump(list(processed_files), f)

processed_files = load_processed_files()

# Fetch metadata from GOG API
def fetch_metadata(game_id):
    api_url = f"https://api.gog.com/products/{game_id}"
    print(f"Fetching metadata for game ID {game_id} from GOG API...")
    response = requests.get(api_url)
    
    if response.status_code != 200:
        print("Error: Unable to fetch data from GOG API.")
        return "Unknown Game", "0000"

    data = response.json()
    gt = data.get("title", "Unknown Game")
    game_title = re.sub(r'[^\w\s]', '', gt)
    release_date = data.get("release_date", "0000")
    if release_date:
        year = release_date.split("-")[0]
    else:
        year = "0000"
    return game_title, year

# Process a single installer (.exe) in WATCH_DIR
def process_installer(installer):
    print(f"Extracting GOG game ID from {installer}...")
    try:
        result = subprocess.run(["innoextract", "--gog-game-id", str(installer)], capture_output=True, text=True)
        gog_game_id = next((line.split("ID is ")[-1] for line in result.stdout.splitlines() if "ID is " in line), None)
        if not gog_game_id:
            raise ValueError("No game ID found")
    except Exception as e:
        print(f"Error: Unable to extract GOG game ID: {e}")
        return
    
    # Fetch game metadata using the installer as the base game
    game_name, year = fetch_metadata(gog_game_id)
    if year == "0000":
        folder_name = f"{game_name} (W_P)"
    else:
        folder_name = f"{game_name} (W_P) ({year})"
    folder_name = folder_name.replace(":", "")
    
    # Create a subfolder inside processed folder named after the game
    game_folder = WATCH_DIR / PROCESSED_DIR / folder_name
    game_folder.mkdir(parents=True, exist_ok=True)
    
    # Create temporary directory
    temp_dir = Path(tempfile.mkdtemp(prefix="processing_", dir=WATCH_DIR))
    try:
        print(f"Extracting {installer}...")
        subprocess.run(["innoextract", "--gog", "--exclude-temp", "--output-dir", str(temp_dir), str(installer)], check=True)
        print(f"Creating zip archive in {DEST_DIR}...")
        zip_name = f"{folder_name}.zip"
        with zipfile.ZipFile(DEST_DIR / zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    file_path = Path(root) / file
                    zipf.write(file_path, file_path.relative_to(temp_dir))
        print(f"Extraction, zipping, and cleanup completed successfully!")
        print(f"Archive: {DEST_DIR / zip_name}")
    finally:
        print(f"Cleaning up {temp_dir}...")
        shutil.rmtree(temp_dir)
    
    # Move processed installer and related .bin files to the processed folder
    processed_path = game_folder / installer.name
    installer.rename(processed_path)
    print(f"Moved {installer} to {processed_path}.")
    for bin_file in WATCH_DIR.glob(installer.stem + "-*.bin"):
        new_bin_path = game_folder / bin_file.name
        bin_file.rename(new_bin_path)
        print(f"Moved {bin_file} to {new_bin_path}.")
    
    processed_files.add(str(installer))
    save_processed_files(processed_files)

# Process all installers in a directory as one game (base game metadata is used)
def process_directory_game(game_dir):
    print(f"Processing game in directory: {game_dir}")
    exe_files = list(game_dir.glob("*.exe"))
    if not exe_files:
        print(f"No installer found in {game_dir}. Skipping.")
        return

    # Select the base installer using the shortest filename heuristic.
    base_installer = min(exe_files, key=lambda e: len(e.name))
    print(f"Determined base installer as: {base_installer.name}")

    try:
        result = subprocess.run(
            ["innoextract", "--gog-game-id", str(base_installer)],
            capture_output=True,
            text=True
        )
        gog_game_id = next(
            (line.split("ID is ")[-1] for line in result.stdout.splitlines() if "ID is " in line),
            None
        )
        if not gog_game_id:
            raise ValueError("No game ID found")
    except Exception as e:
        print(f"Error: Unable to extract GOG game ID from {base_installer}: {e}")
        return

    game_name, year = fetch_metadata(gog_game_id)
    if year == "0000":
        folder_name = f"{game_name} (W_P)"
    else:
        folder_name = f"{game_name} (W_P) ({year})"
    folder_name = folder_name.replace(":", "")

    game_folder = WATCH_DIR / PROCESSED_DIR / folder_name
    game_folder.mkdir(parents=True, exist_ok=True)

    # Create a temporary directory for extraction
    temp_dir = Path(tempfile.mkdtemp(prefix="processing_", dir=WATCH_DIR))
    try:
        # FIRST, process the base installer.
        if str(base_installer) not in processed_files:
            print(f"Extracting base installer: {base_installer}")
            subprocess.run(
                ["innoextract", "--gog", "--exclude-temp", "--output-dir", str(temp_dir), str(base_installer)],
                check=True
            )

        # THEN, process the remaining installers.
        for installer in exe_files:
            # Skip the base installer (already processed) and any already processed files.
            if installer == base_installer or str(installer) in processed_files:
                continue
            print(f"Extracting {installer}...")
            subprocess.run(
                ["innoextract", "--gog", "--exclude-temp", "--output-dir", str(temp_dir), str(installer)],
                check=True
            )
    except Exception as e:
        print(f"Error during extraction: {e}")
        shutil.rmtree(temp_dir)
        return

    try:
        print(f"Creating zip archive in {DEST_DIR}...")
        zip_name = f"{folder_name}.zip"
        with zipfile.ZipFile(DEST_DIR / zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    file_path = Path(root) / file
                    zipf.write(file_path, file_path.relative_to(temp_dir))
        print(f"Extraction, zipping, and cleanup completed successfully!")
        print(f"Archive: {DEST_DIR / zip_name}")
    finally:
        print(f"Cleaning up {temp_dir}...")
        shutil.rmtree(temp_dir)

    # Move all processed installer files
    for installer in exe_files:
        if str(installer) in processed_files:
            continue
        processed_path = game_folder / installer.name
        installer.rename(processed_path)
        print(f"Moved {installer} to {processed_path}.")
        processed_files.add(str(installer))

    # Process and move any .bin files related to the installers.
    for bin_file in game_dir.glob("*.bin"):
        new_bin_path = game_folder / bin_file.name
        bin_file.rename(new_bin_path)
        print(f"Moved {bin_file} to {new_bin_path}.")

    save_processed_files(processed_files)

# Watch the folder for new EXE files or directories containing EXE files
def watch_folder():
    print(f"Watching {WATCH_DIR} for new EXE files or directories...")
    while True:
        try:
            # Process individual EXE files in the main WATCH_DIR
            exe_files = list(WATCH_DIR.glob("*.exe"))
            if exe_files:
                for exe_file in exe_files:
                    if str(exe_file) not in processed_files:
                        print(f"New installer detected: {exe_file}")
                        process_installer(exe_file)
            # Also check each subdirectory in WATCH_DIR for EXE files
            for sub_dir in WATCH_DIR.iterdir():
                if sub_dir.is_dir():
                    exe_files_in_dir = list(sub_dir.glob("*.exe"))
                    if exe_files_in_dir:
                        process_directory_game(sub_dir)
        except Exception as e:
            print(f"Error in watcher loop: {e}")
        time.sleep(5)


if __name__ == "__main__":
    watch_folder()
