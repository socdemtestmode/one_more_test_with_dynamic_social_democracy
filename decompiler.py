import os
import shutil
import json
import re
import sys
import logging
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("decompiler")

class Progress:
    """A simple progress bar for console and Colab."""
    def __init__(self, total, title="Processing"):
        self.total = total
        self.current = 0
        self.title = title
        self.last_percent = -1

    def update(self, n=1):
        self.current += n
        if self.total <= 0: return
        percent = int(100 * (self.current / self.total))
        if percent > self.last_percent:
            bar_len = 40
            filled_len = int(bar_len * self.current // self.total)
            bar = '#' * filled_len + '-' * (bar_len - filled_len)
            sys.stdout.write(f'\r{self.title}: [{bar}] {percent}% ({self.current}/{self.total})')
            sys.stdout.flush()
            self.last_percent = percent
            if self.current >= self.total:
                sys.stdout.write('\n')

def magic_to_logic(magic, root_type='predicate'):
    if not magic: return ""
    res = magic.strip()

    # If it has multiple lines, comments or typical JS keywords, it's a Magic block {! ... !}
    if "\n" in res or "//" in res or "/*" in res or "var " in res or "let " in res or "const " in res or "if (" in res or "{" in res:
        return f"{{!\n{res}\n!}}"

    # Attempt to reverse common Logic -> Magic transformations
    if res.startswith("return "): res = res[7:]
    if res.endswith(";"): res = res[:-1]

    # Q['var'] -> var
    res = re.sub(r"Q\['(.*?)'\]", r"\1", res)
    res = re.sub(r"Q\.([a-zA-Z0-9_]+)", r"\1", res)
    # state.visits['scene'] -> @scene
    res = re.sub(r"state\.visits\['(.*?)'\]", r"@\1", res)
    # (var || 0) -> var
    res = re.sub(r"\(?([\w@\.]+)\s*\|\|\s*0\)?", r"\1", res)

    if root_type in ['predicate', 'expression']:
        res = res.replace("===", "=").replace("==", "=")
        res = res.replace("!==", "!=").replace("!=", "!=")
        res = res.replace(" && ", " and ").replace(" || ", " or ")
        # Remove redundant parens
        while True:
            new_res = re.sub(r"\(([\w@\.\-]+)\)", r"\1", res)
            if new_res == res: break
            res = new_res

    if root_type == 'actions':
        # Reverse Q['v'] = (Q['v'] || 0) + val; -> v += val;
        res = re.sub(r"(\w+)\s*=\s*\1\s*([\+\-\*\/])\s*(.*?)$", r"\1 \2= \3", res)
        # Reverse Q['v'] = val; -> v = val;
        if "=" not in res:
             res = re.sub(r"(\w+)\s*=\s*(.*?)$", r"\1 = \2", res)

    return res.strip()

def reconstruct_content(content_obj, state_deps):
    if content_obj is None: return ""
    if isinstance(content_obj, str): return content_obj
    if isinstance(content_obj, list):
        return "".join(reconstruct_content(item, state_deps) for item in content_obj)

    if isinstance(content_obj, dict):
        ctype = content_obj.get("type")
        inner = content_obj.get("content", "")
        if ctype == "paragraph": return reconstruct_content(inner, state_deps) + "\n\n"
        if ctype == "heading": return "= " + reconstruct_content(inner, state_deps).strip() + "\n\n"
        if ctype == "emphasis-1": return "*" + reconstruct_content(inner, state_deps) + "*"
        if ctype == "emphasis-2": return "**" + reconstruct_content(inner, state_deps) + "**"
        if ctype == "emphasis-3": return "***" + reconstruct_content(inner, state_deps) + "***"
        if ctype == "line-break": return "\n"
        if ctype == "blockquote": return "> " + reconstruct_content(inner, state_deps)
        if ctype == "hrule": return "---"
        if ctype == "conditional":
            idx = content_obj.get('predicate')
            logic = "UNKNOWN"
            if state_deps and idx < len(state_deps):
                logic = magic_to_logic(state_deps[idx].get("fn", {}).get("$code", ""), "predicate")
            return f"[? if {logic} : {reconstruct_content(inner, state_deps).strip()} ?]"
        if ctype == "insert":
            idx = content_obj.get('insert')
            logic = "UNKNOWN"
            if state_deps and idx < len(state_deps):
                logic = magic_to_logic(state_deps[idx].get("fn", {}).get("$code", ""), "expression")
            return f"[? {logic} ?]"
    return str(content_obj)

SCENE_PROPS = [
    ("title", "title"), ("subtitle", "subtitle"), ("unavailableSubtitle", "unavailable-subtitle"),
    ("viewIf", "view-if"), ("chooseIf", "choose-if"),
    ("onArrival", "on-arrival"), ("onDeparture", "on-departure"), ("onDisplay", "on-display"),
    ("maxVisits", "max-visits"), ("countVisitsMax", "count-visits-max"), ("maxVisitsVar", "max-visits-var"),
    ("priority", "priority"), ("tags", "tags"), ("newPage", "new-page"),
    ("setRoot", "set-root"), ("isSpecial", "is-special"), ("gameOver", "game-over"),
    ("goTo", "go-to"), ("goSub", "go-sub"), ("setJump", "set-jump"), ("call", "call"),
    ("setBg", "set-bg"), ("audio", "audio"), ("faceImage", "face-image"),
    ("cardImage", "card-image"), ("wideImage", "wide-image"), ("bannerImage", "banner-image"),
    ("isDeck", "is-deck"), ("isCard", "is-card"), ("isHand", "is-hand"), ("isPinnedCard", "is-pinned-card"),
    ("maxCards", "max-cards"), ("checkQuality", "check-quality"), ("broadDifficulty", "broad-difficulty"),
    ("narrowDifficulty", "narrow-difficulty"), ("difficultyScaler", "difficulty-scaler"),
    ("difficultyIncrement", "difficulty-increment"), ("checkSuccessGoTo", "check-success-go-to"),
    ("checkFailureGoTo", "check-failure-go-to"), ("minChoices", "min-choices"), ("maxChoices", "max-choices"),
    ("isTop", "is-top"), ("setSprites", "set-sprites"), ("setSpriteStyles", "set-sprite-styles"),
    ("setTopLeftStyle", "set-top-left-style"), ("setTopRightStyle", "set-top-right-style"),
    ("setBottomLeftStyle", "set-bottom-left-style"), ("setBottomRightStyle", "set-bottom-right-style")
]

def decompile_scene_heuristic(scene, root_id):
    lines = []
    is_root = scene['id'] == root_id
    if not is_root:
        sid = scene['id']
        if sid.startswith(root_id + "."): sid = "@" + sid[len(root_id)+1:]
        lines.append(sid)

    handled = {"id", "content", "options", "$metadata", "type", "stateDependencies"}

    for js_p, dry_p in SCENE_PROPS:
        if js_p in scene:
            handled.add(js_p)
            val = scene[js_p]
            if val is None: continue
            if js_p == "countVisitsMax" and scene.get("maxVisits") == val: continue

            if js_p in ["subtitle", "unavailableSubtitle"] and isinstance(val, dict):
                lines.append(f"{dry_p}: {reconstruct_content(val.get('content'), val.get('stateDependencies')).strip()}")
            elif js_p in ["viewIf", "chooseIf", "maxVisitsVar"]:
                lines.append(f"{dry_p}: {magic_to_logic(val.get('$code') if isinstance(val, dict) else '', 'predicate')}")
            elif js_p in ["onArrival", "onDeparture", "onDisplay"]:
                actions = [magic_to_logic(act.get("$code", ""), "actions") for act in val]
                if any("{!" in a for a in actions):
                    lines.append(f"{dry_p}: " + "\n".join(actions))
                else:
                    lines.append(f"{dry_p}: {'; '.join(actions)}")
            elif js_p == "tags":
                lines.append(f"{dry_p}: {', '.join(val) if isinstance(val, list) else val}")
            elif js_p in ["goTo", "goSub"]:
                parts = []
                for item in val:
                    p = item["id"]
                    if "predicate" in item: p += f" if {magic_to_logic(item['predicate'].get('$code', ''), 'predicate')}"
                    parts.append(p)
                lines.append(f"{dry_p}: {'; '.join(parts)}")
            else:
                lines.append(f"{dry_p}: {str(val).lower() if isinstance(val, bool) else val}")

    for k, v in scene.items():
        if k not in handled and not k.startswith('$'):
            lines.append(f"{k}: {v}")

    cont = scene.get("content")
    if cont:
        text = ""
        if isinstance(cont, dict): text = reconstruct_content(cont.get("content"), cont.get("stateDependencies") or scene.get("stateDependencies")).strip()
        else: text = str(cont).strip()
        if text:
            lines.append("")
            lines.append(text)

    if "options" in scene:
        lines.append("")
        for opt in scene["options"]:
            oid, otit = opt['id'], opt.get('title', '')
            if oid.startswith(root_id + "."): oid = "@" + oid[len(root_id)+1:]
            line = f"- {oid}"
            if otit:
                t = reconstruct_content(opt['title'].get('content') if isinstance(opt['title'], dict) else opt['title'],
                                        opt['title'].get('stateDependencies') if isinstance(opt['title'], dict) else None).strip()
                line += f": {t}"
            lines.append(line)
            for k, v in opt.items():
                if k in ['id', 'title']: continue
                dKey = k.replace('ViewIf', 'view-if').replace('ChooseIf', 'choose-if') # Simplified for options
                lines.append(f"  {dKey}: {magic_to_logic(v['$code'], 'predicate') if isinstance(v, dict) and '$code' in v else v}")

    return "\n".join(lines)

def decompile(input_path, output_dir):
    try:
        if input_path.startswith("http"):
            logger.info(f"Downloading core.js from {input_path}...")
            r = requests.get(input_path); r.raise_for_status(); content = r.text
        else:
            logger.info(f"Loading core.js from {input_path}...")
            with open(input_path, 'r', encoding='utf-8') as f: content = f.read()
    except Exception as e:
        logger.error(f"Failed to load input: {e}"); return

    prefix = "window.game="
    start_idx = content.find(prefix)
    json_part = content[start_idx + len(prefix):] if start_idx != -1 else content.strip()
    if json_part.endswith(';'): json_part = json_part[:-1]

    try:
        decoder = json.JSONDecoder()
        game_wrapper, _ = decoder.raw_decode(json_part)
        game = json.loads(game_wrapper['compiled']) if isinstance(game_wrapper, dict) and 'compiled' in game_wrapper else game_wrapper
    except Exception as e:
        logger.error(f"JSON Parse error: {e}"); return

    if os.path.exists(output_dir): shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    # 1. Metadata restoration (if available)
    files_metadata = {}
    def find_metadata(obj):
        if isinstance(obj, dict):
            if '$metadata' in obj and '$raw' in obj['$metadata']:
                files_metadata[obj['$metadata']['$file']] = obj['$metadata']['$raw']
            for v in obj.values(): find_metadata(v)
        elif isinstance(obj, list):
            for i in obj: find_metadata(i)
    find_metadata(game)

    if files_metadata:
        logger.info(f"Found source metadata. Restoring 1:1 original files.")
        prog = Progress(len(files_metadata), "Restoring Source")
        for fp, raw in files_metadata.items():
            rel = fp.split('source/', 1)[1] if 'source/' in fp else os.path.basename(fp)
            out = os.path.join(output_dir, rel); os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'w', encoding='utf-8', newline='') as f: f.write(raw)
            prog.update()
        logger.info("SUCCESS: 1:1 Restoration Complete.")
        return

    # 2. Heuristic reconstruction
    logger.warning("Source metadata missing. Reconstructing from JSON (functional 1:1).")
    with open(os.path.join(output_dir, "info.dry"), 'w', encoding='utf-8') as f:
        f.write(f"title: {game.get('title', '')}\nauthor: {game.get('author', '')}\nifid: {game.get('ifid', '')}\n")

    qds = game.get("qdisplays", {})
    if qds:
        os.makedirs(os.path.join(output_dir, "qdisplays"), exist_ok=True)
        for qid, qd in qds.items():
            lines = [""] # Blank line at start
            if qd.get("content"):
                for i in qd["content"]:
                    lines.append(f"({i.get('min', '')}..{i.get('max', '')}) {reconstruct_content(i.get('output'), None, True)}")
            with open(os.path.join(output_dir, "qdisplays", f"{qid}.qdisplay.dry"), 'w', encoding='utf-8') as f:
                f.write("\n".join(lines))

    scenes = game.get("scenes", {})
    prog = Progress(len(scenes), "Reconstructing Scenes")
    groups = {}
    internal_ids = ['prevScene', 'prevTopScene', 'jumpScene', 'backSpecialScene', 'returnScene']
    for sid, scene in scenes.items():
        if sid in internal_ids: continue
        bid = sid.split('.')[0]
        if bid not in groups: groups[bid] = []
        groups[bid].append(scene)

    for bid, sub in groups.items():
        sub.sort(key=lambda s: (s['id'] != bid, s['id']))
        blocks = []
        for i, s in enumerate(sub):
            recon = decompile_scene_heuristic(s, bid)
            blocks.append(recon if i == 0 else f"\n\n" + recon)
            prog.update()
        out = os.path.join(output_dir, "scenes", f"{bid}.scene.dry")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f: f.write("\n".join(blocks) + "\n")

    logger.info(f"SUCCESS: Heuristic reconstruction complete ({len(scenes)} scenes processed).")

if __name__ == "__main__":
    if len(sys.argv) < 2: print("Usage: python3 decompiler.py <core.js_OR_URL> [output_dir]")
    else: decompile(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "decompiled_source")
