# 2_demo_nlq_rules.py - NLQ dạng luật: tách rõ HIỂU và ÁNH XẠ
# Luồng: hiểu (regex) -> ánh xạ (từ điển đồng nghĩa) -> sinh SQL tham số hóa -> validate -> chạy
import re, sqlite3

TODAY_YEAR = 2026   # phải truyền ngày hiện tại để hiểu "năm ngoái"

conn = sqlite3.connect(":memory:")
cur = conn.cursor()
cur.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, product TEXT, amount REAL, year INTEGER)")
cur.executemany("INSERT INTO sales (product, amount, year) VALUES (?, ?, ?)", [
    ("Laptop", 15_000_000, 2025), ("Phone", 8_000_000, 2025), ("Laptop", 12_000_000, 2024),
    ("Tablet", 5_000_000, 2025), ("Phone", 9_000_000, 2024),
])

# ---- Tri thức do con người cung cấp (lớp ngữ nghĩa) ----
METRIC_SYNONYMS = {"doanh thu": "revenue", "doanh số": "revenue", "bán chạy": "revenue"}
METRICS = {"revenue": "SUM(amount)"}                     # khái niệm -> biểu thức SQL
DIM_SYNONYMS = {"sản phẩm": "product", "mặt hàng": "product"}   # từ người dùng -> cột
ALLOWED_TABLES = {"sales"}

# ---- Bước A: HIỂU - tách các vai ngữ nghĩa từ câu hỏi ----
def understand(q):
    q = q.lower()
    spec = {"metric_word": None, "dim_word": None, "year": None, "top1": False}
    spec["metric_word"] = next((w for w in METRIC_SYNONYMS if w in q), None)
    spec["dim_word"] = next((w for w in DIM_SYNONYMS
                             if re.search(rf"(từng|theo|mỗi)\s+{w}|{w}\s+nào", q)), None)
    if "năm ngoái" in q:
        spec["year"] = TODAY_YEAR - 1
    elif "năm nay" in q:
        spec["year"] = TODAY_YEAR
    else:
        m = re.search(r"\b(20\d{2})\b", q)
        spec["year"] = int(m.group(1)) if m else None
    if re.search(r"(nhiều nhất|cao nhất|bán chạy nhất)", q):
        spec["top1"] = True
    return spec

# ---- Bước B: ÁNH XẠ - nối từ ngữ với cột / công thức thật ----
def link_schema(spec):
    metric = METRICS[METRIC_SYNONYMS[spec["metric_word"]]] if spec["metric_word"] else None
    group_by = DIM_SYNONYMS[spec["dim_word"]] if spec["dim_word"] else None
    if spec["top1"] and not group_by:
        group_by = "product"
    return {"metric": metric, "group_by": group_by, "year": spec["year"], "top1": spec["top1"]}

# ---- Bước C: sinh SQL tham số hóa ----
def build_sql(m):
    select = (f"{m['group_by']}, " if m["group_by"] else "") + f"{m['metric']} AS total"
    sql, params = f"SELECT {select} FROM sales", []
    if m["year"]:
        sql += " WHERE year = ?"; params.append(m["year"])
    if m["group_by"]:
        sql += f" GROUP BY {m['group_by']}"
    if m["top1"]:
        sql += " ORDER BY total DESC LIMIT 1"
    return sql, params

# ---- Bước D: kiểm tra an toàn bằng code ----
def validate(sql):
    s = sql.strip().rstrip(";").lower()
    if not s.startswith("select") or ";" in s:
        return False
    return set(re.findall(r"\bfrom\s+(\w+)", s)) <= ALLOWED_TABLES

# ---- Bước E: chạy ----
def answer(q):
    spec = understand(q)
    m = link_schema(spec)
    print("  [A] Hiểu    :", spec)
    print("  [B] Ánh xạ  :", m)
    if m["metric"] is None:
        return "  -> Từ chối: chưa có chỉ số này trong từ điển (không đoán)."
    sql, params = build_sql(m)
    if not validate(sql):
        return "  -> Câu truy vấn không an toàn, đã chặn."
    return f"  [C] SQL     : {sql}  | tham số: {params}\n  [E] Kết quả : {cur.execute(sql, params).fetchall()}"

if __name__ == "__main__":
    for q in ["Doanh thu từng sản phẩm năm 2025 là bao nhiêu?",
              "Doanh số theo mặt hàng năm ngoái?",
              "Sản phẩm nào có doanh thu cao nhất?",
              "Lợi nhuận theo sản phẩm?"]:
        print("=" * 60); print("Câu hỏi:", q); print(answer(q))
    print("=" * 60)
    print("validate('DROP TABLE sales') ->", validate("DROP TABLE sales"))
