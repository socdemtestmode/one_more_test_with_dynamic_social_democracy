import os
import subprocess
import sys
import tempfile
import shutil

# Embedded JS decompiler logic for standalone use
DECOMPILER_JS = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function extractJsonFromCoreJs(coreJsPath) {
    const content = fs.readFileSync(coreJsPath, 'utf8');
    const marker = "window.game={compiled:";
    const startIdx = content.indexOf(marker);
    if (startIdx === -1) {
        try { return JSON.parse(content); } catch(e) {
            throw new Error("Could not find window.game={compiled: in " + coreJsPath);
        }
    }
    const afterMarker = content.substring(startIdx + marker.length).trim();
    const quoteChar = afterMarker[0];
    let jsonStrRaw = "";
    let escaped = false;
    let endIdx = -1;
    for (let i = 1; i < afterMarker.length; i++) {
        const c = afterMarker[i];
        if (escaped) { escaped = false; }
        else if (c === '\\') { escaped = true; }
        else if (c === quoteChar) { endIdx = i; break; }
    }
    if (endIdx === -1) throw new Error("Could not find end of compiled string");
    jsonStrRaw = afterMarker.substring(1, endIdx);
    const sandbox = { result: null };
    vm.createContext(sandbox);
    vm.runInContext(`result = ${quoteChar}${jsonStrRaw}${quoteChar}`, sandbox);
    try { return JSON.parse(sandbox.result); } catch (e) {
        throw new Error("Failed to parse game JSON: " + e.message);
    }
}

function camelToKebab(str) { return str.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase(); }

const scenePropertyMap = {
    onArrival: 'on-arrival', onDeparture: 'on-departure', onDisplay: 'on-display',
    viewIf: 'view-if', chooseIf: 'choose-if', maxVisits: 'max-visits',
    countVisitsMax: 'count-visits-max', maxVisitsVar: 'max-visits-var',
    newPage: 'new-page', setRoot: 'set-root', gameOver: 'game-over',
    isSpecial: 'is-special', setJump: 'set-jump', goTo: 'go-to',
    goSub: 'go-sub', goSubStart: 'go-sub-start', goSubEnd: 'go-sub-end',
    setBg: 'set-bg', setMusic: 'set-music', faceImage: 'face-image',
    wideImage: 'wide-image', bannerImage: 'banner-image', cardImage: 'card-image',
    isDeck: 'is-deck', isCard: 'is-card', isHand: 'is-hand', isPinnedCard: 'is-pinned-card',
    maxCards: 'max-cards', checkQuality: 'check-quality',
    broadDifficulty: 'broad-difficulty', narrowDifficulty: 'narrow-difficulty',
    difficultyScaler: 'difficulty-scaler', difficultyIncrement: 'difficulty-increment',
    checkSuccessGoTo: 'check-success-go-to', checkFailureGoTo: 'check-failure-go-to',
    minChoices: 'min-choices', maxChoices: 'max-choices', isTop: 'is-top',
    setSprites: 'set-sprites', setSpriteStyles: 'set-sprite-styles',
    setTopLeftStyle: 'set-top-left-style', setTopRightStyle: 'set-top-right-style',
    setBottomLeftStyle: 'set-bottom-left-style', setBottomRightStyle: 'set-bottom-right-style',
    unavailableSubtitle: 'unavailable-subtitle'
};

