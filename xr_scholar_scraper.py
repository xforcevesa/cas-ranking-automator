#!/usr/bin/env python3
"""
新锐期刊分区表爬虫工具 (XR-Scholar Journal Scraper)

A requests-based scraper for fetching journal information from xr-scholar.com,
including ISSN, eISSN, publisher, CAS partitions (大类/小类), and more.

Supports boolean logic expressions (AND, OR, NOT, parentheses) for keyword
and category filtering, with multi-dimensional matching across journal name,
ISSN, publisher, language, database indexing, and category names.

Usage:
    # Simple keyword search
    python xr_scholar_scraper.py --keyword "Nature" --year 2026

    # Boolean keyword: AND
    python xr_scholar_scraper.py --keyword "Nature AND (SCIE OR Top)" --year 2026

    # Boolean keyword: OR
    python xr_scholar_scraper.py --keyword "IEEE OR Springer" --year 2026

    # Boolean keyword: NOT
    python xr_scholar_scraper.py --keyword "Nature NOT Reviews" --year 2026

    # Boolean keyword: complex expression
    python xr_scholar_scraper.py --keyword "(Nature OR Science) AND (1区 OR Top) NOT Reviews" --year 2026

    # Browse by major category with boolean filter
    python xr_scholar_scraper.py --category-major "计算机科学" --keyword "IEEE AND (人工智能 OR 理论)" --year 2026

    # Browse by minor category
    python xr_scholar_scraper.py --category-minor "REMOTE SENSING" --keyword "1区 AND Top" --year 2026

    # Multi-dimensional: match name, publisher, etc.
    python xr_scholar_scraper.py --keyword "name:Nature AND publisher:PORTFOLIO" --year 2026

    # Export to CSV
    python xr_scholar_scraper.py --category-major "医学" --keyword "1区 AND Top" --output csv -f medicine_top.csv

    # Clear cache
    python xr_scholar_scraper.py --clear-cache
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ============================================================
# SQLite Cache Manager
# ============================================================

class CacheManager:
    """SQLite-based cache for fetched journal data."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database tables."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS journals (
                    journal_id TEXT PRIMARY KEY,
                    name TEXT,
                    issn TEXT,
                    eissn TEXT,
                    publisher TEXT,
                    language TEXT,
                    database_indexing TEXT,
                    major_en TEXT,
                    major_cn TEXT,
                    major_partition TEXT,
                    major_is_top INTEGER,
                    minor_categories TEXT,  -- JSON array
                    detail_url TEXT,
                    cached_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_cache (
                    cache_key TEXT PRIMARY KEY,
                    journal_ids TEXT,  -- JSON array
                    cached_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS category_cache (
                    cache_key TEXT PRIMARY KEY,
                    journal_ids TEXT,  -- JSON array
                    cached_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS category_list_cache (
                    cache_key TEXT PRIMARY KEY,
                    categories TEXT,  -- JSON array
                    cached_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_journals_cached_at ON journals(cached_at)")

    def get_journal(self, journal_id: str, ttl: int = 86400) -> Optional[dict]:
        """Get cached journal detail. Returns None if expired or not found."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM journals WHERE journal_id = ? AND cached_at > ?",
                (journal_id, time.time() - ttl)
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def save_journal(self, journal_id: str, data: dict):
        """Save journal detail to cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO journals
                (journal_id, name, issn, eissn, publisher, language, database_indexing,
                 major_en, major_cn, major_partition, major_is_top, minor_categories,
                 detail_url, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                journal_id,
                data.get("name", ""),
                data.get("issn", ""),
                data.get("eissn", ""),
                data.get("publisher", ""),
                data.get("language", ""),
                data.get("database_indexing", ""),
                data.get("major_en", ""),
                data.get("major_cn", ""),
                data.get("major_partition", ""),
                1 if data.get("major_is_top") else 0,
                json.dumps(data.get("minor_categories", []), ensure_ascii=False),
                data.get("detail_url", ""),
                time.time(),
            ))

    def get_search_results(self, cache_key: str, ttl: int = 86400) -> Optional[list[str]]:
        """Get cached search result journal IDs."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT journal_ids FROM search_cache WHERE cache_key = ? AND cached_at > ?",
                (cache_key, time.time() - ttl)
            ).fetchone()
            if row:
                return json.loads(row[0])
            return None

    def save_search_results(self, cache_key: str, journal_ids: list[str]):
        """Save search result journal IDs to cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO search_cache (cache_key, journal_ids, cached_at) VALUES (?, ?, ?)",
                (cache_key, json.dumps(journal_ids), time.time())
            )

    def get_category_results(self, cache_key: str, ttl: int = 86400) -> Optional[list[str]]:
        """Get cached category browse result journal IDs."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT journal_ids FROM category_cache WHERE cache_key = ? AND cached_at > ?",
                (cache_key, time.time() - ttl)
            ).fetchone()
            if row:
                return json.loads(row[0])
            return None

    def save_category_results(self, cache_key: str, journal_ids: list[str]):
        """Save category browse result journal IDs to cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO category_cache (cache_key, journal_ids, cached_at) VALUES (?, ?, ?)",
                (cache_key, json.dumps(journal_ids), time.time())
            )

    def get_category_list(self, cache_key: str, ttl: int = 86400) -> Optional[list[dict]]:
        """Get cached category list."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT categories FROM category_list_cache WHERE cache_key = ? AND cached_at > ?",
                (cache_key, time.time() - ttl)
            ).fetchone()
            if row:
                return json.loads(row[0])
            return None

    def save_category_list(self, cache_key: str, categories: list[dict]):
        """Save category list to cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO category_list_cache (cache_key, categories, cached_at) VALUES (?, ?, ?)",
                (cache_key, json.dumps(categories, ensure_ascii=False), time.time())
            )

    def clear(self):
        """Clear all cached data."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM journals")
            conn.execute("DELETE FROM search_cache")
            conn.execute("DELETE FROM category_cache")
            conn.execute("DELETE FROM category_list_cache")
        print(f"[+] Cache cleared: {self.db_path}")

    def stats(self) -> dict:
        """Get cache statistics."""
        with sqlite3.connect(self.db_path) as conn:
            journals = conn.execute("SELECT COUNT(*) FROM journals").fetchone()[0]
            searches = conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
            categories = conn.execute("SELECT COUNT(*) FROM category_cache").fetchone()[0]
            cat_lists = conn.execute("SELECT COUNT(*) FROM category_list_cache").fetchone()[0]
            size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            return {
                "journals": journals,
                "searches": searches,
                "categories": categories,
                "category_lists": cat_lists,
                "size_mb": round(size / 1024 / 1024, 2),
            }


# ============================================================
# Boolean Expression Parser
# ============================================================

class BoolExprParser:
    """
    Parse boolean expressions with AND, OR, NOT, parentheses.
    Supports multi-dimensional field matching with field:value syntax.

    Examples:
        "Nature AND Science"
        "Nature OR (Science AND Cell)"
        "NOT Reviews"
        "name:Nature AND publisher:ELSEVIER"
        "1区 AND Top"
        "(name:IEEE OR name:Springer) AND (1区 OR 2区)"
    """

    def __init__(self, expression: str):
        self.expression = expression.strip()
        self.pos = 0
        self.tokens = self._tokenize()

    def _tokenize(self) -> list[str]:
        """Tokenize the expression into keywords, operators, and parentheses."""
        tokens = []
        expr = self.expression
        i = 0
        while i < len(expr):
            if expr[i].isspace():
                i += 1
                continue
            if expr[i] in '()':
                tokens.append(expr[i])
                i += 1
            elif expr[i:i+3].upper() == 'AND':
                if i + 3 >= len(expr) or not expr[i+3].isalnum():
                    tokens.append('AND')
                    i += 3
                else:
                    j = i
                    while j < len(expr) and (expr[j].isalnum() or expr[j] in '_:'):
                        j += 1
                    tokens.append(expr[i:j])
                    i = j
            elif expr[i:i+2].upper() == 'OR':
                if i + 2 >= len(expr) or not expr[i+2].isalnum():
                    tokens.append('OR')
                    i += 2
                else:
                    j = i
                    while j < len(expr) and (expr[j].isalnum() or expr[j] in '_:'):
                        j += 1
                    tokens.append(expr[i:j])
                    i = j
            elif expr[i:i+3].upper() == 'NOT':
                if i + 3 >= len(expr) or not expr[i+3].isalnum():
                    tokens.append('NOT')
                    i += 3
                else:
                    j = i
                    while j < len(expr) and (expr[j].isalnum() or expr[j] in '_:'):
                        j += 1
                    tokens.append(expr[i:j])
                    i = j
            else:
                j = i
                while j < len(expr) and (expr[j].isalnum() or expr[j] in '_:.' or '\u4e00' <= expr[j] <= '\u9fff'):
                    j += 1
                if j > i:
                    tokens.append(expr[i:j])
                    i = j
                else:
                    i += 1

        # Insert implicit AND: between two keywords, or keyword and NOT, or ) and (, etc.
        final = []
        for idx, tok in enumerate(tokens):
            if idx > 0:
                prev = final[-1]
                # Need implicit AND if prev is keyword/), and current is keyword/NOT/(
                prev_is_operand = prev not in ('AND', 'OR', 'NOT', '(')
                curr_is_operand = tok not in ('AND', 'OR', 'NOT', ')')
                if prev_is_operand and (curr_is_operand or tok == 'NOT'):
                    final.append('AND')
                elif prev == ')' and tok == '(':
                    final.append('AND')
            final.append(tok)

        return final

    def parse(self) -> Callable[[dict], bool]:
        """Parse the expression and return a matcher function."""
        if not self.tokens:
            return lambda _: True

        result = self._parse_or()
        if self.pos < len(self.tokens):
            raise ValueError(f"Unexpected token at position {self.pos}: {self.tokens[self.pos]}")
        return result

    def _parse_or(self) -> Callable[[dict], bool]:
        left = self._parse_and()
        while self.pos < len(self.tokens) and self.tokens[self.pos] == 'OR':
            self.pos += 1
            right = self._parse_and()
            prev_left = left
            left = lambda ctx, l=prev_left, r=right: l(ctx) or r(ctx)
        return left

    def _parse_and(self) -> Callable[[dict], bool]:
        left = self._parse_not()
        while self.pos < len(self.tokens) and self.tokens[self.pos] == 'AND':
            self.pos += 1
            right = self._parse_not()
            prev_left = left
            left = lambda ctx, l=prev_left, r=right: l(ctx) and r(ctx)
        return left

    def _parse_not(self) -> Callable[[dict], bool]:
        if self.pos < len(self.tokens) and self.tokens[self.pos] == 'NOT':
            self.pos += 1
            operand = self._parse_not()
            return lambda ctx, o=operand: not o(ctx)
        return self._parse_primary()

    def _parse_primary(self) -> Callable[[dict], bool]:
        if self.pos >= len(self.tokens):
            raise ValueError("Unexpected end of expression")

        token = self.tokens[self.pos]

        if token == '(':
            self.pos += 1
            expr = self._parse_or()
            if self.pos >= len(self.tokens) or self.tokens[self.pos] != ')':
                raise ValueError("Missing closing parenthesis")
            self.pos += 1
            return expr

        # It's a keyword
        self.pos += 1
        return self._make_matcher(token)

    def _make_matcher(self, keyword: str) -> Callable[[dict], bool]:
        """Create a matcher function for a keyword, supporting field:value syntax."""
        keyword_lower = keyword.lower()

        # Check for field:value syntax
        if ':' in keyword:
            field_name, value = keyword.split(':', 1)
            field_name = field_name.strip().lower()
            value = value.strip().lower()
            return self._make_field_matcher(field_name, value)

        # Default: match across all dimensions
        return lambda ctx: self._match_any(ctx, keyword_lower)

    def _make_field_matcher(self, field_name: str, value: str) -> Callable[[dict], bool]:
        """Create a matcher for a specific field."""
        field_map = {
            'name': lambda ctx: ctx.get('name', '').lower(),
            'issn': lambda ctx: ctx.get('issn', '').lower(),
            'eissn': lambda ctx: ctx.get('eissn', '').lower(),
            'publisher': lambda ctx: ctx.get('publisher', '').lower(),
            'language': lambda ctx: ctx.get('language', '').lower(),
            'database': lambda ctx: ctx.get('database_indexing', '').lower(),
            'major': lambda ctx: f"{ctx.get('major_en', '')} {ctx.get('major_cn', '')}".lower(),
            'minor': lambda ctx: ' '.join(
                f"{m.get('en', '')} {m.get('cn', '')}"
                for m in ctx.get('minors', [])
            ).lower(),
            'partition': lambda ctx: ctx.get('partition', '').lower(),
            'top': lambda ctx: 'top' if ctx.get('is_top') else '',
        }

        getter = field_map.get(field_name)
        if not getter:
            # Unknown field, match across all
            return lambda ctx: self._match_any(ctx, value)

        return lambda ctx: value in getter(ctx)

    @staticmethod
    def _match_any(ctx: dict, keyword: str) -> bool:
        """Check if keyword matches any field in the context."""
        # Normalize keyword: remove spaces for Chinese partition matching
        keyword_norm = keyword.replace(' ', '')
        keyword_with_space = keyword
        if '区' in keyword_norm and ' ' not in keyword:
            keyword_with_space = f"{keyword_norm[0]} 区"

        # Check basic fields
        for key in ('name', 'issn', 'eissn', 'publisher', 'language', 'database_indexing', 'partition'):
            val = ctx.get(key, '').lower()
            if keyword in val or keyword_norm in val.replace(' ', '') or keyword_with_space in val:
                return True

        # Check major category
        major_en = ctx.get('major_en', '').lower()
        major_cn = ctx.get('major_cn', '').lower()
        if (keyword in major_en or keyword_norm in major_en.replace(' ', '') or
                keyword in major_cn or keyword_norm in major_cn.replace(' ', '')):
            return True

        # Check is_top
        if keyword == 'top' and ctx.get('is_top'):
            return True

        # Check minor categories
        for minor in ctx.get('minors', []):
            en = minor.get('en', '').lower()
            cn = minor.get('cn', '').lower()
            part = minor.get('partition', '').lower()
            if (keyword in en or keyword_norm in en.replace(' ', '') or
                    keyword in cn or keyword_norm in cn.replace(' ', '') or
                    keyword in part or keyword_norm in part.replace(' ', '') or
                    keyword_with_space in part):
                return True

        return False


# ============================================================
# Data Models
# ============================================================

@dataclass
class MinorCategory:
    """小类学科信息"""
    english_name: str = ""
    chinese_name: str = ""
    partition: str = ""


@dataclass
class MajorCategory:
    """大类学科信息"""
    english_name: str = ""
    chinese_name: str = ""
    partition: str = ""
    is_top: bool = False


@dataclass
class Journal:
    """期刊完整信息"""
    name: str = ""
    issn: str = ""
    eissn: str = ""
    publisher: str = ""
    language: str = ""
    database_indexing: str = ""
    major_category: MajorCategory = field(default_factory=MajorCategory)
    minor_categories: list[MinorCategory] = field(default_factory=list)
    detail_url: str = ""
    journal_id: str = ""
    partition: str = ""  # 新锐分区 from category listing
    is_top: bool = False  # Top from category listing


# ============================================================
# Core Scraper
# ============================================================

class XRScholarScraper:
    """新锐期刊分区表爬虫"""

    BASE_URL = "https://www.xr-scholar.com"
    SEARCH_URL = urljoin(BASE_URL, "/Journals/Search")
    CATEGORY_MAJOR_URL = urljoin(BASE_URL, "/Category/ZKY")
    CATEGORY_MINOR_URL = urljoin(BASE_URL, "/Category/JCR")
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    def __init__(self, timeout: int = 30, delay: float = 1.0, cache: Optional[CacheManager] = None, cache_ttl: int = 86400):
        self.timeout = timeout
        self.delay = delay
        self.cache = cache
        self.cache_ttl = cache_ttl
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def _get(self, url: str, params: Optional[dict] = None) -> requests.Response:
        """Send a GET request with rate limiting."""
        time.sleep(self.delay)
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response

    # --------------------------------------------------------
    # Keyword search
    # --------------------------------------------------------

    def search_journals(self, keyword: str, year: int = 2026) -> list[dict]:
        """Search for journals by keyword."""
        cache_key = f"search:{keyword}:{year}"

        # Check cache
        if self.cache:
            cached_ids = self.cache.get_search_results(cache_key, self.cache_ttl)
            if cached_ids is not None:
                journals = []
                for jid in cached_ids:
                    cached = self.cache.get_journal(jid, self.cache_ttl)
                    if cached:
                        journals.append(self._cached_to_result(cached))
                    else:
                        # Cache miss for individual journal, fall through to network
                        break
                else:
                    # All journals cached
                    print(f"[*] Search cache hit: '{keyword}' (year={year}) -> {len(journals)} journal(s)")
                    return journals

        # Network request
        params = {"year": year, "keyword": keyword}
        print(f"[*] Searching for '{keyword}' (year={year})...")
        response = self._get(self.SEARCH_URL, params=params)
        results = self._parse_search_results(response.text, year)

        # Save to cache
        if self.cache and results:
            self.cache.save_search_results(cache_key, [r["journal_id"] for r in results if r.get("journal_id")])

        return results

    def _cached_to_result(self, cached: dict) -> dict:
        """Convert cached journal dict to search result dict."""
        return {
            "name": cached.get("name", ""),
            "issn": cached.get("issn", ""),
            "eissn": cached.get("eissn", ""),
            "journal_id": cached.get("journal_id", ""),
            "detail_url": cached.get("detail_url", ""),
            "year": 2026,
        }

    def _parse_search_results(self, html: str, year: int) -> list[dict]:
        """Parse the search results page."""
        soup = BeautifulSoup(html, "html.parser")
        journals = []
        cards = soup.select(".card")
        for card in cards:
            table = card.find("table")
            if not table:
                continue
            rows = table.select("tr")
            for row in rows[1:]:
                cells = row.select("td")
                if len(cells) >= 3:
                    name = cells[0].get_text(strip=True)
                    issn_raw = cells[1].get_text(strip=True)
                    eissn_raw = cells[2].get_text(strip=True)
                    link = row.select_one("a[href*='Journals/']")
                    journal_id = ""
                    detail_url = ""
                    if link:
                        href = link.get("href", "")
                        detail_url = urljoin(self.BASE_URL, href) if href.startswith("/") else href
                        match = re.search(r"/Journals/(\w+)", href)
                        if match:
                            journal_id = match.group(1)
                    journals.append({
                        "name": name,
                        "issn": issn_raw if issn_raw != "-" else "",
                        "eissn": eissn_raw if eissn_raw != "-" else "",
                        "journal_id": journal_id,
                        "detail_url": detail_url,
                        "year": year,
                    })
        print(f"[+] Found {len(journals)} journal(s)")
        return journals

    # --------------------------------------------------------
    # Category listing (get all categories)
    # --------------------------------------------------------

    def list_major_categories(self, year: int = 2026) -> list[dict]:
        """List all major categories (大类学科)."""
        cache_key = f"cat_list:ZKY:{year}"
        if self.cache:
            cached = self.cache.get_category_list(cache_key, self.cache_ttl)
            if cached is not None:
                print(f"[*] Category list cache hit: major categories ({year})")
                return cached

        print(f"[*] Fetching major categories (year={year})...")
        response = self._get(self.CATEGORY_MAJOR_URL)
        cats = self._parse_category_list(response.text, year, "ZKY")

        if self.cache:
            self.cache.save_category_list(cache_key, cats)
        return cats

    def list_minor_categories(self, year: int = 2026) -> list[dict]:
        """List all minor categories (小类学科)."""
        cache_key = f"cat_list:JCR:{year}"
        if self.cache:
            cached = self.cache.get_category_list(cache_key, self.cache_ttl)
            if cached is not None:
                print(f"[*] Category list cache hit: minor categories ({year})")
                return cached

        print(f"[*] Fetching minor categories (year={year})...")
        response = self._get(self.CATEGORY_MINOR_URL)
        cats = self._parse_category_list(response.text, year, "JCR")

        if self.cache:
            self.cache.save_category_list(cache_key, cats)
        return cats

    def _parse_category_list(self, html: str, year: int, cat_type: str) -> list[dict]:
        """Parse category list page (handles pagination)."""
        soup = BeautifulSoup(html, "html.parser")
        categories = []

        # Parse current page
        categories.extend(self._extract_categories_from_page(soup, year, cat_type))

        # Check for more pages via pagination links
        base = self.CATEGORY_MAJOR_URL if cat_type == "ZKY" else self.CATEGORY_MINOR_URL
        total_pages = self._detect_total_pages(soup)

        if total_pages > 1:
            for page in range(2, total_pages + 1):
                response = self._get(base, params={"year": year, "pn": page})
                soup2 = BeautifulSoup(response.text, "html.parser")
                new_cats = self._extract_categories_from_page(soup2, year, cat_type)
                categories.extend(new_cats)
                if not new_cats:
                    break

        print(f"[+] Found {len(categories)} categories")
        return categories

    def _detect_total_pages(self, soup) -> int:
        """Detect total number of pages from pagination links."""
        max_page = 1
        # Check pagination text like "显示第1–20条，共X条"
        page_text = soup.get_text()
        match = re.search(r"显示第\d+–\d+条，共(\d+)条", page_text)
        if match:
            total_items = int(match.group(1))
            # Each page shows ~20 items
            max_page = (total_items + 19) // 20

        # Also check page number links
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            pn_match = re.search(r"pn=(\d+)", href)
            if pn_match:
                page_num = int(pn_match.group(1))
                max_page = max(max_page, page_num)

        return max_page

    def _extract_categories_from_page(self, soup, year: int, cat_type: str) -> list[dict]:
        """Extract category entries from a single page."""
        categories = []
        tables = soup.find_all("table")
        for table in tables:
            rows = table.select("tr")
            for row in rows[1:]:
                cells = row.select("td")
                if len(cells) >= 4:
                    link = row.select_one("a[href]")
                    href = link.get("href", "") if link else ""
                    categories.append({
                        "english_name": cells[1].get_text(strip=True),
                        "chinese_name": cells[2].get_text(strip=True),
                        "journal_count": cells[3].get_text(strip=True),
                        "url": urljoin(self.BASE_URL, href) if href.startswith("/") else href,
                        "category_id": self._extract_category_id(href),
                        "year": year,
                        "type": cat_type,
                    })
        return categories

    @staticmethod
    def _extract_category_id(href: str) -> str:
        """Extract category ID from URL like /Category/ZKY/Journals/2026/nPJWaYlAVgSMW"""
        match = re.search(r"/Journals/\d+/(\w+)", href)
        return match.group(1) if match else ""

    # --------------------------------------------------------
    # Category journal listing (with pagination + keyword)
    # --------------------------------------------------------

    def browse_category(self, category_id: str, cat_type: str = "ZKY",
                        year: int = 2026, keyword: str = "",
                        page_size: int = 100) -> list[dict]:
        """
        Browse journals in a category with pagination.

        Args:
            category_id: Category short ID
            cat_type: "ZKY" for 大类, "JCR" for 小类
            year: Partition year
            keyword: Optional keyword filter (passed to server)
            page_size: Items per page (10/20/50/100)

        Returns:
            List of journal dicts from the category listing
        """
        cache_key = f"cat:{cat_type}:{category_id}:{year}:{keyword}:{page_size}"

        # Check cache
        if self.cache:
            cached_ids = self.cache.get_category_results(cache_key, self.cache_ttl)
            if cached_ids is not None:
                journals = []
                for jid in cached_ids:
                    cached = self.cache.get_journal(jid, self.cache_ttl)
                    if cached:
                        journals.append(self._cached_to_result(cached))
                    else:
                        break
                else:
                    print(f"[*] Category cache hit: '{category_id}' ({cat_type}, year={year}) -> {len(journals)} journal(s)")
                    return journals

        base = self.CATEGORY_MAJOR_URL if cat_type == "ZKY" else self.CATEGORY_MINOR_URL
        base_url = f"{base}/Journals/{year}/{category_id}"

        print(f"[*] Browsing category '{category_id}' (type={cat_type}, year={year}, keyword='{keyword}')...")

        all_journals = []
        page = 1

        while True:
            params = {"ps": page_size}
            if keyword:
                params["keyword"] = keyword
            if page > 1:
                params["pn"] = page

            response = self._get(base_url, params=params)
            journals, has_more = self._parse_category_journals(response.text, year, cat_type, page)
            all_journals.extend(journals)

            if not has_more:
                break
            page += 1

        print(f"[+] Found {len(all_journals)} journal(s) in category")

        # Save to cache
        if self.cache and all_journals:
            self.cache.save_category_results(cache_key, [j["journal_id"] for j in all_journals if j.get("journal_id")])

        return all_journals

    def _parse_category_journals(self, html: str, year: int, cat_type: str, current_page: int = 1) -> tuple[list[dict], bool]:
        """Parse a category journal listing page. Returns (journals, has_more_pages)."""
        soup = BeautifulSoup(html, "html.parser")
        journals = []

        table = soup.find("table")
        if not table:
            return [], False

        rows = table.select("tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) < 5:
                continue

            # Columns: #, 刊名, ISSN, EISSN, 新锐分区, Top
            name_cell = cells[1]
            issn_raw = cells[2].get_text(strip=True)
            eissn_raw = cells[3].get_text(strip=True)
            partition = cells[4].get_text(strip=True) if len(cells) > 4 else ""
            is_top = False
            if len(cells) > 5:
                top_raw = cells[5].get_text(strip=True)
                is_top = "Top" in top_raw or "TOP" in top_raw

            # Extract name and detail link
            link = name_cell.select_one("a[href*='Journals/']")
            name = name_cell.get_text(strip=True)
            journal_id = ""
            detail_url = ""
            if link:
                name = link.get_text(strip=True)
                href = link.get("href", "")
                detail_url = urljoin(self.BASE_URL, href) if href.startswith("/") else href
                match = re.search(r"/Journals/(\w+)", href)
                if match:
                    journal_id = match.group(1)

            journals.append({
                "name": name,
                "issn": issn_raw if issn_raw != "-" else "",
                "eissn": eissn_raw if eissn_raw != "-" else "",
                "journal_id": journal_id,
                "detail_url": detail_url,
                "year": year,
                "partition": partition,
                "is_top": is_top,
                "source_category_type": cat_type,
            })

        # Check for more pages by looking for next page link
        has_more = False
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            text = a.get_text(strip=True)
            pn_match = re.search(r"pn=(\d+)", href)
            if pn_match:
                page_num = int(pn_match.group(1))
                if page_num == current_page + 1:
                    has_more = True
                    break

        # Also check pagination text like "显示第1–20条，共118条"
        if not has_more:
            page_text = soup.get_text()
            match = re.search(r"显示第\d+–(\d+)条，共(\d+)条", page_text)
            if match:
                shown = int(match.group(1))
                total = int(match.group(2))
                has_more = shown < total

        return journals, has_more

    # --------------------------------------------------------
    # Journal detail page
    # --------------------------------------------------------

    def get_journal_detail(self, journal_id: str, year: int = 2026) -> Optional[Journal]:
        """Fetch detailed information for a specific journal."""
        # Check cache
        if self.cache:
            cached = self.cache.get_journal(journal_id, self.cache_ttl)
            if cached:
                print(f"[*] Journal detail cache hit: {journal_id}")
                return self._cached_to_journal(cached)

        url = urljoin(self.BASE_URL, f"/Journals/{journal_id}")
        print(f"[*] Fetching details for journal ID: {journal_id}...")
        response = self._get(url)
        journal = self._parse_journal_detail(response.text, url)

        # Save to cache
        if self.cache and journal:
            self.cache.save_journal(journal_id, self._journal_to_cache_data(journal))

        return journal

    def _cached_to_journal(self, cached: dict) -> Journal:
        """Convert cached dict to Journal object."""
        import json as _json
        minors_data = _json.loads(cached.get("minor_categories", "[]")) if cached.get("minor_categories") else []
        return Journal(
            name=cached.get("name", ""),
            issn=cached.get("issn", ""),
            eissn=cached.get("eissn", ""),
            publisher=cached.get("publisher", ""),
            language=cached.get("language", ""),
            database_indexing=cached.get("database_indexing", ""),
            major_category=MajorCategory(
                english_name=cached.get("major_en", ""),
                chinese_name=cached.get("major_cn", ""),
                partition=cached.get("major_partition", ""),
                is_top=bool(cached.get("major_is_top", 0)),
            ),
            minor_categories=[
                MinorCategory(**m) for m in minors_data
            ],
            detail_url=cached.get("detail_url", ""),
            journal_id=cached.get("journal_id", ""),
        )

    def _journal_to_cache_data(self, journal: Journal) -> dict:
        """Convert Journal object to cache dict."""
        return {
            "journal_id": journal.journal_id,
            "name": journal.name,
            "issn": journal.issn,
            "eissn": journal.eissn,
            "publisher": journal.publisher,
            "language": journal.language,
            "database_indexing": journal.database_indexing,
            "major_en": journal.major_category.english_name,
            "major_cn": journal.major_category.chinese_name,
            "major_partition": journal.major_category.partition,
            "major_is_top": journal.major_category.is_top,
            "minor_categories": [
                {
                    "english_name": m.english_name,
                    "chinese_name": m.chinese_name,
                    "partition": m.partition,
                }
                for m in journal.minor_categories
            ],
            "detail_url": journal.detail_url,
        }

    def _parse_journal_detail(self, html: str, url: str) -> Optional[Journal]:
        """Parse the journal detail page."""
        soup = BeautifulSoup(html, "html.parser")
        journal = Journal()
        journal.detail_url = url

        match = re.search(r"/Journals/(\w+)", url)
        if match:
            journal.journal_id = match.group(1)

        # Extract basic info from <dl>
        dl = soup.find("dl")
        if dl:
            for dt, dd in zip(dl.select("dt"), dl.select("dd")):
                label = dt.get_text(strip=True)
                value = dd.get_text(strip=True)
                if label == "刊名":
                    journal.name = value
                elif label == "ISSN" and value != "-":
                    journal.issn = value
                elif label == "EISSN" and value != "-":
                    journal.eissn = value
                elif label == "出版机构":
                    journal.publisher = value
                elif label == "语种":
                    journal.language = value
                elif label == "数据库":
                    journal.database_indexing = value

        # Extract major/minor categories from tables
        tables = soup.find_all("table")
        if tables:
            self._parse_major_table(tables[0], journal)
        if len(tables) > 1:
            self._parse_minor_table(tables[1], journal)

        return journal

    def _parse_major_table(self, table, journal: Journal):
        """Parse the 大类学科 table."""
        rows = table.select("tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) >= 4:
                mc = MajorCategory()
                mc.english_name = cells[0].get_text(strip=True)
                mc.chinese_name = cells[1].get_text(strip=True)
                partition_match = re.search(r"(\d+)\s*区", cells[2].get_text(strip=True))
                if partition_match:
                    mc.partition = f"{partition_match.group(1)} 区"
                top_raw = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                mc.is_top = "Top" in top_raw or "TOP" in top_raw
                journal.major_category = mc
                break

    def _parse_minor_table(self, table, journal: Journal):
        """Parse the 小类学科 table."""
        minors = []
        rows = table.select("tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) >= 3:
                minor = MinorCategory()
                minor.english_name = cells[0].get_text(strip=True)
                minor.chinese_name = cells[1].get_text(strip=True)
                partition_match = re.search(r"(\d+)\s*区", cells[2].get_text(strip=True))
                if partition_match:
                    minor.partition = f"{partition_match.group(1)} 区"
                minors.append(minor)
        journal.minor_categories = minors

    # --------------------------------------------------------
    # High-level: search + detail
    # --------------------------------------------------------

    def search_and_get_details(self, keyword: str, year: int = 2026) -> list[Journal]:
        """Search by keyword and fetch full details."""
        search_results = self.search_journals(keyword, year)
        if not search_results:
            print("[!] No journals found.")
            return []
        return self._resolve_journals(search_results, year)

    def browse_and_get_details(self, category_id: str, cat_type: str = "ZKY",
                               year: int = 2026, keyword: str = "") -> list[Journal]:
        """Browse a category and fetch full details."""
        results = self.browse_category(category_id, cat_type, year, keyword)
        if not results:
            print("[!] No journals found.")
            return []
        return self._resolve_journals(results, year)

    def _resolve_journals(self, results: list[dict], year: int) -> list[Journal]:
        """Fetch detail pages for each journal and merge data."""
        journals = []
        for result in results:
            journal_id = result.get("journal_id")
            if journal_id:
                detail = self.get_journal_detail(journal_id, year)
                if detail:
                    # Only merge listing data if detail is missing info
                    if not detail.name and result.get("name"):
                        detail.name = result["name"]
                    if not detail.eissn and result.get("eissn"):
                        detail.eissn = result["eissn"]
                    if not detail.issn and result.get("issn"):
                        detail.issn = result["issn"]
                    journals.append(detail)
            else:
                j = Journal(
                    name=result.get("name", ""),
                    issn=result.get("issn", ""),
                    eissn=result.get("eissn", ""),
                    partition=result.get("partition", ""),
                    is_top=result.get("is_top", False),
                    journal_id=result.get("journal_id", ""),
                    detail_url=result.get("detail_url", ""),
                )
                journals.append(j)
        return journals


# ============================================================
# Filtering
# ============================================================

def _build_journal_context(journal: Journal) -> dict:
    """Build a context dict for boolean expression matching."""
    return {
        'name': journal.name,
        'issn': journal.issn,
        'eissn': journal.eissn,
        'publisher': journal.publisher,
        'language': journal.language,
        'database_indexing': journal.database_indexing,
        'partition': journal.partition or journal.major_category.partition,
        'is_top': journal.is_top or journal.major_category.is_top,
        'major_en': journal.major_category.english_name,
        'major_cn': journal.major_category.chinese_name,
        'minors': [
            {
                'en': m.english_name,
                'cn': m.chinese_name,
                'partition': m.partition,
            }
            for m in journal.minor_categories
        ],
    }


def _build_result_context(result: dict) -> dict:
    """Build a context dict from a search/browse result for boolean matching."""
    return {
        'name': result.get('name', ''),
        'issn': result.get('issn', ''),
        'eissn': result.get('eissn', ''),
        'publisher': '',
        'language': '',
        'database_indexing': '',
        'partition': result.get('partition', ''),
        'is_top': result.get('is_top', False),
        'major_en': '',
        'major_cn': '',
        'minors': [],
    }


def _needs_full_details(expression: str) -> bool:
    """Check if a boolean expression needs full journal details for filtering."""
    if not expression.strip():
        return False
    # Fields only available in full journal details (not in listing results)
    detail_only_fields = {'publisher', 'language', 'database', 'major', 'minor'}
    # Check if expression references any detail-only fields
    for field_name in detail_only_fields:
        if field_name + ':' in expression.lower():
            return True
    # Also check for standalone partition/top keywords that need detail data
    # Listing results have partition and is_top, but search results don't
    if re.search(r'\b\d+\s*区\b', expression):
        return True
    if re.search(r'\bTop\b', expression, re.IGNORECASE):
        return True
    return False


def _extract_search_keywords(expression: str) -> str:
    """
    Extract searchable keywords from a boolean expression.
    Returns a combined keyword string for server-side search.
    Only extracts values from fields that are searchable on the server
    (name, issn, eissn, publisher). Ignores partition/top/major/minor
    as those are filtered locally.

    Examples:
        "publisher:ELSEVIER AND 1区 AND Top" -> "ELSEVIER"
        "name:Nature AND publisher:PORTFOLIO" -> "Nature PORTFOLIO"
        "1区 AND Top" -> "" (no searchable keywords)
    """
    searchable_fields = {'name', 'issn', 'eissn', 'publisher'}
    keywords = []
    # Find all field:value pairs
    for match in re.finditer(r'(\w+):(\S+)', expression):
        field_name = match.group(1).lower()
        value = match.group(2)
        if field_name in searchable_fields:
            keywords.append(value)
    return ' '.join(keywords)


def filter_journals(journals: list[Journal], expression: str) -> list[Journal]:
    """Filter journals using a boolean expression."""
    if not expression.strip():
        return journals

    try:
        parser = BoolExprParser(expression)
        matcher = parser.parse()
    except ValueError as e:
        print(f"[!] Invalid boolean expression: {e}")
        return journals

    filtered = []
    for j in journals:
        ctx = _build_journal_context(j)
        if matcher(ctx):
            filtered.append(j)

    print(f"[*] Filtered {len(journals)} -> {len(filtered)} journal(s) with expression: {expression}")
    return filtered


def filter_results(results: list[dict], expression: str) -> list[dict]:
    """Filter search/browse results using a boolean expression."""
    if not expression.strip():
        return results

    try:
        parser = BoolExprParser(expression)
        matcher = parser.parse()
    except ValueError as e:
        print(f"[!] Invalid boolean expression: {e}")
        return results

    filtered = []
    for r in results:
        ctx = _build_result_context(r)
        if matcher(ctx):
            filtered.append(r)

    print(f"[*] Filtered {len(results)} -> {len(filtered)} journal(s) with expression: {expression}")
    return filtered


# ============================================================
# Output Formatters
# ============================================================

def format_journal_table(journal: Journal) -> str:
    """Format a single journal as a readable table string."""
    lines = []
    lines.append(f"{'='*60}")
    lines.append(f"期刊名称: {journal.name}")
    lines.append(f"{'='*60}")
    lines.append(f"ISSN:              {journal.issn or 'N/A'}")
    lines.append(f"eISSN:             {journal.eissn or 'N/A'}")
    lines.append(f"出版商:            {journal.publisher or 'N/A'}")
    lines.append(f"语言:              {journal.language or 'N/A'}")
    lines.append(f"数据库收录:        {journal.database_indexing or 'N/A'}")
    lines.append(f"详情链接:          {journal.detail_url or 'N/A'}")
    lines.append(f"")
    lines.append(f"大类学科:")
    mc = journal.major_category
    if mc.english_name or mc.chinese_name or mc.partition:
        lines.append(f"  英文: {mc.english_name or 'N/A'}")
        lines.append(f"  中文: {mc.chinese_name or 'N/A'}")
        lines.append(f"  分区: {mc.partition or 'N/A'}")
        lines.append(f"  Top:  {'是' if mc.is_top else '否'}")
    else:
        lines.append(f"  N/A")
    lines.append(f"")
    lines.append(f"小类学科:")
    if journal.minor_categories:
        for i, minor in enumerate(journal.minor_categories, 1):
            lines.append(f"  {i}. {minor.english_name} ({minor.chinese_name})")
            lines.append(f"     分区: {minor.partition or 'N/A'}")
    else:
        lines.append(f"  N/A")
    lines.append(f"")
    return "\n".join(lines)


def journals_to_json(journals: list[Journal], indent: int = 2) -> str:
    """Convert journals list to JSON string."""
    data = []
    for j in journals:
        d = {
            "name": j.name,
            "issn": j.issn,
            "eissn": j.eissn,
            "publisher": j.publisher,
            "language": j.language,
            "database_indexing": j.database_indexing,
            "major_category": {
                "english_name": j.major_category.english_name,
                "chinese_name": j.major_category.chinese_name,
                "partition": j.major_category.partition,
                "is_top": j.major_category.is_top,
            },
            "minor_categories": [
                {
                    "english_name": m.english_name,
                    "chinese_name": m.chinese_name,
                    "partition": m.partition,
                }
                for m in j.minor_categories
            ],
            "detail_url": j.detail_url,
        }
        data.append(d)
    return json.dumps(data, ensure_ascii=False, indent=indent)


def journals_to_csv(journals: list[Journal], filepath: str):
    """Write journals list to CSV file."""
    if not journals:
        print("[!] No data to write.")
        return

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "期刊名称", "ISSN", "eISSN", "出版商", "语言", "数据库收录",
            "大类英文", "大类中文", "大类分区", "是否Top",
            "小类数量", "小类详情", "详情URL",
        ])
        for j in journals:
            minor_details = "; ".join(
                f"{m.english_name}({m.chinese_name}): {m.partition}"
                for m in j.minor_categories
            )
            writer.writerow([
                j.name, j.issn, j.eissn, j.publisher, j.language,
                j.database_indexing,
                j.major_category.english_name, j.major_category.chinese_name,
                j.major_category.partition,
                "是" if j.major_category.is_top else "否",
                len(j.minor_categories), minor_details, j.detail_url,
            ])
    print(f"[+] CSV saved to: {filepath}")


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="新锐期刊分区表爬虫工具 - Fetch journal partition data from xr-scholar.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Boolean Expression Syntax:
  Operators: AND, OR, NOT (case-insensitive)
  Grouping: ( ... )
  Field matching: field:value

  Fields: name, issn, eissn, publisher, language, database, major, minor, partition, top

  Examples:
    "Nature AND Science"
    "Nature OR (Science AND Cell)"
    "NOT Reviews"
    "name:Nature AND publisher:PORTFOLIO"
    "1区 AND Top"
    "(name:IEEE OR name:Springer) AND (1区 OR 2区)"
    "publisher:ELSEVIER AND (1区 OR Top) NOT Reviews"

Examples:
  # Simple keyword search
  %(prog)s --keyword "Frontiers in Remote Sensing"

  # Boolean keyword
  %(prog)s --keyword "(Nature OR Science) AND Top"

  # Browse by major category with boolean filter
  %(prog)s --category-major "计算机科学" --keyword "name:IEEE AND 1区"

  # Browse by minor category
  %(prog)s --category-minor "REMOTE SENSING" --keyword "1区 AND Top"

  # Export filtered results to CSV
  %(prog)s --category-major "医学" --keyword "1区 AND Top" --output csv -f medicine_top.csv

  # Fetch ALL journals (no filters)
  %(prog)s --fast
  %(prog)s --prefetch

  # Prefetch: fetch ALL matching journals and cache details
  %(prog)s --category-major "计算机科学" --prefetch
  %(prog)s --category-major "计算机科学" --keyword "1区 AND Top" --prefetch
  %(prog)s --keyword "Nature" --prefetch

  # Cache management
  %(prog)s --cache-stats
  %(prog)s --clear-cache
        """,
    )

    parser.add_argument("--keyword", "-k", help="Search keyword (supports boolean expressions: AND, OR, NOT, parentheses)")
    parser.add_argument("--year", "-y", type=int, default=2026, help="Partition year (default: 2026)")
    parser.add_argument("--output", "-o", choices=["table", "json", "csv"], default="table", help="Output format")
    parser.add_argument("--output-file", "-f", help="Output file path (for csv/json)")
    parser.add_argument("--delay", "-d", type=float, default=1.0, help="Request delay in seconds (default: 1.0)")
    parser.add_argument("--timeout", "-t", type=int, default=30, help="Request timeout in seconds (default: 30)")
    parser.add_argument("--fast", action="store_true", help="Skip detail page fetch (faster)")
    parser.add_argument("--category-major", "-M", help="Major category name (大类), e.g. '计算机科学'")
    parser.add_argument("--category-minor", "-m", help="Minor category name (小类), e.g. 'COMPUTER SCIENCE, ARTIFICIAL INTELLIGENCE'")
    parser.add_argument("--page-size", "-ps", type=int, default=100, choices=[10, 20, 50, 100], help="Items per page for category browse (default: 100)")
    parser.add_argument("--list-categories", "-l", action="store_true", help="List all available categories and exit")
    parser.add_argument("--cache-stats", action="store_true", help="Show cache statistics and exit")
    parser.add_argument("--clear-cache", action="store_true", help="Clear all cached data and exit")
    parser.add_argument("--no-cache", action="store_true", help="Disable caching for this run")
    parser.add_argument("--cache-ttl", type=int, default=86400, help="Cache TTL in seconds (default: 86400 = 24h)")
    parser.add_argument("--prefetch", "-p", action="store_true", help="Prefetch ALL matching journals and cache their details (supports all search modes)")

    args = parser.parse_args()

    # Cache management commands
    cache = None if args.no_cache else CacheManager()

    if args.cache_stats:
        if cache:
            stats = cache.stats()
            print("=== Cache Statistics ===")
            print(f"  Journals:        {stats['journals']}")
            print(f"  Search queries:  {stats['searches']}")
            print(f"  Category queries: {stats['categories']}")
            print(f"  Category lists:  {stats['category_lists']}")
            print(f"  Database size:   {stats['size_mb']} MB")
        else:
            print("Cache is disabled.")
        sys.exit(0)

    if args.clear_cache:
        if cache:
            cache.clear()
        else:
            print("Cache is disabled.")
        sys.exit(0)

    scraper = XRScholarScraper(timeout=args.timeout, delay=args.delay, cache=cache, cache_ttl=args.cache_ttl)

    try:
        # List categories mode
        if args.list_categories:
            print("=== 大类学科 (Major Categories) ===")
            for c in scraper.list_major_categories(args.year):
                print(f"  {c['chinese_name']} / {c['english_name']} (ID: {c['category_id']}, {c['journal_count']} journals)")
            print()
            print("=== 小类学科 (Minor Categories) ===")
            for c in scraper.list_minor_categories(args.year):
                print(f"  {c['chinese_name']} / {c['english_name']} (ID: {c['category_id']}, {c['journal_count']} journals)")
            sys.exit(0)

        # Determine mode
        has_keyword = bool(args.keyword and args.keyword.strip())
        has_major = bool(args.category_major and args.category_major.strip())
        has_minor = bool(args.category_minor and args.category_minor.strip())

        # No filters = fetch ALL journals across all major categories
        fetch_all = not has_keyword and not has_major and not has_minor

        # Prefetch mode: always fetch all details and cache, regardless of --fast
        if args.prefetch:
            print("[*] Prefetch mode: fetching ALL matching journals and caching details...")
            # Force non-fast, non-filtered browsing to get everything
            orig_fast = args.fast
            args.fast = False

        journals = []

        if fetch_all:
            # Fetch ALL journals across all major categories
            cats = scraper.list_major_categories(args.year)
            for c in cats:
                print(f"[*] Fetching all journals from {c['chinese_name']} ({c['english_name']})...")
                results = scraper.browse_category(c["category_id"], "ZKY", args.year, "", args.page_size)
                if args.prefetch:
                    print(f"[*] Prefetching {len(results)} journal details...")
                    all_j = scraper._resolve_journals(results, args.year)
                    journals.extend(all_j)
                elif args.fast:
                    journals.extend(_results_to_journals(results))
                else:
                    journals.extend(scraper._resolve_journals(results, args.year))

            # Deduplicate
            seen = set()
            unique = []
            for j in journals:
                key = j.journal_id or j.detail_url
                if key and key not in seen:
                    seen.add(key)
                    unique.append(j)
                elif not key:
                    unique.append(j)
            journals = unique

            if args.prefetch:
                print(f"[+] Prefetch complete: {len(journals)} journal(s) fetched and cached.")
                args.fast = orig_fast

        elif has_major or has_minor:
            # Category browse mode
            if has_major:
                # Find category ID by name
                cats = scraper.list_major_categories(args.year)
                cat_id = None
                for c in cats:
                    if args.category_major in (c["chinese_name"], c["english_name"], c["category_id"]):
                        cat_id = c["category_id"]
                        break
                if not cat_id:
                    print(f"[!] Major category '{args.category_major}' not found.")
                    print("Available categories:")
                    for c in cats:
                        print(f"  {c['chinese_name']} / {c['english_name']}")
                    sys.exit(1)

                # For boolean expressions or prefetch, browse without keyword and filter locally
                kw = "" if (has_keyword and (_has_boolean_ops(args.keyword) or args.prefetch)) else (args.keyword if has_keyword else "")

                if args.prefetch:
                    # Prefetch: browse all, resolve all details (cache them), then filter
                    results = scraper.browse_category(cat_id, "ZKY", args.year, kw, args.page_size)
                    print(f"[*] Prefetching {len(results)} journal details from major category...")
                    all_j = scraper._resolve_journals(results, args.year)
                    if has_keyword and _has_boolean_ops(args.keyword):
                        journals.extend(filter_journals(all_j, args.keyword))
                    else:
                        journals.extend(all_j)
                elif args.fast:
                    results = scraper.browse_category(cat_id, "ZKY", args.year, kw, args.page_size)
                    if has_keyword and _has_boolean_ops(args.keyword):
                        results = filter_results(results, args.keyword)
                    journals.extend(_results_to_journals(results))
                else:
                    results = scraper.browse_category(cat_id, "ZKY", args.year, kw, args.page_size)
                    if has_keyword and _has_boolean_ops(args.keyword):
                        # Need full details for boolean filtering
                        if _needs_full_details(args.keyword):
                            journals = scraper._resolve_journals(results, args.year)
                            journals = filter_journals(journals, args.keyword)
                        else:
                            results = filter_results(results, args.keyword)
                            journals.extend(scraper._resolve_journals(results, args.year))
                    else:
                        journals.extend(scraper._resolve_journals(results, args.year))

            if has_minor:
                cats = scraper.list_minor_categories(args.year)
                cat_id = None
                for c in cats:
                    if args.category_minor in (c["chinese_name"], c["english_name"], c["category_id"]):
                        cat_id = c["category_id"]
                        break
                if not cat_id:
                    print(f"[!] Minor category '{args.category_minor}' not found.")
                    print("Available categories:")
                    for c in cats:
                        print(f"  {c['chinese_name']} / {c['english_name']}")
                    sys.exit(1)

                kw = "" if (has_keyword and (_has_boolean_ops(args.keyword) or args.prefetch)) else (args.keyword if has_keyword else "")

                if args.prefetch:
                    results = scraper.browse_category(cat_id, "JCR", args.year, kw, args.page_size)
                    print(f"[*] Prefetching {len(results)} journal details from minor category...")
                    all_j = scraper._resolve_journals(results, args.year)
                    if has_keyword and _has_boolean_ops(args.keyword):
                        journals.extend(filter_journals(all_j, args.keyword))
                    else:
                        journals.extend(all_j)
                elif args.fast:
                    results = scraper.browse_category(cat_id, "JCR", args.year, kw, args.page_size)
                    if has_keyword and _has_boolean_ops(args.keyword):
                        results = filter_results(results, args.keyword)
                    journals.extend(_results_to_journals(results))
                else:
                    results = scraper.browse_category(cat_id, "JCR", args.year, kw, args.page_size)
                    if has_keyword and _has_boolean_ops(args.keyword):
                        if _needs_full_details(args.keyword):
                            journals = scraper._resolve_journals(results, args.year)
                            journals = filter_journals(journals, args.keyword)
                        else:
                            results = filter_results(results, args.keyword)
                            journals.extend(scraper._resolve_journals(results, args.year))
                    else:
                        journals.extend(scraper._resolve_journals(results, args.year))

            # Deduplicate by journal_id
            seen = set()
            unique = []
            for j in journals:
                key = j.journal_id or j.detail_url
                if key and key not in seen:
                    seen.add(key)
                    unique.append(j)
                elif not key:
                    unique.append(j)
            journals = unique

        elif has_keyword:
            # Keyword search mode
            # Check if keyword contains boolean operators
            if _has_boolean_ops(args.keyword):
                # Extract searchable keywords from expression for server-side search
                search_kw = _extract_search_keywords(args.keyword)

                if args.prefetch:
                    # For prefetch with boolean: if no searchable keywords, browse all categories
                    if not search_kw:
                        print("[*] No searchable keywords in expression, browsing all major categories for prefetch...")
                        cats = scraper.list_major_categories(args.year)
                        for c in cats:
                            results = scraper.browse_category(c["category_id"], "ZKY", args.year, "", args.page_size)
                            print(f"[*] Prefetching {len(results)} journal details from {c['chinese_name']}...")
                            all_j = scraper._resolve_journals(results, args.year)
                            journals.extend(filter_journals(all_j, args.keyword))
                        # Deduplicate
                        seen = set()
                        unique = []
                        for j in journals:
                            key = j.journal_id or j.detail_url
                            if key and key not in seen:
                                seen.add(key)
                                unique.append(j)
                            elif not key:
                                unique.append(j)
                        journals = unique
                    else:
                        # Has searchable keywords - search and resolve all
                        results = scraper.search_journals(search_kw, args.year)
                        print(f"[*] Prefetching {len(results)} journal details...")
                        all_j = scraper._resolve_journals(results, args.year)
                        journals = filter_journals(all_j, args.keyword)
                elif args.fast:
                    results = scraper.search_journals(search_kw, args.year)
                    results = filter_results(results, args.keyword)
                    journals = _results_to_journals(results)
                else:
                    # If expression needs fields only in detail pages, fetch details first
                    if _needs_full_details(args.keyword):
                        results = scraper.search_journals(search_kw, args.year)
                        journals = scraper._resolve_journals(results, args.year)
                        journals = filter_journals(journals, args.keyword)
                    else:
                        results = scraper.search_journals(search_kw, args.year)
                        results = filter_results(results, args.keyword)
                        journals = scraper._resolve_journals(results, args.year)

                # If no results and no searchable keywords, suggest category browsing
                if not journals and not search_kw and not args.prefetch:
                    print("[!] Tip: Use --category-major or --category-minor with this filter for better results.")
                    print("    Example: python xr_scholar_scraper.py --category-major \"计算机科学\" --keyword \"{}\"".format(args.keyword))
            else:
                # Simple keyword, use server-side search
                if args.prefetch:
                    results = scraper.search_journals(args.keyword, args.year)
                    print(f"[*] Prefetching {len(results)} journal details...")
                    journals = scraper._resolve_journals(results, args.year)
                elif args.fast:
                    results = scraper.search_journals(args.keyword, args.year)
                    journals = _results_to_journals(results)
                else:
                    journals = scraper.search_and_get_details(args.keyword, args.year)

        if args.prefetch:
            print(f"[+] Prefetch complete: {len(journals)} journal(s) fetched and cached.")
            # Restore original fast setting
            args.fast = orig_fast

        if not journals:
            print("[!] No journals found.")
            sys.exit(0)

        # Output
        if args.output == "table":
            for j in journals:
                print(format_journal_table(j))

        elif args.output == "json":
            json_str = journals_to_json(journals)
            if args.output_file:
                with open(args.output_file, "w", encoding="utf-8") as f:
                    f.write(json_str)
                print(f"[+] JSON saved to: {args.output_file}")
            else:
                print(json_str)

        elif args.output == "csv":
            filepath = args.output_file or "journals.csv"
            journals_to_csv(journals, filepath)

    except requests.exceptions.RequestException as e:
        print(f"[!] Request error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
        sys.exit(130)


def _has_boolean_ops(keyword: str) -> bool:
    """Check if a keyword contains boolean operators."""
    return bool(re.search(r'\b(AND|OR|NOT)\b', keyword, re.IGNORECASE))


def _results_to_journals(results: list[dict]) -> list[Journal]:
    """Convert search/browse result dicts to Journal objects."""
    journals = []
    for r in results:
        j = Journal(
            name=r.get("name", ""),
            issn=r.get("issn", ""),
            eissn=r.get("eissn", ""),
            partition=r.get("partition", ""),
            is_top=r.get("is_top", False),
            journal_id=r.get("journal_id", ""),
            detail_url=r.get("detail_url", ""),
        )
        journals.append(j)
    return journals


if __name__ == "__main__":
    main()
