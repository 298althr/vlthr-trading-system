import os, psycopg2
c = psycopg2.connect(os.environ.get("DB_URL", ""), sslmode="prefer")
cur = c.cursor()
cur.execute("SELECT column_name, is_nullable, data_type FROM information_schema.columns WHERE table_name='paper_trades' ORDER BY ordinal_position")
for r in cur.fetchall():
    print(r)
cur.close()
c.close()
