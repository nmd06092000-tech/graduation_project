#!/usr/bin/env python3
"""Kiểm tra sơ đồ sequence (.puml) theo quy ước của mẫu.

Cách dùng:  python check_puml.py file1.puml [file2.puml ...]
Kết quả:    [LỖI] = sai cấu trúc / sai quy ước bắt buộc, [CHÚ Ý] = nên xem lại.
Exit code 1 nếu có [LỖI].

Đây là kiểm tra quy ước, không thay cho kiểm tra cú pháp của PlantUML
(java -jar plantuml.jar -checkonly ...).
"""
import os
import re
import sys

KINDS = ["actor", "boundary", "control", "entity"]
DECL = re.compile(r"^(actor|boundary|control|entity|participant|database|collections|queue)\s+(\S+)(?:\s+as\s+(\S+))?\s*$")
ARROW = re.compile(r"^(\S+?)\s*(-->|->)\s*(\S+?)\s*(?::\s*(.*))?$")
FORBIDDEN = ["autonumber", "activate", "deactivate", "note ", "skinparam", "return ", "box ", "group "]


def check(path):
    errs, warns = [], []
    E = lambda n, m: errs.append(f"  [LỖI]  dòng {n}: {m}")
    W = lambda n, m: warns.append(f"  [CHÚ Ý] dòng {n}: {m}")
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read()
    if "\r\n" in raw:
        W(0, "file dùng CRLF; nên dùng LF")
    lines = raw.split("\n")
    body = [(i + 1, l.strip()) for i, l in enumerate(lines) if l.strip() and not l.strip().startswith("'")]
    if not body or not body[0][1].startswith("@startuml"):
        E(1, "phải bắt đầu bằng '@startuml <ユースケース名>'")
    else:
        name = body[0][1][len("@startuml"):].strip()
        stem = os.path.splitext(os.path.basename(path))[0]
        if not name:
            E(body[0][0], "thiếu tên sau @startuml (tên use case)")
        elif name != stem:
            W(body[0][0], f"tên @startuml '{name}' khác tên file '{stem}'")
    if not body or body[-1][1] != "@enduml":
        E(len(lines), "phải kết thúc bằng '@enduml'")

    kind_of, order, stack = {}, [], []
    seen_arrow = False
    for n, l in body[1:-1]:
        low = l.lower()
        for fb in FORBIDDEN:
            if low.startswith(fb.strip() + " ") or low == fb.strip():
                W(n, f"mẫu không dùng '{fb.strip()}' — giữ sơ đồ đơn giản như mẫu")
        m = DECL.match(l)
        if m:
            kind, name, alias = m.group(1), m.group(2), m.group(3)
            if seen_arrow:
                W(n, f"khai báo '{name}' sau khi đã có mũi tên; khai báo hết ở đầu")
            if kind not in KINDS:
                W(n, f"mẫu chỉ dùng actor/boundary/control/entity (đang dùng '{kind}')")
            kind_of[alias or name] = kind
            order.append((n, kind, name))
            continue
        if l.startswith("alt "):
            stack.append(["alt", n, False])
            if not l.rstrip().endswith("場合"):
                W(n, "điều kiện alt nên kết thúc bằng '〜場合'")
            continue
        if l.startswith("else"):
            if not stack:
                E(n, "'else' không nằm trong alt")
            else:
                stack[-1][2] = True
            if not l.rstrip().endswith("場合"):
                W(n, "điều kiện else nên kết thúc bằng '〜場合'")
            continue
        if l == "end":
            if not stack:
                E(n, "'end' thừa")
            else:
                st = stack.pop()
                if not st[2]:
                    W(st[1], "alt không có nhánh else — mẫu luôn ghi cả trường hợp thất bại")
            continue
        if l.startswith(("opt ", "loop ", "par ")):
            stack.append([l.split()[0], n, True])
            W(n, f"mẫu chỉ dùng alt/else; xem lại '{l.split()[0]}'")
            continue
        m = ARROW.match(l)
        if not m:
            W(n, f"không nhận ra dòng: {l}")
            continue
        seen_arrow = True
        a, arrow, b, msg = m.group(1), m.group(2), m.group(3), (m.group(4) or "").strip()
        for p in (a, b):
            if p not in kind_of:
                E(n, f"'{p}' chưa được khai báo")
        ka, kb = kind_of.get(a), kind_of.get(b)
        if not msg:
            W(n, "mũi tên không có nội dung (ví dụ: 表示する())")
        if ka == "boundary" and kb == "control":
            if arrow != "->":
                E(n, "màn hình → view phải là '->'")
            if not re.match(r"^(GET|POST|PUT|PATCH|DELETE)\(.*\)$", msg):
                E(n, "màn hình → view phải là GET() hoặc POST(項目，…)")
        if ka == "control" and kb == "boundary":
            if arrow != "-->":
                E(n, "view → màn hình (表示) phải là '-->'")
            if msg and not msg.endswith("()"):
                W(n, "nội dung view → màn hình nên kết thúc bằng '()' (表示する() / 「…」メッセージを表示する())")
            if "メッセージ" in msg and not re.search(r"「.+」メッセージを表示する\(\)$", msg):
                W(n, "thông báo nên viết dạng 「<nguyên văn>」メッセージを表示する()")
        if ka == "entity" and kb == "control" and arrow != "-->":
            E(n, "entity → view (kết quả trả về) phải là '-->'")
        if ka == "control" and kb == "entity" and arrow != "->":
            E(n, "view → entity phải là '->'")
        if ka == "actor" and arrow != "->":
            E(n, "thao tác của actor phải là '->'")
        if ka == "control" and kb == "control" and a != b and msg != "リダイレクト":
            W(n, "view → view khác nên là 'リダイレクト'")
        if re.search(r"\([^)]*,[^)]*\)", msg):
            E(n, "tham số trong () phải ngăn bằng dấu phẩy toàn khổ '，'")
    if stack:
        E(stack[-1][1], f"'{stack[-1][0]}' chưa đóng bằng 'end'")

    rank = {k: i for i, k in enumerate(KINDS)}
    last = -1
    for n, kind, name in order:
        r = rank.get(kind, 99)
        if r < last:
            W(n, f"thứ tự khai báo nên là actor → boundary → control → entity ('{name}')")
        last = max(last, r)
    declared = {k for k in kind_of}
    used = set()
    for n, l in body:
        m = ARROW.match(l)
        if m:
            used |= {m.group(1), m.group(3)}
    for p in sorted(declared - used):
        W(0, f"'{p}' được khai báo nhưng không dùng")
    return errs, warns


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    bad = False
    for p in sys.argv[1:]:
        errs, warns = check(p)
        status = "OK" if not errs and not warns else ("LỖI" if errs else "CHÚ Ý")
        print(f"{p}: {status}")
        for m in errs + warns:
            print(m)
        bad |= bool(errs)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
