import psycopg2, os
c = psycopg2.connect(os.environ['DB_URL'], sslmode='require')
cur = c.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='paper_trades' ORDER BY ordinal_position")
print(', '.join([r[0] for r in cur.fetchall()]))
c.close()
