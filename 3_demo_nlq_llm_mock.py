# 3_demo_nlq_llm_mock.py - NLQ kiểu "hệ thống thật" (LLM được giả lập)
# Luồng: câu hỏi -> [1] retrieval trên KHO MÔ TẢ schema -> [2] dựng prompt
#        -> [3] LLM (mock) trả JSON -> [4] code sinh SQL + validate -> [5] chạy
# Điểm khác demo 2: bạn KHÔNG viết luật cho từng câu, chỉ cung cấp "sách giáo khoa" (CATALOG) + "hàng rào" (validate).
import math, re, sqlite3, json
from collections import Counter

TODAY = "2026-10-20"

# ---------- Dữ liệu mẫu: 2 bảng ----------
conn = sqlite3.connect(":memory:"); cur = conn.cursor()
cur.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, product TEXT, region TEXT, amount REAL, quantity INTEGER, year INTEGER)")
cur.executemany("INSERT INTO sales (product, region, amount, quantity, year) VALUES (?,?,?,?,?)", [
    ("Laptop", "Hà Nội", 15_000_000, 3, 2025), ("Phone", "Hà Nội", 8_000_000, 10, 2025),
    ("Laptop", "TP.HCM", 12_000_000, 2, 2024), ("Tablet", "TP.HCM", 5_000_000, 4, 2025),
    ("Phone", "TP.HCM", 9_000_000, 12, 2024),
])
cur.execute("CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT, department TEXT)")
cur.executemany("INSERT INTO employees (name, department) VALUES (?,?)",
                [("An", "Kinh doanh"), ("Bình", "Kinh doanh"), ("Chi", "Kỹ thuật")])

# ---------- KHO MÔ TẢ: do CON NGƯỜI viết (hoặc AI soạn nháp, người duyệt) ----------
CATALOG = [
    {"kind": "metric", "name": "revenue", "table": "sales", "expr": "SUM(amount)",
     "desc": "Doanh thu: tổng số tiền bán hàng (VND)", "syn": ["doanh thu", "doanh số"]},
    {"kind": "metric", "name": "units_sold", "table": "sales", "expr": "SUM(quantity)",
     "desc": "Số lượng bán: tổng số sản phẩm đã bán", "syn": ["số lượng bán", "bán được bao nhiêu"]},
    {"kind": "metric", "name": "headcount", "table": "employees", "expr": "COUNT(*)",
     "desc": "Số nhân viên: đếm nhân viên", "syn": ["số nhân viên", "bao nhiêu nhân viên", "nhân sự"]},
    {"kind": "dimension", "name": "product", "table": "sales", "expr": "product",
     "desc": "Sản phẩm bán ra", "syn": ["sản phẩm", "mặt hàng"]},
    {"kind": "dimension", "name": "region", "table": "sales", "expr": "region",
     "desc": "Khu vực bán hàng", "syn": ["khu vực", "vùng", "miền"]},
    {"kind": "dimension", "name": "department", "table": "employees", "expr": "department",
     "desc": "Phòng ban của nhân viên", "syn": ["phòng ban", "bộ phận"]},
    {"kind": "time", "name": "year", "table": "sales", "expr": "year",
     "desc": "Năm phát sinh giao dịch bán hàng", "syn": ["năm", "năm nay", "năm ngoái"]},
]
# Từ "bán chạy" cố ý mơ hồ: khai báo ở CẢ 2 chỉ số -> hệ thống phải hỏi lại
for e in CATALOG:
    if e["name"] in ("revenue", "units_sold"):
        e["syn"].append("bán chạy")
ALLOWED_TABLES = {"sales", "employees"}

# ---------- [1] RETRIEVAL trên kho mô tả (TF-IDF thay cho embedding) ----------
def tok(t): return re.findall(r"\w+", t.lower())
def entry_text(e): return f"{e['desc']} {' '.join(e['syn'])}"
docs = [tok(entry_text(e)) for e in CATALOG]
df = Counter(w for d in docs for w in set(d))
idf = {w: math.log((1 + len(docs)) / (1 + c)) + 1 for w, c in df.items()}
def vec(ts):
    tf = Counter(ts); return {w: tf[w] * idf[w] for w in tf if w in idf}