function decompileLogic(code, isPredicate = true) {
    if (typeof code !== 'string') return code;
    let logic = code.startsWith('return ') ? code.substring(7).replace(/;$/, '') : code;
    logic = logic.replace(/\s*!==\s*0/g, "");
    logic = logic.replace(/\s*\|\|\s*0/g, "");
    logic = logic.replace(/Q\[\x27(.*?)\x27\]/g, "$1");
    logic = logic.replace(/Q\["(.*?)"\]/g, "$1");
    logic = logic.replace(/Q\.(.*?)(?=[^a-zA-Z0-9_]|$)/g, "$1");
    logic = logic.replace(/this\.state\.visits/g, "state.visits");
    logic = logic.replace(/state\.visits\[\x27(.*?)\x27\]/g, "@$1");
    logic = logic.replace(/state\.visits\["(.*?)"\]/g, "@$1");
    if (isPredicate) {
        logic = logic.replace(/===/g, "=").replace(/==/g, "=");
        logic = logic.replace(/&&/g, " and ").replace(/\|\|/g, " or ").replace(/!/g, " not ");
        let changed = true;
        while (changed) {
            changed = false; let next = logic;
            next = next.replace(/\(\s*([a-zA-Z_@0-9.]+)\s*\)/g, "$1");
            next = next.replace(/\(\s*([a-zA-Z_@0-9.]+\s*(=|!=|>|<|>=|<=)\s*[a-zA-Z_@0-9.]+)\s*\)/g, "$1");
            next = next.replace(/\(\s*\(([^()]+?)\)\s*\)/g, "($1)");
            next = next.replace(/\(\s*([^()]+? and [^()]+?)\s*\)\s*and/g, "$1 and");
            next = next.replace(/and\s*\(\s*([^()]+? and [^()]+?)\s*\)/g, "and $1");
            next = next.replace(/\(\s*([^()]+? or [^()]+?)\s*\)\s*or/g, "$1 or");
            next = next.replace(/or\s*\(\s*([^()]+? or [^()]+?)\s*\)/g, "or $1");
            if (next !== logic) { logic = next; changed = true; }
        }
        if (logic.startsWith('(') && logic.endsWith(')')) {
            let inner = logic.substring(1, logic.length - 1), balance = 0, ok = true;
            for (let i = 0; i < inner.length; i++) {
                if (inner[i] === '(') balance++; else if (inner[i] === ')') balance--;
                if (balance < 0) { ok = false; break; }
            }
            if (ok && balance === 0) logic = inner;
        }
        return logic.replace(/\s+/g, " ").trim();
    }
    logic = logic.replace(/([a-zA-Z_@0-9.]+)\s*=\s*\(\s*\1\s*\|\|\s*0\s*\)\s*\+\s*(.*?)(;|$)/g, "$1 += $2$3");
    logic = logic.replace(/([a-zA-Z_@0-9.]+)\s*=\s*\(\s*\1\s*\|\|\s*0\s*\)\s*-\s*(.*?)(;|$)/g, "$1 -= $2$3");
    return logic.trim();
}

function decompileContent(content, stateDependencies, isOneLine = false) {
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
        let parts = content.map(c => decompileContent(c, stateDependencies, isOneLine));
        let isTopLevel = content.some(c => c && ['paragraph', 'heading', 'quotation', 'attribution', 'hrule'].includes(c.type));
        return (isTopLevel && !isOneLine) ? parts.join('\n\n') : parts.join('');
    }
    if (!content) return "";
    let localDeps = content.stateDependencies || stateDependencies;
    let result = "";
    if (content.type) {
        switch (content.type) {
            case 'paragraph': result = decompileContent(content.content, localDeps, isOneLine); break;
            case 'heading': result = "= " + decompileContent(content.content, localDeps, isOneLine); break;
            case 'quotation': result = "> " + decompileContent(content.content, localDeps, isOneLine); break;
            case 'attribution': result = ">> " + decompileContent(content.content, localDeps, isOneLine); break;
            case 'emphasis-1': result = "*" + decompileContent(content.content, localDeps, true) + "*"; break;
            case 'emphasis-2': result = "**" + decompileContent(content.content, localDeps, true) + "**"; break;
            case 'emphasis-3': result = "`" + decompileContent(content.content, localDeps, true) + "`"; break;
            case 'line-break': result = "//\n"; break;
            case 'hrule': result = "---"; break;
            case 'conditional':
                let condText = "UNKNOWN";
                if (localDeps && localDeps[content.predicate] !== undefined) {
                     let dep = localDeps[content.predicate];
                     if (dep.fn && dep.fn.$code) condText = decompileLogic(dep.fn.$code);
                }
                result = `[? if ${condText} : ${decompileContent(content.content, localDeps, true)} ?]`;
                break;
            case 'insert':
                let insText = "UNKNOWN", qd = "";
                if (localDeps && localDeps[content.insert] !== undefined) {
                     let dep = localDeps[content.insert];
                     if (dep.fn && dep.fn.$code) insText = decompileLogic(dep.fn.$code);
                     if (dep.qdisplay) qd = " : " + dep.qdisplay;
                }
                result = `[+ ${insText}${qd} +]`;
                break;
            case 'magic': result = `{! ${content.content} !}`; break;
            case 'hidden': result = `[${decompileContent(content.content, localDeps, true)}]`; break;
            default: if (content.content) result = decompileContent(content.content, localDeps, isOneLine);
        }
    } else if (content.content) result = decompileContent(content.content, localDeps, isOneLine);
    return result;
}

