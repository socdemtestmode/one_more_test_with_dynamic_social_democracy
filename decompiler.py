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

def camel_to_kebab(s):
    return re.sub(r'([a-z0-9])([A-Z])', r'\1-\2', s).lower()

scene_property_map = {
    'onArrival': 'on-arrival', 'onDeparture': 'on-departure', 'onDisplay': 'on-display',
    'viewIf': 'view-if', 'chooseIf': 'choose-if', 'maxVisits': 'max-visits',
    'countVisitsMax': 'count-visits-max', 'maxVisitsVar': 'max-visits-var',
    'newPage': 'new-page', 'setRoot': 'set-root', 'gameOver': 'game-over',
    'isSpecial': 'is-special', 'setJump': 'set-jump', 'goTo': 'go-to',
    'goSub': 'go-sub', 'goSubStart': 'go-sub-start', 'goSubEnd': 'go-sub-end',
    'setBg': 'set-bg', 'setMusic': 'set-music', 'faceImage': 'face-image',
    'wideImage': 'wide-image', 'bannerImage': 'banner-image', 'cardImage': 'card-image',
    'isDeck': 'is-deck', 'isCard': 'is-card', 'isHand': 'is-hand',
    'isPinnedCard': 'is-pinned-card', 'maxCards': 'max-cards', 'checkQuality': 'check-quality',
    'broadDifficulty': 'broad-difficulty', 'narrowDifficulty': 'narrow-difficulty',
    'difficultyScaler': 'difficulty-scaler', 'difficultyIncrement': 'difficulty-increment',
    'checkSuccessGoTo': 'check-success-go-to', 'checkFailureGoTo': 'check-failure-go-to',
    'minChoices': 'min-choices', 'maxChoices': 'max-choices', 'isTop': 'is-top',
    'setSprites': 'set-sprites', 'setSpriteStyles': 'set-sprite-styles',
    'setTopLeftStyle': 'set-top-left-style', 'setTopRightStyle': 'set-top-right-style',
    'setBottomLeftStyle': 'set-bottom-left-style', 'setBottomRightStyle': 'set-bottom-right-style',
    'unavailableSubtitle': 'unavailable-subtitle'
}

