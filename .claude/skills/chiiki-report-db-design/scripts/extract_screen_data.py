#!/usr/bin/env python3
"""task-app の画面データ棚卸しスクリプト。

route -> template -> service -> model をたどり、各画面に表示・入力されるデータを
Markdown で一覧化する。DB 設計の材料を集めるためのもので、判断は人(または Claude)が行う。

使い方 (task-app ディレクトリで実行):
    python3 <skill_dir>/scripts/extract_screen_data.py . > docs/db/screen_data_inventory.md

標準ライブラリのみ使用。コードを実行せず、ast と正規表現で静的に解析する。
"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------

JINJA_IGNORE = {
    "url_for", "loop", "true", "false", "none", "True", "False", "None",
    "and", "or", "not", "in", "is", "if", "else", "elif", "endif", "for",
    "endfor", "range", "super", "self", "caller", "request", "session", "g",
    "config", "current_user", "get_flashed_messages", "csrf_token", "varargs",
    "kwargs", "defined", "undefined", "length", "lower", "upper",
}


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def short(value: str, limit: int = 40) -> str:
    value = value.replace("\n", " ").replace("|", "\\|")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def parse_py(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"<!-- 解析失敗: {path}: {exc} -->")
        return None


def const_str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


# ---------------------------------------------------------------------------
# Blueprint / route
# ---------------------------------------------------------------------------

def scan_routes(root: Path):
    """blueprints/*/routes.py から route 情報を集める。"""
    routes = []
    bp_dir = root / "app" / "blueprints"
    if not bp_dir.is_dir():
        return routes
    for path in sorted(bp_dir.rglob("*.py")):
        tree = parse_py(path)
        if tree is None:
            continue
        # 変数名 -> (blueprint 名, url_prefix)
        blueprints: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                if call_name(node.value) == "Blueprint" and node.targets:
                    target = node.targets[0]
                    if isinstance(target, ast.Name):
                        name = const_str(node.value.args[0]) if node.value.args else target.id
                        prefix = ""
                        for kw in node.value.keywords:
                            if kw.arg == "url_prefix":
                                prefix = const_str(kw.value) or ""
                        blueprints[target.id] = (name or target.id, prefix)

        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            urls, methods, roles, bp_name = [], [], [], ""
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                func = deco.func
                if isinstance(func, ast.Attribute) and func.attr in {"route", "get", "post"}:
                    var = func.value.id if isinstance(func.value, ast.Name) else ""
                    bp_name, prefix = blueprints.get(var, (var, ""))
                    url = const_str(deco.args[0]) if deco.args else None
                    if url is not None:
                        urls.append(prefix + url)
                    if func.attr in {"get", "post"}:
                        methods.append(func.attr.upper())
                    for kw in deco.keywords:
                        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                            methods += [const_str(e) or "?" for e in kw.value.elts]
                elif call_name(deco) == "role_required":
                    roles += [ast.unparse(a) for a in deco.args]

            if not urls:
                continue

            templates, services, json_keys, form_keys = [], [], [], []
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                name = call_name(sub)
                if name == "render_template" and sub.args:
                    tpl = const_str(sub.args[0]) or ast.unparse(sub.args[0])
                    ctx = []
                    for kw in sub.keywords:
                        if kw.arg is None:
                            ctx.append("**" + ast.unparse(kw.value))
                        else:
                            ctx.append(f"{kw.arg}={short(ast.unparse(kw.value), 50)}")
                    templates.append((tpl, ctx))
                elif name == "jsonify":
                    for arg in sub.args:
                        if isinstance(arg, ast.Dict):
                            json_keys += [const_str(k) or "?" for k in arg.keys if k is not None]
                    json_keys += [kw.arg for kw in sub.keywords if kw.arg]
                elif isinstance(sub.func, ast.Attribute) and isinstance(sub.func.value, ast.Name):
                    owner = sub.func.value.id
                    if owner.endswith("_service") or owner in {"ai", "classifier"}:
                        services.append(f"{owner}.{sub.func.attr}")
                    # request.form.get("x") / request.files.get("x")
                if (
                    isinstance(sub.func, ast.Attribute)
                    and sub.func.attr in {"get", "getlist"}
                    and isinstance(sub.func.value, ast.Attribute)
                    and sub.func.value.attr in {"form", "files", "args"}
                    and sub.args
                ):
                    key = const_str(sub.args[0])
                    if key:
                        form_keys.append(f"{sub.func.value.attr}.{key}")
            # request.form["x"]
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Subscript)
                    and isinstance(sub.value, ast.Attribute)
                    and sub.value.attr in {"form", "files", "args"}
                ):
                    key = const_str(sub.slice)
                    if key:
                        form_keys.append(f"{sub.value.attr}.{key}")

            routes.append({
                "file": rel(path, root),
                "endpoint": f"{bp_name}.{node.name}" if bp_name else node.name,
                "urls": urls,
                "methods": sorted(set(methods)) or ["GET"],
                "roles": roles,
                "templates": templates,
                "services": sorted(set(services)),
                "json_keys": json_keys,
                "request_keys": sorted(set(form_keys)),
            })
    return routes


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

RE_EXPR = re.compile(r"\{\{-?\s*(.+?)\s*-?\}\}", re.S)
RE_STMT = re.compile(r"\{%-?\s*(.+?)\s*-?%\}", re.S)
RE_FOR = re.compile(r"^for\s+([\w\s,]+?)\s+in\s+(.+?)(?:\s+if\s+.*)?$", re.S)
RE_SET = re.compile(r"^set\s+(\w+)\s*=\s*(.+)$", re.S)
RE_EXTENDS = re.compile(r"""^(extends|include|import|from)\s+["']([^"']+)["']""")
RE_PATH = re.compile(r"(?<![\w.'\"])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[['\"][\w]+['\"]\])*)")
RE_STRING = re.compile(r"""(['"])(?:\\.|(?!\1).)*\1""")
RE_TAG = re.compile(r"<(input|select|textarea|form|button)\b([^>]*)>", re.I | re.S)
RE_ATTR = re.compile(r"""([\w:-]+)\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""")


RE_KWARG = re.compile(r"\b\w+\s*=(?!=)")


def expr_paths(expr: str) -> list[str]:
    """Jinja 式から変数パスを取り出す (filter 名・文字列・キーワード引数名は除く)。"""
    parts = expr.split("|")
    head = parts[0]
    # filter の引数 (例: format(x.y)) も拾う
    tail_args = []
    for p in parts[1:]:
        m = re.search(r"\((.*)\)", p, re.S)
        if m:
            tail_args.append(m.group(1))
    found = []
    for text in [head, *tail_args]:
        text = RE_KWARG.sub(" ", RE_STRING.sub("''", text))
        for m in RE_PATH.finditer(text):
            path = m.group(1).replace("['", ".").replace("[\"", ".").replace("']", "").replace("\"]", "")
            first = path.split(".")[0]
            if first in JINJA_IGNORE or first.isdigit():
                continue
            # x.strftime(...) / x.items() のようなメソッド呼び出しは最後の要素を落とす
            if "." in path and text[m.end():].lstrip().startswith("("):
                path = path.rsplit(".", 1)[0]
            found.append(path)
    return found


def scan_template(path: Path, root: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    info = {
        "file": rel(path, root),
        "extends": [],
        "vars": defaultdict(set),   # トップレベル名 -> 属性パス集合
        "loops": [],                # (alias, iterable)
        "inputs": [],               # フォーム入力
        "forms": [],
    }
    aliases: dict[str, str] = {}

    def resolve(p: str) -> str:
        first, _, rest = p.partition(".")
        if first in aliases:
            alias = aliases[first]
            base = alias if alias.endswith(("{key}", "{value}")) else alias + "[]"
            return f"{base}.{rest}" if rest else base
        return p

    for m in RE_STMT.finditer(text):
        stmt = m.group(1).strip()
        em = RE_EXTENDS.match(stmt)
        if em:
            info["extends"].append(f"{em.group(1)} {em.group(2)}")
            continue
        fm = RE_FOR.match(stmt)
        if fm:
            names = [n.strip() for n in fm.group(1).split(",")]
            iterable = fm.group(2).strip()
            iter_paths = expr_paths(iterable)
            iter_path = resolve(iter_paths[0]) if iter_paths else iterable
            if len(names) == 2 and re.search(r"\.items\s*\(\s*\)\s*$", iterable):
                # {% for k, v in d.items() %} -> d{key} / d{value}
                aliases[names[0]] = iter_path + "{key}"
                aliases[names[1]] = iter_path + "{value}"
            else:
                for idx, n in enumerate(names):
                    aliases[n] = iter_path + (f"<{idx}>" if len(names) > 1 else "")
            info["loops"].append((", ".join(names), iterable))
            continue
        sm = RE_SET.match(stmt)
        if sm:
            src = expr_paths(sm.group(2))
            if src:
                aliases[sm.group(1)] = resolve(src[0]).removesuffix("[]")
            continue
        if stmt.startswith(("if ", "elif ")):
            for p in expr_paths(stmt.split(" ", 1)[1]):
                rp = resolve(p)
                info["vars"][re.split(r"[.\[{<]", rp, maxsplit=1)[0]].add(rp)

    for m in RE_EXPR.finditer(text):
        for p in expr_paths(m.group(1)):
            rp = resolve(p)
            info["vars"][re.split(r"[.\[{<]", rp, maxsplit=1)[0]].add(rp)

    for m in RE_TAG.finditer(text):
        tag = m.group(1).lower()
        attrs = {}
        for am in RE_ATTR.finditer(m.group(2)):
            attrs[am.group(1).lower()] = am.group(3) or am.group(4) or am.group(5) or ""
        if tag == "form":
            info["forms"].append({
                "action": attrs.get("action", ""),
                "method": attrs.get("method", "get").upper(),
                "enctype": attrs.get("enctype", ""),
            })
            continue
        if tag == "button" and "name" not in attrs:
            continue
        itype = attrs.get("type", tag if tag != "input" else "text")
        if itype in {"submit", "button", "reset", "hidden"} and "name" not in attrs:
            continue
        info["inputs"].append({
            "tag": tag,
            "type": itype,
            "name": attrs.get("name", ""),
            "id": attrs.get("id", ""),
            "required": "required" in m.group(2),
            "maxlength": attrs.get("maxlength", ""),
            "accept": attrs.get("accept", ""),
            "placeholder": attrs.get("placeholder", ""),
        })
    return info


# ---------------------------------------------------------------------------
# Service (サンプルデータ = 画面とのデータ契約)
# ---------------------------------------------------------------------------

def dict_shape(node: ast.Dict) -> list[tuple[str, str]]:
    shape = []
    for k, v in zip(node.keys, node.values):
        key = const_str(k) if k is not None else None
        if key is None:
            continue
        if isinstance(v, ast.Constant):
            sample = repr(v.value)
        elif isinstance(v, ast.Dict):
            sample = "{" + ", ".join(kk for kk, _ in dict_shape(v)) + "}"
        elif isinstance(v, (ast.List, ast.Tuple)):
            sample = f"[{len(v.elts)} 件]"
        else:
            sample = ast.unparse(v)
        shape.append((key, short(sample)))
    return shape


def scan_services(root: Path):
    result = []
    for sub in ("services", "ai"):
        base = root / "app" / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            tree = parse_py(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("_") and node.name != "__init__":
                    continue
                shapes: dict[tuple[str, ...], list[tuple[str, str]]] = {}
                for d in ast.walk(node):
                    if isinstance(d, ast.Dict):
                        s = dict_shape(d)
                        if s:
                            shapes.setdefault(tuple(k for k, _ in s), s)
                args = [a.arg for a in node.args.args if a.arg not in {"self", "cls"}]
                doc = ast.get_docstring(node) or ""
                result.append({
                    "file": rel(path, root),
                    "func": node.name,
                    "args": args,
                    "doc": short(doc.splitlines()[0], 80) if doc else "",
                    "shapes": list(shapes.values()),
                })
    return result


# ---------------------------------------------------------------------------
# Model / constants / forms
# ---------------------------------------------------------------------------

def scan_models(root: Path):
    models = []
    base = root / "app" / "models"
    if not base.is_dir():
        return models
    for path in sorted(base.rglob("*.py")):
        tree = parse_py(path)
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            table, cols, rels, consts = "", [], [], []
            for item in node.body:
                if isinstance(item, ast.Assign) and item.targets and isinstance(item.targets[0], ast.Name):
                    name = item.targets[0].id
                    value = item.value
                    if name == "__tablename__":
                        table = const_str(value) or ""
                    elif isinstance(value, ast.Call) and call_name(value) in {"Column", "mapped_column"}:
                        cols.append((name, short(ast.unparse(value), 90)))
                    elif isinstance(value, ast.Call) and call_name(value) == "relationship":
                        rels.append((name, short(ast.unparse(value), 90)))
                    elif isinstance(value, ast.Constant):
                        consts.append((name, repr(value.value)))
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if item.value is not None and isinstance(item.value, ast.Call):
                        fn = call_name(item.value)
                        if fn in {"Column", "mapped_column"}:
                            cols.append((item.target.id, short(ast.unparse(item.value), 90)))
                        elif fn == "relationship":
                            rels.append((item.target.id, short(ast.unparse(item.value), 90)))
            if table or cols:
                models.append({
                    "file": rel(path, root), "class": node.name, "table": table,
                    "cols": cols, "rels": rels, "consts": consts,
                })
    return models


RE_COMMENTS = {
    ".html": re.compile(r"\{#.*?#\}|<!--.*?-->", re.S),
    ".js": re.compile(r"//[^\n]*|/\*.*?\*/", re.S),
    ".css": re.compile(r"/\*.*?\*/", re.S),
}