function decompileScene(scene, rootId) {
    let lines = [];
    const isRoot = scene.id === rootId;
    if (!isRoot) {
        let shortId = scene.id.startsWith(rootId + ".") ? scene.id.substring(rootId.length + 1) : scene.id;
        lines.push(`@${shortId}`);
    }
    let stateDeps = scene.stateDependencies || (scene.content && scene.content.stateDependencies);
    for (let key in scene) {
        if (['id', 'type', 'content', 'options', 'stateDependencies'].includes(key)) continue;
        let value = scene[key];
        if (value === undefined || value === null) continue;
        if (key === 'countVisitsMax' && scene.maxVisits !== undefined && value === scene.maxVisits) continue;
        let dryKey = scenePropertyMap[key] || camelToKebab(key);
        if (['onArrival', 'onDeparture', 'onDisplay'].includes(key)) {
            let actions = value.map(a => a.$code).join('\n').trim();
            let decompiled = decompileLogic(actions, false);
            const isComplex = decompiled.includes('\n') || decompiled.includes(';') || decompiled.includes('{') || decompiled.includes('Q.') || decompiled.includes('this.');
            if (isComplex) {
                lines.push(`${dryKey}: {!`);
                lines.push(decompiled);
                lines.push(`!}`);
            } else if (decompiled) lines.push(`${dryKey}: ${decompiled}`);
        } else if (['goTo', 'goSub', 'goSubStart', 'goSubEnd'].includes(key)) {
            if (Array.isArray(value)) {
                let parts = value.map(v => {
                    let sid = v.id.startsWith(rootId + ".") ? "@" + v.id.substring(rootId.length + 1) : v.id;
                    return sid + (v.predicate ? ` if ${decompileLogic(v.predicate.$code)}` : "");
                });
                lines.push(`${dryKey}: ${parts.join('; ')}`);
            } else {
                let sid = value.startsWith(rootId + ".") ? "@" + value.substring(rootId.length + 1) : value;
                lines.push(`${dryKey}: ${sid}`);
            }
        } else if (key === 'tags') {
            lines.push(`${dryKey}: ${Array.isArray(value) ? value.join(', ') : value}`);
        } else if (value && value.$code) {
            lines.push(`${dryKey}: ${decompileLogic(value.$code)}`);
        } else if (typeof value === 'object' && !Array.isArray(value)) {
            lines.push(`${dryKey}: ${decompileContent(value, value.stateDependencies || stateDeps, true)}`);
        } else if (key === 'setSprites') {
            lines.push(`${dryKey}: ${Array.isArray(value) ? value.map(p => `${p[0]}: ${p[1]}`).join(', ') : value}`);
        } else lines.push(`${dryKey}: ${value}`);
    }
    if (scene.content) {
        let content = decompileContent(scene.content, stateDeps).trim();
        if (content) lines.push("", content);
    }
    if (scene.options && scene.options.length > 0) {
        lines.push("");
        scene.options.forEach(opt => {
            let sid = opt.id.startsWith(rootId + ".") ? "@" + opt.id.substring(rootId.length + 1) : opt.id;
            let line = `- ${sid}`;
            if (opt.title) {
                let t = decompileContent(opt.title, stateDeps, true).trim();
                if (t) line += `: ${t}`;
            }
            lines.push(line);
            for (let k in opt) {
                if (k === 'id' || k === 'title') continue;
                let val = opt[k];
                let dKey = scenePropertyMap[k] || camelToKebab(k);
                lines.push(`  ${dKey}: ${val && val.$code ? decompileLogic(val.$code) : val}`);
            }
        });
    }
    return lines.join('\n');
}