def decompile_logic(code, is_predicate=True):
    if not isinstance(code, str): return str(code)

    if is_predicate:
        logic = code[7:] if code.startswith('return ') else code
        if logic.endswith(';'): logic = logic[:-1]
        logic = re.sub(r"\(?\s*Q\[['\"]([^'\"]+)['\"]\s*\]\s*\|\|\s*0\s*\)?", r"\1", logic)
        logic = re.sub(r"\(?\s*Q\.([a-zA-Z0-9_]+)\s*\|\|\s*0\s*\)?", r"\1", logic)
        logic = re.sub(r"Q\[['\"]([^'\"]+)['\"]\s*\]", r"\1", logic)
        logic = re.sub(r"Q\.([a-zA-Z0-9_]+)", r"\1", logic)
        logic = re.sub(r"state\.visits\[['\"]([^'\"]+)['\"]\s*\]", r"@\1", logic)
        logic = re.sub(r"\(?\s*([a-zA-Z0-9_@]+)\s*\|\|\s*0\s*\)?", r"\1", logic)
        logic = re.sub(r"\(?\s*\(\s*([a-zA-Z0-9_@]+)\s*\)\s*!==\s*0\s*\)?", r'\1', logic)
        logic = re.sub(r"\(?\s*([a-zA-Z0-9_@]+)\s*!==\s*0\s*\)?", r'\1', logic)
        logic = re.sub(r"\s*!==\s*0(?=[^0-9]|$)", '', logic)
        logic = logic.replace("===", " = ").replace("==", " = ")
        logic = logic.replace("!==", " != ").replace("!=", " != ")
        logic = logic.replace("&&", " and ").replace("||", " or ")
        logic = re.sub(r"!(?![a-zA-Z0-9_])", "not ", logic)
        logic = re.sub(r"\s+", " ", logic).strip()
        changed = True
        while changed:
            changed = False
            if logic.startswith('(') and logic.endswith(')'):
                count, balanced = 0, True
                for i in range(len(logic) - 1):
                    if logic[i] == '(': count += 1
                    elif logic[i] == ')': count -= 1
                    if count == 0 and i > 0: balanced = False; break
                if balanced: logic = logic[1:-1]; changed = True
            next_l = re.sub(r"\(\s*([a-zA-Z_@0-9\.\s'\"]+(?:=|!=|<|>|<=|>=)[a-zA-Z_@0-9\.\s'\"]+)\s*\)", r"\1", logic)
            if next_l != logic: logic = next_l; changed = True
            next_l = re.sub(r"\(\s*([a-zA-Z_@0-9\.]+)\s*\)", r"\1", logic)
            if next_l != logic: logic = next_l; changed = True
            next_l = re.sub(r"\(\s*(not\s+[a-zA-Z_@0-9\.]+)\s*\)", r"\1", logic)
            if next_l != logic: logic = next_l; changed = True
        return logic.strip()
    else:
        if "\n" in code or "//" in code or "/*" in code or "if (" in code or "{" in code: return code
        logic = code.replace("\n", "; ")
        logic = re.sub(r"\(?\s*Q\[['\"]([^'\"]+)['\"]\s*\]\s*\|\|\s*0\s*\)?", r"\1", logic)
        logic = re.sub(r"Q\[['\"]([^'\"]+)['\"]\s*\]", r"\1", logic)
        logic = re.sub(r"Q\.([a-zA-Z0-9_]+)", r"\1", logic)
        logic = re.sub(r"state\.visits\[['\"]([^'\"]+)['\"]\s*\]", r"@\1", logic)
        logic = re.sub(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*\1\s*([\+\-\*\/])\s*([^;]+)", r"\1 \2= \3", logic)
        logic = re.sub(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([^;]+)", r"\1 = \2", logic)
        lines = [s.strip() for s in logic.split(';') if s.strip()]
        return "; ".join(lines)

def reconstruct_content(content, state_deps, is_one_line=False):
    if content is None: return ""
    if isinstance(content, str): return content
    if isinstance(content, list):
        parts = [reconstruct_content(item, state_deps, is_one_line) for item in content]
        is_top_level = any(isinstance(c, dict) and c.get('type') in ['paragraph', 'heading', 'quotation', 'attribution', 'hrule'] for c in content)
        if is_top_level and not is_one_line: return "\n\n".join(filter(None, [p.strip() for p in parts]))
        return "".join(parts)
    local_deps = content.get('stateDependencies') or state_deps
    ctype, inner = content.get('type'), content.get('content', '')
    if ctype == 'paragraph': return reconstruct_content(inner, local_deps, is_one_line)
    if ctype == 'heading': return "= " + reconstruct_content(inner, local_deps, is_one_line).strip()
    if ctype == 'quotation': return "> " + reconstruct_content(inner, local_deps, is_one_line)
    if ctype == 'attribution': return ">> " + reconstruct_content(inner, local_deps, is_one_line)
    if ctype in ['emphasis-1', 'emphasis-2', 'emphasis-3']:
        m = {'emphasis-1': '*', 'emphasis-2': '**', 'emphasis-3': '***'}[ctype]
        return f"{m}{reconstruct_content(inner, local_deps, True)}{m}"
    if ctype == 'line-break': return "//\n"
    if ctype == 'hrule': return "---"
    if ctype == 'conditional':
        idx = content.get('predicate')
        cond = "UNKNOWN"
        if local_deps and idx is not None and idx < len(local_deps):
            cond = decompile_logic(local_deps[idx].get('fn', {}).get('$code', ''), True)
        return f"[? if {cond} : {reconstruct_content(inner, local_deps, True).strip()} ?]"
    if ctype == 'insert':
        idx = content.get('insert')
        ins, qd = "UNKNOWN", ""
        if local_deps and idx is not None and idx < len(local_deps):
            dep = local_deps[idx]
            ins = decompile_logic(dep.get('fn', {}).get('$code', ''), False)
            if dep.get('qdisplay'): qd = f" : {dep['qdisplay']}"
        return f"[+ {ins}{qd} +]"
    if ctype == 'magic': return f"{{! {inner} !}}"
    if ctype == 'hidden': return f"[{reconstruct_content(inner, local_deps, True)}]"
    return reconstruct_content(inner, local_deps, is_one_line) if inner else ""

def decompile_scene_heuristic(scene, root_id):
    lines = []
    if scene['id'] != root_id:
        sid = scene['id']
        if sid.startswith(root_id + "."): sid = "@" + sid[len(root_id)+1:]
        lines.append(sid)
    handled = {"id", "content", "options", "$metadata", "type", "stateDependencies"}
    state_deps = scene.get("stateDependencies") or (scene.get("content", {}).get("stateDependencies") if isinstance(scene.get("content"), dict) else None)
    for js_p, dry_p in scene_property_map.items():
        if js_p in scene:
            handled.add(js_p); val = scene[js_p]
            if val is None: continue
            if js_p == "countVisitsMax" and scene.get("maxVisits") == val: continue
            if js_p in ["subtitle", "unavailableSubtitle"] and isinstance(val, dict):
                lines.append(f"{dry_p}: {reconstruct_content(val.get('content'), val.get('stateDependencies'), True).strip()}")
            elif js_p in ["onArrival", "onDeparture", "onDisplay"]:
                code = "\n".join(a.get("$code", "") for a in val).strip()
                decomp = decompile_logic(code, False)
                if "\n" in decomp or "//" in decomp or "/*" in decomp or "{" in decomp:
                    lines.append(f"{dry_p}: {{!\n{decomp}\n!}}")
                elif decomp: lines.append(f"{dry_p}: {decomp}")
            elif js_p in ["goTo", "goSub", "goSubStart", "goSubEnd"]:
                parts = [v["id"] + (f" if {decompile_logic(v['predicate'].get('$code', ''), True)}" if "predicate" in v else "") for v in val]
                lines.append(f"{dry_p}: {'; '.join(parts)}")
            elif js_p == "tags": lines.append(f"{dry_p}: {', '.join(val) if isinstance(val, list) else val}")
            elif js_p == 'setSprites':
                lines.append(f"{dry_p}: {', '.join(f'{p[0]}: {p[1]}' for p in val) if isinstance(val, list) else val}")
            elif isinstance(val, dict) and "$code" in val:
                lines.append(f"{dry_p}: {decompile_logic(val['$code'], True)}")
            else: lines.append(f"{dry_p}: {str(val).lower() if isinstance(val, bool) else val}")
    for k, v in scene.items():
        if k not in handled and not k.startswith('$'): lines.append(f"{camel_to_kebab(k)}: {v}")
    cont = scene.get("content")
    if cont:
        text = reconstruct_content(cont.get("content"), cont.get("stateDependencies") or state_deps).strip() if isinstance(cont, dict) else str(cont).strip()
        if text: lines.append(""); lines.append(text)
    if "options" in scene:
        lines.append("")
        for opt in scene["options"]:
            oid = opt['id']
            if oid.startswith(root_id + "."): oid = "@" + oid[len(root_id)+1:]
            elif not oid.startswith("@"): oid = "@" + oid
            line = f"- {oid}"
            if opt.get('title'):
                title_obj = opt['title']
                t = reconstruct_content(title_obj.get('content') if isinstance(title_obj, dict) else title_obj, title_obj.get('stateDependencies') if isinstance(title_obj, dict) else state_deps, True).strip()
                if t: line += f": {t}"
            lines.append(line)
            for k, v in opt.items():
                if k in ['id', 'title']: continue
                dKey = scene_property_map.get(k, camel_to_kebab(k))
                lines.append(f"  {dKey}: {decompile_logic(v['$code'], True) if isinstance(v, dict) and '$code' in v else v}")
    return "\n".join(lines)

def decompile(input_path, output_dir):
    try:
        if input_path.startswith("http"): content = requests.get(input_path).text
        else:
            with open(input_path, 'r', encoding='utf-8') as f: content = f.read()
    except Exception as e: logger.error(f"Failed to load: {e}"); return
    prefix = "window.game="
    idx = content.find(prefix)
    if idx == -1: idx = 0
    else: idx += len(prefix)
    json_end = content.rfind("};(function")
    json_src = content[idx:json_end+1] if json_end != -1 else content[idx:].strip().rstrip(';')
    try:
        wrapper = json.loads(json_src)
        game = json.loads(wrapper['compiled']) if isinstance(wrapper, dict) and 'compiled' in wrapper else wrapper
    except Exception as e:
        try:
            decoder = json.JSONDecoder()
            wrapper, _ = decoder.raw_decode(content[idx:].strip())
            game = json.loads(wrapper['compiled']) if isinstance(wrapper, dict) and 'compiled' in wrapper else wrapper
        except Exception as e2: logger.error(f"JSON Parse error: {e2}"); return
    if os.path.exists(output_dir): shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    files_metadata = {}
    def find_metadata(obj):
        if isinstance(obj, dict):
            if '$metadata' in obj and '$raw' in obj['$metadata']:
                files_metadata[obj['$metadata']['$file']] = obj['$metadata']['$raw']
            for v in obj.values(): find_metadata(v)
        elif isinstance(obj, list): [find_metadata(i) for i in obj]
    find_metadata(game)

    if files_metadata:
        logger.info(f"Found source metadata ({len(files_metadata)} files). Restoring 1:1 original files.")
        for fp, raw in tqdm(files_metadata.items(), desc="Restoring"):
            rel = fp.split('source/', 1)[1] if 'source/' in fp else fp
            out = os.path.join(output_dir, rel); os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'w', encoding='utf-8', newline='') as f: f.write(raw)
        logger.info("SUCCESS: 1:1 Restoration Complete.")
        return

    logger.warning("No metadata. Using heuristic reconstruction.")
    with open(os.path.join(output_dir, "info.dry"), 'w', encoding='utf-8') as f:
        f.write("\n".join(f"{k}: {game.get(k, '')}" for k in ['title', 'author', 'ifid']) + "\n")
        for k, v in game.items():
            if k not in ['scenes', 'qualities', 'qdisplays', 'tagLookup', 'content', 'title', 'author', 'ifid'] and isinstance(v, (str, int, bool)):
                f.write(f"{camel_to_kebab(k)}: {v}\n")
    for section in ["qdisplays", "qualities"]:
        data = game.get(section, {})
        if data:
            os.makedirs(os.path.join(output_dir, section), exist_ok=True)
            for qid, d in data.items():
                if section == "qdisplays":
                    lines = [""] + [f"({i.get('min', '')}..{i.get('max', '')}) {reconstruct_content(i.get('output'), None, True)}" for i in d.get("content", [])]
                else: lines = [f"{camel_to_kebab(k)}: {v}" for k, v in d.items() if k != 'id']
                with open(os.path.join(output_dir, section, f"{qid}.{section[:-1]}.dry"), 'w', encoding='utf-8') as f: f.write("\n".join(lines) + "\n")
    scenes = game.get("scenes", {})
    groups, internal_ids = {}, ['prevScene', 'prevTopScene', 'jumpScene', 'backSpecialScene', 'returnScene']
    for sid, scene in scenes.items():
        if sid in internal_ids: continue
        bid = sid.split('.')[0]
        if bid not in groups: groups[bid] = []
        groups[bid].append(scene)
    for bid, sub in tqdm(groups.items(), desc="Reconstructing"):
        sub.sort(key=lambda s: (s['id'] != bid, s['id']))
        blocks = [decompile_scene_heuristic(s, bid) for s in sub]
        out = os.path.join(output_dir, "scenes", f"{bid}.scene.dry"); os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f: f.write("\n\n".join(blocks) + "\n")
    logger.info("SUCCESS.")

if __name__ == "__main__":
    from tqdm import tqdm
    if len(sys.argv) < 2: print("Usage: python3 decompiler.py <core.js_OR_URL> [output_dir]")
    else: decompile(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "decompiled_source")