def scan_placeholders(root: Path):
    """コメント/docstring しかないファイル (README の ⬜) を一覧化する。コメントが仕様。"""
    out = []
    for path in sorted((root / "app").rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".html", ".js", ".css"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            only_doc = all(
                isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
                for n in tree.body
            )
            if not only_doc or (not text.strip() and path.name == "__init__.py"):
                continue
        elif RE_COMMENTS[path.suffix].sub("", text).strip():
            continue
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        out.append((rel(path, root), short(" / ".join(lines), 120) or "(空)"))
    return out


def scan_forms(root: Path):
    out = []
    base = root / "app" / "forms"
    if not base.is_dir():
        return out
    for path in sorted(base.rglob("*.py")):
        tree = parse_py(path)
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            fields = []
            for item in node.body:
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Call):
                    fn = call_name(item.value)
                    if fn.endswith("Field") and item.targets and isinstance(item.targets[0], ast.Name):
                        fields.append((item.targets[0].id, short(ast.unparse(item.value), 90)))
            if fields:
                out.append({"file": rel(path, root), "class": node.name, "fields": fields})
    return out


def scan_js(root: Path):
    out = []
    base = root / "app" / "static" / "js"
    if not base.is_dir():
        return out
    re_fetch = re.compile(r"""fetch\(\s*[`'"]([^`'"]+)""")
    re_prop = re.compile(r"\b(?:d|item|r|row|report|data|p|x|res)\.(\w+)\b(?!\s*\()")
    for path in sorted(base.rglob("*.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        urls = re_fetch.findall(text)
        props = sorted(set(re_prop.findall(text)) - {"length"})
        if urls or props:
            out.append((rel(path, root), urls, props))
    return out


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------

def main() -> int:
    # Windows でリダイレクトすると cp932 になり記号で落ちるため UTF-8 に固定する
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    if not (root / "app").is_dir():
        print(f"app/ が見つかりません: {root} (task-app ディレクトリを指定してください)", file=sys.stderr)
        return 1

    routes = scan_routes(root)
    tpl_dir = root / "app" / "templates"
    templates = {}
    if tpl_dir.is_dir():
        for p in sorted(tpl_dir.rglob("*.html")):
            templates[rel(p, tpl_dir)] = scan_template(p, root)
    services = scan_services(root)
    models = scan_models(root)
    forms = scan_forms(root)
    js = scan_js(root)
    placeholders = scan_placeholders(root)

    w = print
    w("# 画面データ棚卸し (自動抽出)\n")
    w(f"対象: `{root.name}`。静的解析の結果なので、動的に組み立てた値や JS で描画する値は漏れる可能性がある。\n")
    w("## 1. 画面一覧 (route → template → service)\n")
    w("| Endpoint | URL | Method | Role | Template | Service | request から読む値 |")
    w("|---|---|---|---|---|---|---|")
    for r in routes:
        tpls = "<br>".join(t for t, _ in r["templates"]) or ("JSON" if r["json_keys"] else "-")
        w(f"| `{r['endpoint']}` | {'<br>'.join(r['urls'])} | {', '.join(r['methods'])} | "
          f"{', '.join(r['roles']) or '**なし**'} | {tpls} | {'<br>'.join(r['services']) or '-'} | "
          f"{', '.join(r['request_keys']) or '-'} |")
    w("")

    used_templates = set()
    w("## 2. 画面ごとの表示・入力データ\n")
    for r in routes:
        for tpl, ctx in r["templates"]:
            used_templates.add(tpl)
            info = templates.get(tpl)
            w(f"### `{r['endpoint']}` — {tpl}\n")
            w(f"- URL: {', '.join(r['urls'])} ({', '.join(r['methods'])})")
            w(f"- render_template に渡す値: {', '.join(f'`{c}`' for c in ctx) or 'なし'}")
            if r["json_keys"]:
                w(f"- JSON キー: {', '.join(r['json_keys'])}")
            if info is None:
                w("- ⚠ テンプレートファイルが見つからない\n")
                continue
            emit_template(info, w)
        if not r["templates"] and r["json_keys"]:
            w(f"### `{r['endpoint']}` — JSON\n")
            w(f"- JSON キー: {', '.join(r['json_keys'])}\n")

    unused = [t for t in templates if t not in used_templates]
    if unused:
        w("## 3. route から呼ばれていないテンプレート (layout / component / mockup など)\n")
        for t in unused:
            info = templates[t]
            if not (info["vars"] or info["inputs"]):
                w(f"- {t} (変数・入力なし)")
                continue
            w(f"### {t}\n")
            emit_template(info, w)
        w("")

    w("## 4. Service が返すデータの形 (サンプルデータ含む)\n")
    any_shape = False
    for s in services:
        if not s["shapes"]:
            continue
        any_shape = True
        w(f"### `{s['file']}` — `{s['func']}({', '.join(s['args'])})`\n")
        if s["doc"]:
            w(f"> {s['doc']}\n")
        for shape in s["shapes"]:
            w("| key | サンプル値 |")
            w("|---|---|")
            for k, v in shape:
                w(f"| `{k}` | {v} |")
            w("")
    if not any_shape:
        w("dict リテラルを返す service 関数は見つからなかった。\n")

    w("## 5. 既存モデル\n")
    if not models:
        w("モデル定義なし。\n")
    for m in models:
        w(f"### `{m['class']}` → `{m['table'] or '(tablename なし)'}` ({m['file']})\n")
        for name, col in m["cols"]:
            w(f"- `{name}`: `{col}`")
        for name, rl in m["rels"]:
            w(f"- relationship `{name}`: `{rl}`")
        for name, val in m["consts"]:
            w(f"- 定数 `{name}` = {val}")
        w("")

    if forms:
        w("## 6. Flask-WTF フォーム\n")
        for f in forms:
            w(f"### `{f['class']}` ({f['file']})\n")
            for name, field in f["fields"]:
                w(f"- `{name}`: `{field}`")
            w("")

    if js:
        w("## 7. JavaScript が使うデータ\n")
        for path, urls, props in js:
            w(f"- {path}: fetch {', '.join(urls) or 'なし'} / 参照プロパティ {', '.join(props) or 'なし'}")
        w("")

    if placeholders:
        w("## 8. 仮置きファイル (コメント/docstring のみ。コメントが仕様)\n")
        for path, text in placeholders:
            w(f"- {path}: {text}")
        w("")

    w("## 9. 要確認\n")
    issues = []
    for r in routes:
        if not r["roles"] and not any(u.startswith(("/login", "/register", "/reset", "/logout", "/guide", "/faq")) or u == "/"
                                      for u in r["urls"]):
            issues.append(f"`{r['endpoint']}`: role_required が見つからない")
    for t, info in templates.items():
        for i in info["inputs"]:
            if not i["name"]:
                issues.append(f"{t}: `{i['tag']}` (type={i['type']}, id={i['id'] or '-'}) に name がない → 送信されない")
        has_file = any(i["type"] == "file" for i in info["inputs"])
        if has_file and not any("multipart" in f["enctype"] for f in info["forms"]):
            issues.append(f"{t}: file 入力があるが form に enctype=\"multipart/form-data\" がない")
    for i in issues or ["なし"]:
        w(f"- {i}")
    return 0


def emit_template(info: dict, w) -> None:
    if info["extends"]:
        w(f"- 継承/読込: {', '.join(info['extends'])}")
    if info["loops"]:
        w(f"- ループ: {', '.join(f'`{a}` in `{it}`' for a, it in info['loops'])}")
    if info["vars"]:
        w("- 表示している値:")
        for top in sorted(info["vars"]):
            paths = sorted(info["vars"][top])
            w(f"  - `{top}`: {', '.join(f'`{p}`' for p in paths)}")
    if info["forms"]:
        for f in info["forms"]:
            w(f"- form: method={f['method']} action=`{f['action'] or '(同じURL)'}` enctype={f['enctype'] or '-'}")
    if info["inputs"]:
        w("- 入力項目:\n")
        w("  | name | 種類 | id | required | maxlength | accept/placeholder |")
        w("  |---|---|---|---|---|---|")
        for i in info["inputs"]:
            extra = i["accept"] or short(i["placeholder"], 30)
            w(f"  | {i['name'] or '**(なし)**'} | {i['tag']}/{i['type']} | {i['id'] or '-'} | "
              f"{'✓' if i['required'] else ''} | {i['maxlength']} | {extra} |")
    w("")


if __name__ == "__main__":
    sys.exit(main())
