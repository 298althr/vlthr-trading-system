import os, psycopg2
c = psycopg2.connect(os.environ.get("DB_URL", ""), sslmode="prefer")
cur = c.cursor()
cur.execute("""
    SELECT conname, pg_get_constraintdef(oid)
    FROM pg_constraint
    WHERE conrelid = 'paper_trades'::regclass
""")
for r in cur.fetchall():
    print(r)
cur.close()
c.close()
