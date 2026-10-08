import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import requests

DB_PATH = Path.home() / ".news_aggregator.db"

# Источники: имя → URL RSS-ленты
SOURCES = {
    "habr":     "https://habr.com/ru/rss/news/?fl=ru",
    "tass":     "https://tass.ru/rss/v2.xml",
    "rbc":      "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    "lenta":    "https://lenta.ru/rss/news",
    "ria":      "https://ria.ru/export/rss2/archive/index.xml",
}


# ---------- База ----------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news 08.10 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT NOT NULL UNIQUE,
            summary TEXT,
            published TEXT,
            fetched_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source ON news 08.10 (source)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_published ON news 08.10 (published)")
    conn.commit()
    return conn


def save_articles(conn, articles: list[dict]) -> int:
    cursor = conn.cursor()
    fetched_at = datetime.now().isoformat()
    saved = 0
    for a in articles:
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO news 08.10 (source, title, link, summary, published, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                a["source"], a["title"], a["link"],
                a.get("summary", ""), a.get("published", ""), fetched_at,
            ))
            saved += cursor.rowcount
        except sqlite3.Error as e:
            print(f"⚠ Ошибка: {e}")
    conn.commit()
    return saved


# ---------- Загрузка ----------

def fetch_feed(source: str, url: str) -> list[dict]:
    """Скачивает RSS-ленту и возвращает список статей."""
    try:
        # feedparser сам умеет ходить по URL, но с таймаутом и заголовками
        # надёжнее сначала requests
        response = requests.get(url, timeout=15, headers={"User-Agent": "NewsAggregator/1.0"})
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"❌ {source}: {e}")
        return []

    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        print(f"⚠ {source}: некорректный RSS")
        return []

    articles = []
    for entry in feed.entries:
        # Разные RSS отдают по-разному: link или links[0].href
        link = entry.get("link") or (entry.get("links") or [{}])[0].get("href", "")
        if not link:
            continue

        published = ""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            published = datetime(*entry.published_parsed[:6]).isoformat()

        articles.append({
            "source": source,
            "title": entry.get("title", "").strip(),
            "link": link,
            "summary": (entry.get("summary") or "")[:500],
            "published": published,
        })
    return articles


def cmd_fetch(conn, sources: list[str] | None):
    targets = sources or list(SOURCES.keys())
    total_new = 0
    total_fetched = 0

    for source in targets:
        if source not in SOURCES:
            print(f"⚠ Неизвестный источник: {source}")
            continue
        print(f"📡 {source}...", end=" ")
        articles = fetch_feed(source, SOURCES[source])
        new = save_articles(conn, articles)
        total_fetched += len(articles)
        total_new += new
        print(f"получено {len(articles)}, новых {new}")

    print(f"\n✅ Всего получено: {total_fetched}, новых в базе: {total_new}")


# ---------- Поиск ----------

def cmd_search(conn, query: str, source: str | None, days: int | None, limit: int):
    sql = "SELECT source, title, link, published FROM news 08.10 WHERE title LIKE ?"
    params: list = [f"%{query}%"]

    if source:
        sql += " AND source = ?"
        params.append(source)

    if days:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        sql += " AND (published >= ? OR published = '')"
        params.append(since)

    sql += " ORDER BY published DESC, id DESC LIMIT ?"
    params.append(limit)

    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()

    if not rows:
        print("Ничего не найдено.")
        return

    print(f"\n🔍 Найдено: {len(rows)}\n")
    for source, title, link, published in rows:
        date = published[:10] if published else "—"
        print(f"[{source:<6}] {date}  {title}")
        print(f"         {link}\n")


def cmd_latest(conn, limit: int, source: str | None):
    sql = "SELECT source, title, link, published FROM news 08.10"
    params: list = []
    if source:
        sql += " WHERE source = ?"
        params.append(source)
    sql += " ORDER BY published DESC, id DESC LIMIT ?"
    params.append(limit)

    cursor = conn.cursor()
    cursor.execute(sql, params)
    for source, title, link, published in cursor.fetchall():
        date = published[:16].replace("T", " ") if published else "—"
        print(f"[{source:<6}] {date}  {title}")


def cmd_sources(conn):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT source, COUNT(*) FROM news 08.10
        GROUP BY source ORDER BY COUNT(*) DESC
    """)
    rows = cursor.fetchall()
    print("\n📰 Источники в базе:\n")
    if not rows:
        print("Пока пусто. Запусти `fetch`.")
        return
    for source, count in rows:
        print(f"  {source:<8} {count:>5} статей")


def cmd_stats(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), COUNT(DISTINCT source), MIN(published), MAX(published) FROM news 08.10")
    total, sources, first, last = cursor.fetchone()
    print(f"\n📊 Статистика\n")
    print(f"Всего статей:    {total}")
    print(f"Источников:      {sources}")
    print(f"Период:          {first or '—'} → {last or '—'}")
    print(f"База:            {DB_PATH}")


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description="Агрегатор новостей из RSS")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="Скачать новости")
    p_fetch.add_argument("--sources", nargs="+", help="Только эти источники (habr, tass, ...)")

    p_search = sub.add_parser("search", help="Поиск по заголовкам")
    p_search.add_argument("query", help="Слово/фраза")
    p_search.add_argument("--source", help="Только из этого источника")
    p_search.add_argument("--days", type=int, help="Только за N последних дней")
    p_search.add_argument("--limit", type=int, default=20)

    p_latest = sub.add_parser("latest", help="Последние новости")
    p_latest.add_argument("--limit", type=int, default=20)
    p_latest.add_argument("--source", help="Только из этого источника")

    sub.add_parser("sources", help="Список источников с количеством")
    sub.add_parser("stats", help="Статистика базы")

    args = parser.parse_args()
    conn = init_db()

    try:
        if args.command == "fetch":
            cmd_fetch(conn, args.sources)
        elif args.command == "search":
            cmd_search(conn, args.query, args.source, args.days, args.limit)
        elif args.command == "latest":
            cmd_latest(conn, args.limit, args.source)
        elif args.command == "sources":
            cmd_sources(conn)
        elif args.command == "stats":
            cmd_stats(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()