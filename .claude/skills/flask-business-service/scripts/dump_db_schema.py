#!/usr/bin/env python3
"""Đọc schema DB thật (CHỈ ĐỌC) và so với model SQLAlchemy của dự án.

- Lấy bảng, cột (kiểu, NULL, default, comment), PK, FK, unique, index, check, enum (PostgreSQL)
- Tuỳ chọn đếm số dòng mỗi bảng (--counts) và liệt kê giá trị khác nhau của vài cột mã
  (--distinct reports.status users.role) để biết mã thật đang lưu trong DB
- So với model (--compare-models <gốc dự án>): bảng/cột có trong model mà DB chưa có
  (cần migration / tạo lại DB dev) và ngược lại, lệch NULL / kiểu

An toàn:
- PostgreSQL: mở phiên với default_transaction_read_only=on; SQLite: mở chế độ ro
- Không in mật khẩu trong URL; không in dữ liệu dòng (chỉ đếm và giá trị mã khi được yêu cầu)

URL DB lấy theo thứ tự: --url → biến môi trường → file .env (--env-file, mặc định ./.env)
với các khoá DATABASE_URL, SQLALCHEMY_DATABASE_URI.

Chạy bằng python của venv dự án (đã có driver, ví dụ psycopg):
  python dump_db_schema.py --env-file task-app/.env --compare-models task-app
  python dump_db_schema.py --url sqlite:///instance/dev.db --counts
  python dump_db_schema.py --distinct reports.status reports.category_code

DB chỉ truy cập được từ máy developer: xuất JSON ở đó (không cần file script nằm trong dự án)
  python - --env-file .env --counts --format json -o /tmp/schema.json < dump_db_schema.py
rồi so với model ở nơi có mã nguồn:
  python dump_db_schema.py --from-json schema.json --compare-models task-app
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import sqlalchemy as sa
    from sqlalchemy.engine import make_url
except ImportError:  # pragma: no cover
    sys.exit("Cần SQLAlchemy (chạy bằng python của venv dự án)")

URL_KEYS = ("DATABASE_URL", "SQLALCHEMY_DATABASE_URI")


def read_env_file(path: Path) -> dict:
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out


def get_url(args) -> str:
    if args.url:
        return args.url
    for k in URL_KEYS:
        if os.environ.get(k):
            return os.environ[k]
    env = read_env_file(args.env_file)
    for k in URL_KEYS:
        if env.get(k):
            return env[k]
    sys.exit("Không tìm thấy URL DB (--url, biến môi trường hoặc .env với DATABASE_URL / SQLALCHEMY_DATABASE_URI)")


def make_engine(url_str: str):
    url = make_url(url_str)
    if url.drivername == "postgres":  # Heroku-style
        url = url.set(drivername="postgresql")
    kwargs = {}
    if url.get_backend_name() == "postgresql":
        kwargs["connect_args"] = {"options": "-c default_transaction_read_only=on"}
    elif url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        db_path = Path(url.database).resolve()
        if not db_path.exists():
            sys.exit(f"File SQLite không tồn tại: {db_path}")
        url = make_url(f"sqlite:///file:{db_path}?mode=ro&uri=true")
    return sa.create_engine(url), url


def safe_url(url) -> str:
    return url.render_as_string(hide_password=True)


def dump(engine, schema, counts, distinct):
    insp = sa.inspect(engine)
    backend = engine.dialect.name
    tables = []
    for t in sorted(insp.get_table_names(schema=schema)):
        cols = []
        for c in insp.get_columns(t, schema=schema):
            cols.append({"name": c["name"], "type": str(c["type"]), "nullable": c.get("nullable", True),
                         "default": str(c.get("default")) if c.get("default") is not None else None,
                         "comment": c.get("comment")})
        pk = insp.get_pk_constraint(t, schema=schema).get("constrained_columns") or []
        fks = [{"columns": fk["constrained_columns"], "ref": f"{fk['referred_table']}({', '.join(fk['referred_columns'])})",
                "ondelete": (fk.get("options") or {}).get("ondelete")} for fk in insp.get_foreign_keys(t, schema=schema)]
        uniques = [u["column_names"] for u in insp.get_unique_constraints(t, schema=schema)]
        indexes = [{"name": i["name"], "columns": i["column_names"], "unique": i.get("unique", False)}
                   for i in insp.get_indexes(t, schema=schema)]
        try:
            checks = [ck.get("sqltext") for ck in insp.get_check_constraints(t, schema=schema)]
        except NotImplementedError:
            checks = []
        try:
            comment = insp.get_table_comment(t, schema=schema).get("text")
        except NotImplementedError:
            comment = None
        tables.append({"name": t, "comment": comment, "columns": cols, "pk": pk, "fks": fks,
                       "uniques": uniques, "indexes": indexes, "checks": [str(c) for c in checks if c]})

    enums = []
    if backend == "postgresql":
        try:
            enums = [{"name": e["name"], "labels": e["labels"]} for e in insp.get_enums(schema=schema or "public")]
        except Exception:
            enums = []

    extra = {"counts": {}, "distinct": {}}
    if counts or distinct:
        prep = engine.dialect.identifier_preparer
        with engine.connect() as conn:
            if counts:
                for t in tables:
                    q = f"SELECT COUNT(*) FROM {prep.quote(t['name'])}"
                    try:
                        extra["counts"][t["name"]] = conn.execute(sa.text(q)).scalar()
                    except Exception as e:
                        extra["counts"][t["name"]] = f"lỗi: {e.__class__.__name__}"
            for spec in distinct:
                if "." not in spec:
                    continue
                tname, cname = spec.split(".", 1)
                if not re.fullmatch(r"\w+", tname) or not re.fullmatch(r"\w+", cname):
                    continue
                q = (f"SELECT {prep.quote(cname)}, COUNT(*) FROM {prep.quote(tname)} "
                     f"GROUP BY {prep.quote(cname)} ORDER BY COUNT(*) DESC LIMIT 50")
                try:
                    extra["distinct"][spec] = [[str(r[0]), r[1]] for r in conn.execute(sa.text(q))]
                except Exception as e:
                    extra["distinct"][spec] = f"lỗi: {e.__class__.__name__}"
    return {"backend": backend, "tables": tables, "enums": enums, **extra}


TYPE_FAMILY = [
    ("int", re.compile(r"int|serial", re.I)),
    ("str", re.compile(r"char|text|string|unicode|citext|enum", re.I)),
    ("bool", re.compile(r"bool", re.I)),
    ("datetime", re.compile(r"timestamp|datetime", re.I)),
    ("date", re.compile(r"^date", re.I)),
    ("float", re.compile(r"float|numeric|decimal|double|real", re.I)),
    ("json", re.compile(r"json", re.I)),
    ("bytes", re.compile(r"blob|bytea|binary|largebinary", re.I)),
    ("uuid", re.compile(r"uuid", re.I)),
]


def family(t: str) -> str:
    for name, rx in TYPE_FAMILY:
        if rx.search(t or ""):
            return name
    return "?"


def compare(db, project_root: Path):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from scan_flask_app import PyModule, find_models, iter_files  # cùng thư mục script
    except (NameError, ImportError):
        sys.exit("--compare-models cần scan_flask_app.py cùng thư mục. Khi chạy qua stdin: xuất --format json "
                 "rồi so bằng --from-json ở nơi có đủ script.")

    mods = {}
    for p in iter_files(project_root, {".py"}):
        m = PyModule(project_root, p)
        mods[m.name] = m
    models = find_models(mods)
    dbt = {t["name"]: t for t in db["tables"]}
    issues = []
    for m in models:
        t = dbt.get(m["table"])
        if t is None:
            issues.append(("thiếu bảng trong DB", m["table"], f"model {m['class']} ({m['file']}) — cần migration hoặc tạo lại DB dev"))
            continue
        dbc = {c["name"]: c for c in t["columns"]}
        for c in m["columns"]:
            d = dbc.get(c["name"])
            if d is None:
                issues.append(("thiếu cột trong DB", f"{m['table']}.{c['name']}", f"model có ({c['type']}) — db.create_all() không thêm cột vào bảng đã tồn tại"))
                continue
            if bool(d["nullable"]) != bool(c["nullable"]) and not c["pk"]:
                issues.append(("lệch NULL", f"{m['table']}.{c['name']}", f"model nullable={c['nullable']}, DB nullable={d['nullable']}"))
            fm, fd = family(c["type"]), family(d["type"])
            if "?" not in (fm, fd) and fm != fd and not (fm == "str" and fd == "str"):
                issues.append(("lệch kiểu", f"{m['table']}.{c['name']}", f"model {c['type']} ↔ DB {d['type']}"))
        mcols = {c["name"] for c in m["columns"]}
        for name in dbc:
            if name not in mcols:
                issues.append(("cột chỉ có trong DB", f"{m['table']}.{name}", "model không khai báo — kiểm tra có còn dùng không"))
    mtables = {m["table"] for m in models}
    for name in dbt:
        if name not in mtables and name not in ("alembic_version",):
            issues.append(("bảng chỉ có trong DB", name, "không có model tương ứng"))
    return issues


def render_md(db, url_txt, issues):
    o = [f"# Schema DB ({db['backend']})", "", f"URL: `{url_txt}`", ""]
    for t in db["tables"]:
        cnt = db["counts"].get(t["name"])
        o.append(f"## {t['name']}" + (f" — {cnt} dòng" if cnt is not None else "") + (f" — {t['comment']}" if t["comment"] else ""))
        o += ["", "| Cột | Kiểu | NULL | Default | PK | Comment |", "|---|---|---|---|---|---|"]
        for c in t["columns"]:
            o.append(f"| {c['name']} | {c['type']} | {'✓' if c['nullable'] else ''} | {c['default'] or ''} | "
                     f"{'✓' if c['name'] in t['pk'] else ''} | {c['comment'] or ''} |")
        if t["fks"]:
            o.append("- FK: " + "; ".join(f"{', '.join(f['columns'])} → {f['ref']}" + (f" ON DELETE {f['ondelete']}" if f["ondelete"] else "") for f in t["fks"]))
        if t["uniques"]:
            o.append("- UNIQUE: " + "; ".join(", ".join(u) for u in t["uniques"]))
        if t["indexes"]:
            o.append("- INDEX: " + "; ".join(f"{i['name']}({', '.join(c for c in i['columns'] if c)}){' unique' if i['unique'] else ''}" for i in t["indexes"]))
        if t["checks"]:
            o.append("- CHECK: " + "; ".join(t["checks"]))
        o.append("")
    if db["enums"]:
        o += ["## Enum (PostgreSQL)", ""] + [f"- {e['name']}: {', '.join(e['labels'])}" for e in db["enums"]] + [""]
    if db["distinct"]:
        o += ["## Giá trị mã đang lưu", ""]
        for spec, vals in db["distinct"].items():
            o.append(f"- {spec}: " + (vals if isinstance(vals, str) else ", ".join(f"{v} ({n})" for v, n in vals)))
        o.append("")
    if issues is not None:
        o += ["## So với model", ""]
        if issues:
            o += ["| Loại | Ở đâu | Ghi chú |", "|---|---|---|"] + [f"| {a} | {b} | {c} |" for a, b, c in issues]
        else:
            o.append("Model và DB khớp (theo bảng, cột, NULL, nhóm kiểu).")
        o.append("")
    return "\n".join(o)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url")
    ap.add_argument("--from-json", type=Path, help="dùng schema đã xuất (--format json) thay vì kết nối DB")
    ap.add_argument("--env-file", type=Path, default=Path(".env"))
    ap.add_argument("--schema", default=None, help="schema PostgreSQL (mặc định: search_path)")
    ap.add_argument("--counts", action="store_true", help="đếm số dòng mỗi bảng")
    ap.add_argument("--distinct", nargs="*", default=[], help="bảng.cột cần liệt kê giá trị (mã trạng thái, vai trò…)")
    ap.add_argument("--compare-models", type=Path, help="gốc dự án để so với model")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("-o", "--out", type=Path)
    a = ap.parse_args()

    if a.from_json:
        db = json.loads(a.from_json.read_text(encoding="utf-8"))
        url_txt = db.pop("url", str(a.from_json))
        db.pop("model_issues", None)
        db.setdefault("counts", {}); db.setdefault("distinct", {}); db.setdefault("enums", [])
    else:
        engine, url = make_engine(get_url(a))
        url_txt = safe_url(url)
        try:
            db = dump(engine, a.schema, a.counts, a.distinct)
        except sa.exc.OperationalError as e:
            sys.exit(f"Không kết nối được DB ({url_txt}): {str(e.orig).splitlines()[0] if e.orig else e}")
    issues = compare(db, a.compare_models.resolve()) if a.compare_models else None
    if a.format == "json":
        text = json.dumps({"url": url_txt, **db, "model_issues": issues}, ensure_ascii=False, indent=2, default=str)
    else:
        text = render_md(db, url_txt, issues)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
        print(f"Đã ghi {a.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