function runDecompiler(game, outputDir) {
    if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
    let info = "";
    const infoKeys = ['title', 'author', 'ifid'];
    infoKeys.forEach(k => { if (game[k]) info += `${k}: ${game[k]}\n`; });
    for (let key in game) {
        if (['scenes', 'qualities', 'qdisplays', 'tagLookup', 'content', 'sections'].concat(infoKeys).includes(key)) continue;
        if (['string', 'number', 'boolean'].includes(typeof game[key])) info += `${camelToKebab(key)}: ${game[key]}\n`;
    }
    fs.writeFileSync(path.join(outputDir, 'info.dry'), info);
    if (game.qdisplays) {
        const qdir = path.join(outputDir, 'qdisplays');
        if (!fs.existsSync(qdir)) fs.mkdirSync(qdir);
        for (let id in game.qdisplays) {
             let res = "", qd = game.qdisplays[id];
             if (qd.content && Array.isArray(qd.content)) {
                 qd.content.forEach(range => {
                     let r = "(" + (range.min ?? "") + ".." + (range.max ?? "") + ") ";
                     r += decompileContent(range.output, null, true);
                     res += r + "\n";
                 });
             }
             fs.writeFileSync(path.join(qdir, `${id}.qdisplay.dry`), "\n" + res.trim() + "\n");
        }
    }
    if (game.qualities) {
        const qualDir = path.join(outputDir, 'qualities');
        if (!fs.existsSync(qualDir)) fs.mkdirSync(qualDir);
        for (let id in game.qualities) {
            let q = game.qualities[id], qlines = [];
            for (let key in q) if (key !== 'id') qlines.push(`${camelToKebab(key)}: ${q[key]}`);
            fs.writeFileSync(path.join(qualDir, `${id}.quality.dry`), qlines.join('\n'));
        }
    }
    const scenesDir = path.join(outputDir, 'scenes');
    if (!fs.existsSync(scenesDir)) fs.mkdirSync(scenesDir);
    let rootScenes = {};
    const internalIds = ['prevScene', 'prevTopScene', 'jumpScene', 'backSpecialScene', 'returnScene'];
    for (let id in game.scenes) {
        if (internalIds.includes(id)) continue;
        let rootId = id.split('.')[0];
        if (!rootScenes[rootId]) rootScenes[rootId] = [];
        rootScenes[rootId].push(game.scenes[id]);
    }
    for (let rootId in rootScenes) {
        let scenes = rootScenes[rootId];
        scenes.sort((a, b) => (a.id === rootId ? -1 : (b.id === rootId ? 1 : a.id.localeCompare(b.id))));
        let dryContent = "";
        scenes.forEach((scene, index) => {
            if (index > 0) dryContent += "\n\n";
            dryContent += decompileScene(scene, rootId);
        });
        fs.writeFileSync(path.join(scenesDir, `${rootId}.scene.dry`), dryContent + "\n");
    }
}

const inputPath = process.argv[2], outputDir = process.argv[3] || 'decompiled';
const game = extractJsonFromCoreJs(inputPath);
runDecompiler(game, outputDir);
"""

def main():
    if len(sys.argv) < 2:
        print("Usage: python dendry_decompiler.py <core.js path> [output_directory]")
        return
    core_js = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "decompiled"
    with tempfile.NamedTemporaryFile(suffix='.js', mode='w', delete=False) as f:
        f.write(DECOMPILER_JS)
        temp_js = f.name
    try:
        subprocess.run(["node", temp_js, core_js, output_dir], check=True)
    finally:
        if os.path.exists(temp_js): os.remove(temp_js)

if __name__ == "__main__": main()