def cos(a, b):
    dot = sum(a[w] * b.get(w, 0) for w in a)
    na = math.sqrt(sum(v*v for v in a.values())); nb = math.sqrt(sum(v*v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0
dvecs = [vec(d) for d in docs]

def retrieve_schema(q, top_k=4, min_score=0.08):
    qv = vec(tok(q))
    s = sorted(((cos(qv, v), e) for v, e in zip(dvecs, CATALOG)), key=lambda x: -x[0])
    return [(sc, e) for sc, e in s[:top_k] if sc >= min_score]

# ---------- [2] DỰNG PROMPT ----------
def build_prompt(q, hits):
    lines = "\n".join(f"- [{e['kind']}] {e['name']} | bảng {e['table']} | {e['desc']} | đồng nghĩa: {', '.join(e['syn'])}"
                      for _, e in hits)
    return ("Bạn là bộ phân tích câu hỏi dữ liệu. Chỉ dùng các mục trong SCHEMA bên dưới.\n"
            f"Hôm nay là {TODAY}.\n"
            "Chỉ trả về JSON: {metric, group_by, year, top_n} hoặc {error} hoặc {clarify}.\n"
            "Nếu thiếu chỉ số, trả error. Nếu một từ khớp nhiều chỉ số, trả clarify (không đoán).\n\n"
            f"SCHEMA:\n{lines}\n\nCÂU HỎI: {q}")

# ---------- [3] LLM GIẢ LẬP: chỉ được nhìn thấy PROMPT, không nhìn thấy CATALOG ----------
def mock_llm(prompt):
    q = prompt.split("CÂU HỎI:")[1].strip().lower()
    today_year = int(re.search(r"Hôm nay là (\d{4})", prompt).group(1))
    items = []
    for line in prompt.split("SCHEMA:\n")[1].split("\n\nCÂU HỎI:")[0].split("\n"):
        m = re.match(r"- \[(\w+)\] (\w+) \| bảng (\w+) \| .*? \| đồng nghĩa: (.*)", line)
        if m: items.append({"kind": m[1], "name": m[2], "table": m[3], "syn": m[4].split(", ")})
    hit = lambda it: any(s in q for s in it["syn"] if s not in ("năm",))
    metrics = [i for i in items if i["kind"] == "metric" and hit(i)]
    if len(metrics) > 1:
        return json.dumps({"clarify": f"Ý bạn là {' hay '.join(m['name'] for m in metrics)}?"})
    if not metrics:
        return json.dumps({"error": "Không có chỉ số phù hợp trong schema."})
    metric = metrics[0]
    dims = [i for i in items if i["kind"] == "dimension" and i["table"] == metric["table"]
            and any(re.search(rf"(từng|theo|mỗi)\s+{s}|{s}\s+nào", q) for s in i["syn"])]
    year = None
    if any(i["kind"] == "time" for i in items):
        if "năm ngoái" in q: year = today_year - 1
        elif "năm nay" in q: year = today_year
        else:
            m = re.search(r"\b(20\d{2})\b", q); year = int(m[1]) if m else None
    top_n = 1 if re.search(r"(nhiều nhất|cao nhất|lớn nhất)", q) else None
    return json.dumps({"metric": metric["name"], "group_by": dims[0]["name"] if dims else None,
                       "year": year, "top_n": top_n}, ensure_ascii=False)

# ---------- [4] CODE sinh SQL từ JSON + kiểm tra an toàn (không tin LLM) ----------
def build_sql(spec):
    by = {e["name"]: e for e in CATALOG}
    m = by.get(spec["metric"])
    if not m or m["kind"] != "metric": return None            # whitelist theo kho mô tả
    g = by.get(spec["group_by"]) if spec.get("group_by") else None
    if g and (g["kind"] != "dimension" or g["table"] != m["table"]): return None
    sel = (f"{g['expr']}, " if g else "") + f"{m['expr']} AS total"
    sql, params = f"SELECT {sel} FROM {m['table']}", []
    if spec.get("year") and m["table"] == "sales":
        sql += " WHERE year = ?"; params.append(int(spec["year"]))
    if g: sql += f" GROUP BY {g['expr']}"
    if spec.get("top_n"): sql += f" ORDER BY total DESC LIMIT {int(spec['top_n'])}"
    return sql, params

def validate(sql):
    s = sql.strip().rstrip(";").lower()
    return s.startswith("select") and ";" not in s and set(re.findall(r"\bfrom\s+(\w+)", s)) <= ALLOWED_TABLES

# ---------- [5] PIPELINE ----------
def ask(q):
    hits = retrieve_schema(q)
    print("  [1] Schema lấy được:", [f"{e['name']}({sc:.2f})" for sc, e in hits])
    out = json.loads(mock_llm(build_prompt(q, hits)))
    print("  [3] LLM trả về    :", out)
    if "clarify" in out: return "  -> HỎI LẠI: " + out["clarify"]
    if "error" in out:   return "  -> TỪ CHỐI: " + out["error"]
    built = build_sql(out)
    if not built or not validate(built[0]): return "  -> Bị chặn ở lớp kiểm tra (spec không hợp lệ)."
    sql, params = built
    return f"  [4] SQL           : {sql} | tham số {params}\n  [5] Kết quả       : {cur.execute(sql, params).fetchall()}"

if __name__ == "__main__":
    for q in ["Doanh số theo mặt hàng năm ngoái?",
              "Doanh thu theo khu vực năm 2024?",
              "Số nhân viên theo phòng ban?",
              "Sản phẩm bán chạy nhất?",                 # mơ hồ -> hỏi lại
              "Lợi nhuận theo sản phẩm?"]:               # chưa định nghĩa -> từ chối
        print("=" * 64); print("Câu hỏi:", q); print(ask(q))
    print("=" * 64)
    print("Spec độc hại bị chặn:", build_sql({"metric": "x; DROP TABLE sales", "group_by": None}))
