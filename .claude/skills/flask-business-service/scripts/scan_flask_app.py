#!/usr/bin/env python3
"""Quét tĩnh dự án Flask dùng Blueprint → bản đồ route ↔ template ↔ form ↔ service ↔ model.

Không import / không chạy code của dự án (chỉ đọc bằng ast + parser của Jinja2),
nên an toàn khi chưa có .env hay DB.

Cho ra:
  1. Blueprint (tên, url_prefix) và bảng route: method, URL đầy đủ, endpoint, guard,
     template render + biến truyền vào, khoá request.form/args/files đọc, flash, redirect,
     hàm service được gọi, truy cập DB trực tiếp trong route (vi phạm 3 tầng)
  2. Template: tiêu đề, extends/include, form (action, method, enctype, từng ô: name /
     type / required / nhãn), nút, confirm() — và "hình dạng dữ liệu" template cần
     (biến gốc + thuộc tính, kể cả thuộc tính của phần tử trong vòng for)
  3. Service / AI: hàm, chữ ký, docstring, cờ: dữ liệu mẫu? dùng DB? import Flask request/flash?
  4. Model: bảng, cột, khoá, quan hệ
  5. Kiểm tra chéo: template không tồn tại, biến thiếu / thừa, ô form ↔ khoá route đọc,
     url_for tới endpoint không tồn tại, service còn dữ liệu mẫu, route truy cập DB trực tiếp

Cách dùng (chạy ở gốc dự án hoặc truyền --root):
  python scan_flask_app.py --root task-app                  # Markdown ra stdout
  python scan_flask_app.py --root . --filter citizen report # chỉ route/template liên quan
  python scan_flask_app.py --root . --format json -o map.json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

try:
    import jinja2
    from jinja2 import meta, nodes
except ImportError:  # pragma: no cover
    jinja2 = None

SKIP_DIRS = {".git", "venv", ".venv", "env", ".env", "node_modules", "__pycache__", "instance",
             ".pytest_cache", "site-packages", "dist", "build", ".mypy_cache", ".ruff_cache"}
HTTP_DECOS = {"route": None, "get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE", "patch": "PATCH"}
FLASK_GLOBALS = {"config", "request", "session", "g", "url_for", "get_flashed_messages",
                 "current_user", "csrf_token", "range", "dict", "lipsum", "cycler", "joiner",
                 "namespace", "self", "loop", "varargs", "kwargs", "caller", "true", "false", "none",
                 "True", "False", "None", "current_app", "form"}  # form: thường là WTForm — vẫn kiểm tra riêng
SAMPLE_WORDS = re.compile(r"ダミー|サンプル|仮データ|モック|固定値|dummy|sample|mock|fake|placeholder|TODO|FIXME",
                          re.IGNORECASE)


def iter_files(root: Path, suffixes):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in suffixes and not any(part in SKIP_DIRS for part in p.parts):
            yield p


def module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def unparse(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "?"


def const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


# ======================================================================= Python

class PyModule:
    def __init__(self, root: Path, path: Path):
        self.path = path
        self.rel = str(path.relative_to(root))
        self.name = module_name(root, path)
        self.is_pkg = path.name == "__init__.py"
        self.source = path.read_text(encoding="utf-8", errors="replace")
        self.lines = self.source.splitlines()
        self.error = None
        try:
            self.tree = ast.parse(self.source)
        except SyntaxError as e:
            self.tree = ast.Module(body=[], type_ignores=[])
            self.error = f"SyntaxError: {e}"
        self.imports = {}  # local name -> (module, attr or None)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                mod = self._resolve_from(node)
                for a in node.names:
                    self.imports[a.asname or a.name] = (mod, a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    self.imports[(a.asname or a.name).split(".")[0]] = (a.name if a.asname else a.name.split(".")[0], None)

    def _resolve_from(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        base = self.name.split(".")
        if not self.is_pkg:
            base = base[:-1]
        if node.level > 1:
            base = base[: len(base) - (node.level - 1)]
        return ".".join(base + ([node.module] if node.module else []))


def find_blueprints(mods):
    """{(module, var): {'name', 'url_prefix', 'module'}}"""
    bps = {}
    for m in mods.values():
        for node in ast.walk(m.tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                fn = node.value.func
                fname = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                if fname != "Blueprint" or not node.value.args:
                    continue
                name = const_str(node.value.args[0]) or unparse(node.value.args[0])
                prefix = None
                for kw in node.value.keywords:
                    if kw.arg == "url_prefix":
                        prefix = const_str(kw.value)
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        bps[(m.name, t.id)] = {"name": name, "url_prefix": prefix, "module": m.name}
    return bps


def resolve(mods, bps, module: str, var: str, depth=0):
    """Theo import (tối đa 4 cấp) để tìm Blueprint mà biến `var` trong `module` trỏ tới."""
    if (module, var) in bps:
        return bps[(module, var)]
    if depth > 4 or module not in mods:
        return None
    imp = mods[module].imports.get(var)
    if imp:
        src_mod, attr = imp
        if attr is None:
            return None
        found = resolve(mods, bps, src_mod, attr, depth + 1)
        if found:
            return found
        # `from app.blueprints import citizen` rồi dùng citizen.bp -> không xử lý ở đây
    return None


def resolve_attr_bp(mods, bps, m: PyModule, node):
    """node: Name (bp) hoặc Attribute (citizen.bp)."""
    if isinstance(node, ast.Name):
        return resolve(mods, bps, m.name, node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        imp = m.imports.get(node.value.id)
        if imp:
            mod = imp[0] if imp[1] is None else f"{imp[0]}.{imp[1]}"
            return resolve(mods, bps, mod, node.attr)
    return None


def find_registrations(mods, bps):
    """url_prefix khai báo lúc register_blueprint (ghi đè prefix của Blueprint)."""
    regs = {}
    for m in mods.values():
        for node in ast.walk(m.tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "register_blueprint" and node.args):
                bp = resolve_attr_bp(mods, bps, m, node.args[0])
                prefix = None
                for kw in node.keywords:
                    if kw.arg == "url_prefix":
                        prefix = const_str(kw.value)
                key = bp["name"] if bp else unparse(node.args[0])
                regs[key] = {"registered_in": m.rel, "url_prefix": prefix, "resolved": bool(bp)}
    return regs


def context_globals(mods):
    keys = set()
    for m in mods.values():
        for node in ast.walk(m.tree):
            if isinstance(node, ast.FunctionDef):
                decos = [unparse(d) for d in node.decorator_list]
                if any("context_processor" in d for d in decos):
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Return) and sub.value is not None:
                            v = sub.value
                            if isinstance(v, ast.Dict):
                                keys.update(k.value for k in v.keys if isinstance(k, ast.Constant))
                            elif isinstance(v, ast.Call) and unparse(v.func) == "dict":
                                keys.update(kw.arg for kw in v.keywords if kw.arg)
                if any("template_global" in d for d in decos):
                    keys.add(node.name)
            # app.jinja_env.globals["x"] = ... / .update(x=...) / add_template_global(f, "name")
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Subscript) and unparse(t.value).endswith("jinja_env.globals"):
                        s = const_str(t.slice)
                        if s:
                            keys.add(s)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                f = unparse(node.func)
                if f.endswith("jinja_env.globals.update"):
                    keys.update(kw.arg for kw in node.keywords if kw.arg)
                if f.endswith("add_template_global"):
                    for kw in node.keywords:
                        if kw.arg == "name" and const_str(kw.value):
                            keys.add(const_str(kw.value))
                    if len(node.args) >= 2 and const_str(node.args[1]):
                        keys.add(const_str(node.args[1]))
                    elif node.args and isinstance(node.args[0], ast.Name):
                        keys.add(node.args[0].id)
    return keys


def is_service_module(name: str) -> bool:
    parts = name.split(".")
    return any(p in ("services", "service", "ai") or p.endswith("_service") for p in parts)


def analyze_route_body(m: PyModule, fn: ast.FunctionDef, bp_name: str):
    info = {"templates": [], "redirects": [], "url_fors": [], "flashes": [], "request_keys": [],
            "service_calls": [], "direct_db": [], "aborts": [], "forms": []}
    service_aliases = {}
    for local, (mod, attr) in m.imports.items():
        full = mod if attr is None else f"{mod}.{attr}"
        if is_service_module(mod) or is_service_module(full):
            service_aliases[local] = full

    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            fname = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if fname == "render_template" and node.args:
                tpl = const_str(node.args[0]) or unparse(node.args[0])
                kws = [kw.arg if kw.arg else "**" + unparse(kw.value) for kw in node.keywords]
                info["templates"].append({"template": tpl, "context": kws, "line": node.lineno})
            elif fname == "url_for" and node.args:
                ep = const_str(node.args[0])
                if ep:
                    if ep.startswith("."):
                        ep = bp_name + ep
                    info["url_fors"].append(ep)
            elif fname == "redirect" and node.args:
                info["redirects"].append(unparse(node.args[0]))
            elif fname == "flash" and node.args:
                cat = unparse(node.args[1]) if len(node.args) > 1 else next(
                    (unparse(k.value) for k in node.keywords if k.arg == "category"), "")
                info["flashes"].append({"message": const_str(node.args[0]) or unparse(node.args[0]),
                                        "category": cat.strip("'\""), "line": node.lineno})
            elif fname == "abort" and node.args:
                info["aborts"].append(unparse(node.args[0]))
            elif fname.endswith("Form") and isinstance(f, ast.Name):
                info["forms"].append(fname)
            # request.form.get("x") / request.args.get / request.files.getlist
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute):
                base = unparse(f.value)
                if base in ("request.form", "request.args", "request.files", "request.values") and node.args:
                    k = const_str(node.args[0])
                    if k:
                        info["request_keys"].append((base.split(".")[1], k))
            if isinstance(f, ast.Attribute) and unparse(f) in ("request.get_json",):
                info["request_keys"].append(("json", "*"))
            # gọi service
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in service_aliases:
                info["service_calls"].append(f"{service_aliases[f.value.id]}.{f.attr}")
            elif isinstance(f, ast.Name) and f.id in service_aliases:
                info["service_calls"].append(service_aliases[f.id])
            # DB trực tiếp trong route
            fs = unparse(f)
            if fs.startswith("db.session.") or ".query." in fs or fs.endswith(".query.get") or fs in ("db.session.execute", "select"):
                info["direct_db"].append(f"{fs}() L{node.lineno}")
        elif isinstance(node, ast.Subscript):
            base = unparse(node.value)
            if base in ("request.form", "request.args", "request.files", "request.values"):
                k = const_str(node.slice)
                if k:
                    info["request_keys"].append((base.split(".")[1], k))
    for k in info:
        if isinstance(info[k], list) and info[k] and isinstance(info[k][0], (str, tuple)):
            info[k] = list(dict.fromkeys(info[k]))
    seen, uniq = set(), []
    for t in info["templates"]:
        key = (t["template"], tuple(t["context"]))
        if key not in seen:
            seen.add(key)
            uniq.append(t)
    info["templates"] = uniq
    return info


def find_routes(mods, bps, regs):
    routes = []
    for m in mods.values():
        for node in ast.walk(m.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route_decos, guards = [], []
            for d in node.decorator_list:
                call = d if isinstance(d, ast.Call) else None
                target = call.func if call else d
                if isinstance(target, ast.Attribute) and target.attr in HTTP_DECOS and call is not None:
                    bp = resolve_attr_bp(mods, bps, m, target.value)
                    if bp is None and isinstance(target.value, ast.Name) and target.value.id == "app":
                        bp = {"name": "", "url_prefix": None}
                    if bp is None:
                        # biến blueprint không lần ra được: vẫn ghi lại
                        bp = {"name": f"?{unparse(target.value)}", "url_prefix": None}
                    rule = const_str(call.args[0]) if call.args else "?"
                    methods = [HTTP_DECOS[target.attr]] if HTTP_DECOS[target.attr] else ["GET"]
                    endpoint = node.name
                    for kw in call.keywords:
                        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                            methods = [const_str(e) or unparse(e) for e in kw.value.elts]
                        if kw.arg == "endpoint" and const_str(kw.value):
                            endpoint = const_str(kw.value)
                    route_decos.append((bp, rule, methods, endpoint))
                else:
                    guards.append(unparse(d))
            for bp, rule, methods, endpoint in route_decos:
                prefix = (regs.get(bp["name"], {}) or {}).get("url_prefix") or bp.get("url_prefix") or ""
                url = (prefix.rstrip("/") + "/" + (rule or "").lstrip("/")) if prefix else rule
                full_ep = f"{bp['name']}.{endpoint}" if bp["name"] else endpoint
                routes.append({
                    "file": f"{m.rel}:{node.lineno}",
                    "function": node.name,
                    "blueprint": bp["name"],
                    "endpoint": full_ep,
                    "url": url,
                    "methods": methods,
                    "guards": guards,
                    "docstring": (ast.get_docstring(node) or "").split("\n")[0],
                    **analyze_route_body(m, node, bp["name"]),
                })
    routes.sort(key=lambda r: (r["blueprint"], r["url"] or ""))
    return routes


def func_flags(m: PyModule, fn) -> list[str]:
    flags = []
    end = getattr(fn, "end_lineno", fn.lineno)
    body_src = "\n".join(m.lines[fn.lineno - 1:end])
    if SAMPLE_WORDS.search(body_src):
        flags.append("dữ liệu mẫu?")
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Return) and isinstance(sub.value, (ast.List, ast.Dict, ast.Tuple)):
            consts = [n for n in ast.walk(sub.value) if isinstance(n, ast.Constant)]
            names = [n for n in ast.walk(sub.value) if isinstance(n, (ast.Name, ast.Attribute, ast.Call))]
            if len(consts) >= 3 and len(consts) > len(names):
                flags.append("trả literal (mẫu?)")
                break
    if re.search(r"db\.session|\.query\b|select\(|db\.paginate", body_src):
        flags.append("dùng DB")
    if re.search(r"\bcommit\(\)", body_src):
        flags.append("commit")
    if re.search(r"genai|generate_content|get_classifier|classify\(", body_src):
        flags.append("gọi AI")
    if len(body_src.strip().splitlines()) <= 3 and re.search(r"\bpass\b|\.\.\.|NotImplementedError", body_src):
        flags.append("chưa cài đặt")
    return list(dict.fromkeys(flags))


def find_services(mods):
    out = []
    for m in mods.values():
        if not is_service_module(m.name):
            continue
        flask_imports = sorted(k for k, (mod, attr) in m.imports.items()
                               if mod == "flask" and attr in ("request", "flash", "render_template", "redirect", "session", "url_for"))
        funcs = []
        for node in m.tree.body:
            targets = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                targets.append((node.name, node))
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("__"):
                        targets.append((f"{node.name}.{sub.name}", sub))
            for name, fn in targets:
                ret = f" -> {unparse(fn.returns)}" if fn.returns is not None else ""
                funcs.append({
                    "name": name,
                    "signature": f"{name}({unparse(fn.args)}){ret}",
                    "line": fn.lineno,
                    "doc": (ast.get_docstring(fn) or "").split("\n")[0],
                    "flags": func_flags(m, fn),
                })
        if m.is_pkg and not funcs:
            continue
        module_flags = []
        if SAMPLE_WORDS.search("\n".join(l for l in m.lines if l.strip().startswith("#"))):
            module_flags.append("comment nhắc dữ liệu mẫu/TODO")
        if len(m.source.strip().splitlines()) <= 2:
            module_flags.append("file giữ chỗ")
        out.append({"module": m.name, "file": m.rel, "flask_imports": flask_imports,
                    "flags": module_flags, "functions": funcs, "error": m.error})
    out.sort(key=lambda s: s["module"])
    return out


def camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def find_models(mods):
    models = []
    for m in mods.values():
        for node in ast.walk(m.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [unparse(b) for b in node.bases]
            table, cols, rels = None, [], []
            for st in node.body:
                tgt, val, ann = None, None, None
                if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                    tgt, val = st.targets[0].id, st.value
                elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    tgt, val, ann = st.target.id, st.value, st.annotation
                if tgt == "__tablename__":
                    table = const_str(val)
                    continue
                if not isinstance(val, ast.Call):
                    continue
                fn = unparse(val.func)
                if fn.endswith("Column") or fn.endswith("mapped_column"):
                    col = {"attr": tgt, "name": tgt, "type": "", "pk": False, "fk": None,
                           "nullable": None, "unique": False, "index": False, "default": None}
                    for a in val.args:
                        s = unparse(a)
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            col["name"] = a.value
                        elif "ForeignKey" in s:
                            fk = a.args[0] if isinstance(a, ast.Call) and a.args else None
                            col["fk"] = const_str(fk) if fk is not None else s
                        elif not col["type"]:
                            col["type"] = s.replace("db.", "").replace("sa.", "")
                    if not col["type"] and ann is not None:
                        col["type"] = re.sub(r"^Mapped\[(.*)\]$", r"\1", unparse(ann))
                    for kw in val.keywords:
                        v = unparse(kw.value)
                        if kw.arg == "primary_key":
                            col["pk"] = v == "True"
                        elif kw.arg == "nullable":
                            col["nullable"] = v == "True"
                        elif kw.arg == "unique":
                            col["unique"] = v == "True"
                        elif kw.arg == "index":
                            col["index"] = v == "True"
                        elif kw.arg in ("default", "server_default"):
                            col["default"] = v
                    if col["nullable"] is None:
                        if col["pk"]:
                            col["nullable"] = False
                        elif ann is not None and fn.endswith("mapped_column"):
                            # Mapped[X] → NOT NULL; Mapped[Optional[X]] / Mapped[X | None] → NULL
                            a_src = unparse(ann)
                            col["nullable"] = "Optional" in a_src or "None" in a_src
                        else:
                            col["nullable"] = True  # mặc định của Column()
                    cols.append(col)
                elif fn.endswith("relationship"):
                    target = const_str(val.args[0]) if val.args else (unparse(ann) if ann is not None else "?")
                    extra = {kw.arg: unparse(kw.value) for kw in val.keywords if kw.arg in ("back_populates", "backref", "lazy", "cascade")}
                    rels.append({"attr": tgt, "target": target, **extra})
            is_model = table or any(b.endswith("Model") or b in ("Base", "db.Model") for b in bases)
            if is_model and (cols or table):
                models.append({"class": node.name, "table": table or camel_to_snake(node.name),
                               "table_inferred": table is None, "file": f"{m.rel}:{node.lineno}",
                               "columns": cols, "relationships": rels})
    models.sort(key=lambda x: x["table"])
    return models


# ======================================================================= Template

class FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms, self.cur, self.labels, self.confirms = [], None, {}, []
        self._label_for, self._label_text, self._btn = None, [], None
        self.title, self._in_title, self.h1, self._in_h1 = "", False, "", False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k, v in a.items():
            if k.startswith("on") and v and "confirm(" in v:
                m = re.search(r"confirm\(\s*['\"](.+?)['\"]", v)
                self.confirms.append(m.group(1) if m else v[:80])
        if tag == "form":
            self.cur = {"action": a.get("action", ""), "method": (a.get("method") or "GET").upper(),
                        "enctype": a.get("enctype", ""), "fields": [], "buttons": []}
            self.forms.append(self.cur)
        elif tag in ("input", "select", "textarea") and self.cur is not None:
            t = a.get("type", tag if tag != "input" else "text")
            if t in ("submit", "button", "image"):
                self.cur["buttons"].append(a.get("value") or a.get("name") or t)
                return
            self.cur["fields"].append({"name": a.get("name"), "type": t, "id": a.get("id"),
                                       "required": "required" in a, "label": None,
                                       "accept": a.get("accept"), "multiple": "multiple" in a,
                                       "maxlength": a.get("maxlength")})
        elif tag == "button" and self.cur is not None:
            self._btn = {"type": a.get("type", "submit"), "text": []}
        elif tag == "label":
            self._label_for, self._label_text = a.get("for"), []
        elif tag == "title":
            self._in_title = True
        elif tag == "h1" or (tag == "h2" and not self.h1):
            self._in_h1 = True

    def handle_endtag(self, tag):
        if tag == "form":
            self.cur = None
        elif tag == "button" and self._btn is not None:
            if self.cur is not None and self._btn["type"] in ("submit", ""):
                self.cur["buttons"].append(" ".join("".join(self._btn["text"]).split()))
            self._btn = None
        elif tag == "label" and self._label_for is not None:
            self.labels[self._label_for] = " ".join("".join(self._label_text).split())
            self._label_for = None
        elif tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2"):
            self._in_h1 = False

    def handle_data(self, data):
        if self._btn is not None:
            self._btn["text"].append(data)
        if self._label_for is not None:
            self._label_text.append(data)
        if self._in_title:
            self.title += data.strip()
        if self._in_h1 and not self.h1.strip():
            self.h1 += data.strip()


JINJA_STRIP = re.compile(r"\{#.*?#\}", re.S)


def strip_jinja(text: str) -> str:
    return " ".join(re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", text or "", flags=re.S).split())


def jinja_shape(src: str):
    """→ (biến gốc cần, {biến: set(thuộc tính)}, url_for endpoints, templates tham chiếu, lỗi)"""
    if jinja2 is None:
        roots = set(re.findall(r"\{\{\s*([A-Za-z_]\w*)", src)) | set(re.findall(r"\{%\s*(?:if|for \w+ in)\s+([A-Za-z_]\w*)", src))
        return roots, {}, set(re.findall(r"url_for\(\s*['\"]([\w.]+)", src)), set(), "jinja2 chưa cài: dùng regex"
    env = jinja2.Environment(extensions=["jinja2.ext.do", "jinja2.ext.loopcontrols"])
    try:
        tree = env.parse(src)
    except jinja2.TemplateSyntaxError as e:
        roots = set(re.findall(r"\{\{\s*([A-Za-z_]\w*)", src))
        return roots, {}, set(re.findall(r"url_for\(\s*['\"]([\w.]+)", src)), set(), f"TemplateSyntaxError L{e.lineno}: {e.message}"
    roots = meta.find_undeclared_variables(tree)
    refs = {r for r in meta.find_referenced_templates(tree) if r}
    alias = {}  # loop var -> "iter[]"
    for f in tree.find_all(nodes.For):
        it = f.iter
        it_name = None
        if isinstance(it, nodes.Name):
            it_name = it.name
        elif isinstance(it, nodes.Getattr) and isinstance(it.node, nodes.Name):
            it_name = f"{it.node.name}.{it.attr}"
        elif isinstance(it, nodes.Call) and isinstance(it.node, nodes.Getattr) and isinstance(it.node.node, nodes.Name):
            it_name = f"{it.node.node.name}.{it.node.attr}()"  # dict.items()
        if it_name is None:
            continue
        tgts = [f.target] if isinstance(f.target, nodes.Name) else list(getattr(f.target, "items", []))
        for t in tgts:
            if isinstance(t, nodes.Name):
                alias[t.name] = it_name + "[]"
    attrs = defaultdict(set)
    for g in tree.find_all(nodes.Getattr):
        if isinstance(g.node, nodes.Name):
            key = alias.get(g.node.name, g.node.name)
            attrs[key].add(g.attr)
    for g in tree.find_all(nodes.Getitem):
        if isinstance(g.node, nodes.Name) and isinstance(g.arg, nodes.Const) and isinstance(g.arg.value, str):
            key = alias.get(g.node.name, g.node.name)
            attrs[key].add(g.arg.value)
    eps = set()
    for c in tree.find_all(nodes.Call):
        if isinstance(c.node, nodes.Name) and c.node.name == "url_for" and c.args and isinstance(c.args[0], nodes.Const):
            eps.add(c.args[0].value)
    return set(roots), {k: sorted(v) for k, v in attrs.items()}, eps, refs, None


def find_templates(root: Path):
    tpls = {}
    for tdir in [p for p in root.rglob("templates") if p.is_dir() and not any(x in SKIP_DIRS for x in p.parts)]:
        for f in tdir.rglob("*"):
            if not f.is_file() or f.suffix not in (".html", ".htm", ".jinja", ".j2", ".txt", ".xml"):
                continue
            name = f.relative_to(tdir).as_posix()
            src = f.read_text(encoding="utf-8", errors="replace")
            roots, attrs, eps, refs, err = jinja_shape(src)
            p = FormParser()
            try:
                p.feed(JINJA_STRIP.sub("", src))
            except Exception as e:  # HTML hỏng vẫn tiếp tục
                err = (err or "") + f" HTMLParser: {e}"
            for form in p.forms:
                for fld in form["fields"]:
                    if fld["id"] and fld["id"] in p.labels:
                        fld["label"] = p.labels[fld["id"]]
                m = re.search(r"url_for\(\s*['\"]([\w.]+)['\"]", form["action"] or "")
                form["action_endpoint"] = m.group(1) if m else None
            bt = re.search(r"\{%-?\s*block\s+title\s*-?%\}(.*?)\{%-?\s*endblock", src, re.S)
            title = next((strip_jinja(x) for x in (bt.group(1) if bt else "", p.h1, p.title) if strip_jinja(x)), "")
            tpls[name] = {"file": str(f.relative_to(root)), "title": title, "h1": strip_jinja(p.h1),
                          "extends": sorted(r for r in refs if re.search(r"extends\s+['\"]" + re.escape(r), src)),
                          "references": sorted(refs), "needs": sorted(roots), "shape": attrs,
                          "url_fors": sorted(eps), "forms": p.forms, "confirms": p.confirms, "error": err}
    return tpls


def template_needs(tpls, name, seen=None):
    """Biến cần của template + các template nó extends/include (đệ quy)."""
    seen = seen or set()
    if name in seen or name not in tpls:
        return set()
    seen.add(name)
    need = set(tpls[name]["needs"])
    for r in tpls[name]["references"]:
        need |= template_needs(tpls, r, seen)
    return need


# ======================================================================= Kiểm tra chéo

def cross_check(routes, tpls, services, models, globals_):
    issues = []
    endpoints = {r["endpoint"] for r in routes} | {"static"}
    endpoints |= {f"{r['blueprint']}.static" for r in routes if r["blueprint"]}
    by_ep = {r["endpoint"]: r for r in routes}
    ignore = FLASK_GLOBALS | globals_

    for r in routes:
        where = f"{r['endpoint']} ({r['file']})"
        if r["direct_db"]:
            issues.append(("3 tầng", where, "route truy cập DB trực tiếp: " + ", ".join(r["direct_db"][:4])))
        if not r["guards"]:
            issues.append(("phân quyền", where, "route không có decorator guard (login_required/role_required) — kiểm tra có cố ý không"))
        for ep in r["url_fors"]:
            if ep not in endpoints:
                issues.append(("url_for", where, f"endpoint không tồn tại: {ep}"))
        for t in r["templates"]:
            name = t["template"]
            if name not in tpls:
                issues.append(("template", where, f"render_template('{name}') — không tìm thấy file"))
                continue
            passed = {c for c in t["context"] if not c.startswith("**")}
            if any(c.startswith("**") for c in t["context"]):
                continue  # truyền **ctx: không đối chiếu được
            need = template_needs(tpls, name) - ignore
            missing = sorted(need - passed)
            unused = sorted(passed - template_needs(tpls, name))
            if missing:
                issues.append(("biến template", where, f"{name} dùng nhưng route không truyền: {', '.join(missing)} "
                               "(có thể do {% if x is defined %} hoặc do layout cha — xem lại)"))
            if unused:
                issues.append(("biến template", where, f"truyền vào {name} nhưng template không dùng: {', '.join(unused)}"))
            # form ↔ request keys
            read = {k for src, k in r["request_keys"] if src in ("form", "files", "values")}
            for form in tpls[name]["forms"]:
                if form["method"] != "POST":
                    continue
                target = form["action_endpoint"]
                if target and target != r["endpoint"]:
                    continue  # form gửi tới route khác — kiểm ở route đó
                if not target and "POST" not in r["methods"]:
                    issues.append(("form", where, f"{name}: form POST về chính URL nhưng route không nhận POST"))
                    continue
                names = {f["name"] for f in form["fields"] if f["name"]} - {"csrf_token"}
                if r["forms"]:
                    continue  # dùng WTForm: field đọc qua form.x.data
                if read:
                    if names - read:
                        issues.append(("form", where, f"{name}: ô gửi lên nhưng route không đọc: {', '.join(sorted(names - read))}"))
                    if read - names:
                        issues.append(("form", where, f"{name}: route đọc nhưng form không có ô: {', '.join(sorted(read - names))}"))
                if any(f["type"] == "file" for f in form["fields"]) and "multipart" not in form["enctype"]:
                    issues.append(("form", where, f"{name}: có input file nhưng thiếu enctype=\"multipart/form-data\""))
                for f in form["fields"]:
                    if not f["name"]:
                        issues.append(("form", where, f"{name}: ô type={f['type']} id={f['id']} thiếu thuộc tính name"))

    for name, t in tpls.items():
        for ep in t["url_fors"]:
            if not ep.startswith(".") and ep not in endpoints:
                issues.append(("url_for", name, f"endpoint không tồn tại: {ep}"))
        for form in t["forms"]:
            ep = form["action_endpoint"]
            if ep and ep in by_ep and form["method"] not in by_ep[ep]["methods"]:
                issues.append(("form", name, f"form {form['method']} tới {ep} nhưng route chỉ nhận {by_ep[ep]['methods']}"))
        if t["error"]:
            issues.append(("đọc template", name, t["error"]))

    for s in services:
        if s["flask_imports"]:
            issues.append(("3 tầng", s["file"], f"service import từ flask: {', '.join(s['flask_imports'])}"))
        for f in s["functions"]:
            if {"dữ liệu mẫu?", "trả literal (mẫu?)", "chưa cài đặt"} & set(f["flags"]):
                issues.append(("service", f"{s['file']}:{f['line']}", f"{f['name']}: {', '.join(f['flags'])}"))
        if s["error"]:
            issues.append(("đọc python", s["file"], s["error"]))
    return list(dict.fromkeys(issues))


# ======================================================================= Xuất

def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |")
    return out


def render_md(data) -> str:
    o = ["# Bản đồ dự án Flask", ""]
    o += ["## 1. Blueprint", ""]
    o += md_table(["Blueprint", "url_prefix (Blueprint)", "url_prefix (register)", "Định nghĩa"],
                  [[b["name"], b["url_prefix"] or "", (data["registrations"].get(b["name"]) or {}).get("url_prefix") or "",
                    b["module"]] for b in data["blueprints"]])
    unresolved = [k for k, v in data["registrations"].items() if not v["resolved"]]
    if unresolved:
        o += ["", f"register_blueprint chưa lần ra Blueprint: {', '.join(unresolved)}"]

    o += ["", "## 2. Route", ""]
    rows = []
    for r in data["routes"]:
        tpl = "; ".join(f"{t['template']}({', '.join(t['context'])})" for t in r["templates"])
        keys = ", ".join(f"{s}:{k}" for s, k in r["request_keys"])
        fl = "; ".join(f"「{f['message']}」{('/' + f['category']) if f['category'] else ''}" for f in r["flashes"])
        rows.append([",".join(r["methods"]), r["url"], r["endpoint"], " ".join(r["guards"]), tpl,
                     ", ".join(r["service_calls"]), keys, fl, ", ".join(r["redirects"]), r["file"]])
    o += md_table(["Method", "URL", "Endpoint", "Guard", "Template(biến)", "Service gọi", "request đọc",
                   "flash", "redirect", "Nguồn"], rows)

    o += ["", "## 3. Template", ""]
    for name, t in sorted(data["templates"].items()):
        o.append(f"### {name}" + (f" — {t['title']}" if t["title"] else ""))
        if t["extends"]:
            o.append(f"- extends: {', '.join(t['extends'])}")
        other = [x for x in t["references"] if x not in t["extends"]]
        if other:
            o.append(f"- include/import: {', '.join(other)}")
        need = [n for n in t["needs"] if n not in FLASK_GLOBALS]
        if need:
            o.append(f"- biến cần: {', '.join(need)}")
        if t["shape"]:
            o.append("- hình dạng dữ liệu: " + "; ".join(f"{k} → {', '.join(v)}" for k, v in sorted(t["shape"].items())
                                                       if k.split('.')[0].rstrip('[]') not in ("request", "config", "loop")))
        for i, f in enumerate(t["forms"], 1):
            o.append(f"- form {i}: {f['method']} → {f['action_endpoint'] or f['action'] or '(cùng URL)'}"
                     + (f" enctype={f['enctype']}" if f["enctype"] else ""))
            for fld in f["fields"]:
                bits = [fld["type"]]
                if fld["required"]:
                    bits.append("required")
                if fld["maxlength"]:
                    bits.append(f"maxlength={fld['maxlength']}")
                if fld["accept"]:
                    bits.append(f"accept={fld['accept']}")
                if fld["multiple"]:
                    bits.append("multiple")
                o.append(f"    - {fld['name']} ({', '.join(bits)})" + (f" 「{fld['label']}」" if fld["label"] else ""))
            if f["buttons"]:
                o.append(f"    - nút: {', '.join('「' + b + '」' for b in f['buttons'])}")
        if t["confirms"]:
            o.append(f"- confirm(): {', '.join('「' + c + '」' for c in t['confirms'])}")
        o.append("")

    o += ["## 4. Service / AI", ""]
    for s in data["services"]:
        flags = (" ‹" + "・".join(s["flags"]) + "›") if s["flags"] else ""
        o.append(f"### {s['module']} ({s['file']}){flags}")
        for f in s["functions"]:
            fl = f" ‹{'・'.join(f['flags'])}›" if f["flags"] else ""
            doc = f" — {f['doc']}" if f["doc"] else ""
            o.append(f"- L{f['line']} `{f['signature']}`{fl}{doc}")
        if not s["functions"]:
            o.append("- (chưa có hàm)")
        o.append("")

    o += ["## 5. Model", ""]
    for m in data["models"]:
        o.append(f"### {m['table']}{' (tên suy ra)' if m['table_inferred'] else ''} — {m['class']} ({m['file']})")
        o += md_table(["Cột", "Kiểu", "PK", "FK", "NULL", "Unique", "Index", "Default"],
                      [[c["name"], c["type"], "✓" if c["pk"] else "", c["fk"] or "", "✓" if c["nullable"] else "",
                        "✓" if c["unique"] else "", "✓" if c["index"] else "", c["default"] or ""] for c in m["columns"]])
        if m["relationships"]:
            o.append("- quan hệ: " + "; ".join(f"{r['attr']} → {r['target']}" for r in m["relationships"]))
        o.append("")

    o += ["## 6. Kiểm tra chéo", ""]
    if data["issues"]:
        o += md_table(["Loại", "Ở đâu", "Nội dung"], data["issues"])
    else:
        o.append("Không thấy vấn đề.")
    o.append("")
    if data["context_globals"]:
        o.append(f"Biến toàn cục template (context_processor/globals): {', '.join(sorted(data['context_globals']))}")
    return "\n".join(o) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--filter", nargs="*", default=[], help="chỉ giữ route/template/service có chứa chuỗi này")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("-o", "--out", type=Path)
    a = ap.parse_args()
    root = a.root.resolve()

    mods = {}
    for p in iter_files(root, {".py"}):
        m = PyModule(root, p)
        mods[m.name] = m
    bps = find_blueprints(mods)
    regs = find_registrations(mods, bps)
    routes = find_routes(mods, bps, regs)
    tpls = find_templates(root)
    services = find_services(mods)
    models = find_models(mods)
    globals_ = context_globals(mods)
    issues = cross_check(routes, tpls, services, models, globals_)

    if a.filter:
        def hit(*xs):
            return any(f in (x or "") for f in a.filter for x in xs)
        routes = [r for r in routes if hit(r["endpoint"], r["url"], r["file"], *[t["template"] for t in r["templates"]])]
        keep_t = {t["template"] for r in routes for t in r["templates"]}
        tpls = {k: v for k, v in tpls.items() if k in keep_t or hit(k)}
        called = {c for r in routes for c in r["service_calls"]}
        services = [s for s in services if hit(s["module"]) or any(c.startswith(s["module"]) for c in called)]
        issues = [i for i in issues if hit(i[1], i[2])]

    data = {"root": str(root), "blueprints": sorted(bps.values(), key=lambda b: b["name"]),
            "registrations": regs, "routes": routes, "templates": tpls, "services": services,
            "models": models, "context_globals": sorted(globals_), "issues": issues}
    text = json.dumps(data, ensure_ascii=False, indent=2, default=list) if a.format == "json" else render_md(data)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(f"Đã ghi {a.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
