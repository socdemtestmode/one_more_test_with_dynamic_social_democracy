import os
import shutil
import subprocess
import sys
import json

def run_command(command):
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        return None

def compare_dirs(dir1, dir2):
    """Simple comparison of directory contents by content."""
    files1 = {}
    for root, _, filenames in os.walk(dir1):
        for f in filenames:
            rel = os.path.relpath(os.path.join(root, f), dir1)
            with open(os.path.join(root, f), 'rb') as file:
                files1[rel] = file.read()

    files2 = {}
    for root, _, filenames in os.walk(dir2):
        for f in filenames:
            rel = os.path.relpath(os.path.join(root, f), dir2)
            with open(os.path.join(root, f), 'rb') as file:
                files2[rel] = file.read()

    common = set(files1.keys()).intersection(set(files2.keys()))
    only1 = set(files1.keys()) - set(files2.keys())
    only2 = set(files2.keys()) - set(files1.keys())

    mismatched = []
    for f in common:
        if files1[f] != files2[f]:
            mismatched.append(f)

    return common, only1, only2, mismatched

def extract_game_from_js(core_js_path):
    with open(core_js_path, 'r', encoding='utf-8') as f:
        content = f.read()

    prefix = "window.game="
    start_idx = content.find(prefix)
    if start_idx == -1:
        return None

    json_part = content[start_idx + len(prefix):]

    try:
        decoder = json.JSONDecoder()
        obj, end = decoder.raw_decode(json_part)
        return obj
    except Exception as e:
        print(f"Error parsing JSON: {e}")
        return None

def main():
    core_js = "out/html/core.js"
    output_dir = "decompiled_source"

    if len(sys.argv) > 1:
        core_js = sys.argv[1]
    if len(sys.argv) > 2:
        output_dir = sys.argv[2]

    print(f"Starting universal decompiler...")
    print(f"Core JS: {core_js}")
    print(f"Output: {output_dir}")

    game_wrapper = extract_game_from_js(core_js)
    if not game_wrapper:
        print("Failed to extract game data from core.js")
        sys.exit(1)

    compiled_json_str = game_wrapper.get('compiled')
    if not compiled_json_str:
        print("Compiled game data not found in wrapper")
        sys.exit(1)

    game = json.loads(compiled_json_str)

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    files_to_write = {}

    def collect_files(obj):
        if isinstance(obj, dict):
            if '$metadata' in obj:
                metadata = obj['$metadata']
                if '$raw' in metadata and '$file' in metadata:
                    files_to_write[metadata['$file']] = metadata['$raw']
            for val in obj.values():
                collect_files(val)
        elif isinstance(obj, list):
            for item in obj:
                collect_files(item)

    collect_files(game)

    for filepath, raw_content in files_to_write.items():
        if 'source/' in filepath:
            rel_path = filepath.split('source/', 1)[1]
        else:
            rel_path = os.path.basename(filepath)

        full_out_path = os.path.join(output_dir, rel_path)
        os.makedirs(os.path.dirname(full_out_path), exist_ok=True)

        with open(full_out_path, 'w', encoding='utf-8') as f:
            f.write(raw_content)

    print(f"Restored {len(files_to_write)} files.")

    # 3. Compare with current source if it exists
    if os.path.exists('source'):
        print("\nComparing decompiled output with existing 'source' directory:")
        common, only_source, only_decomp, mismatched = compare_dirs('source', output_dir)

        print(f"Common files: {len(common)}")
        if only_source:
            print(f"Files ONLY in original source ({len(only_source)}):")
            for f in sorted(list(only_source))[:10]:
                print(f"  - {f}")

        if only_decomp:
            print(f"Files ONLY in decompiled output ({len(only_decomp)}):")
            for f in sorted(list(only_decomp))[:10]:
                print(f"  - {f}")

        if mismatched:
            print(f"Mismatched files ({len(mismatched)}):")
            for f in sorted(mismatched)[:10]:
                print(f"  - {f}")

        if not only_source and not only_decomp and not mismatched:
            print("SUCCESS: 1:1 character-perfect match achieved!")
        else:
            print("FAILURE: Discrepancies found.")

if __name__ == "__main__":
    main()
