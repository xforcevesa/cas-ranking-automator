"""
Automated tests for XR-Scholar Journal Scraper.

Tests cover:
1. Boolean expression parser (unit tests, no network)
2. HTML parsing logic (unit tests, no network)
3. End-to-end scraper tests (requires network, marked with @pytest.mark.network)
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xr_scholar_scraper import (
    BoolExprParser,
    Journal,
    MajorCategory,
    MinorCategory,
    XRScholarScraper,
    filter_journals,
    filter_results,
    format_journal_table,
    journals_to_json,
    _has_boolean_ops,
    _build_journal_context,
    _build_result_context,
)


# ============================================================
# Boolean Expression Parser Tests
# ============================================================

class TestBoolExprParser(unittest.TestCase):
    """Test the boolean expression parser."""

    def _make_ctx(self, **kwargs):
        """Helper to create a context dict."""
        ctx = {
            'name': '',
            'issn': '',
            'eissn': '',
            'publisher': '',
            'language': '',
            'database_indexing': '',
            'partition': '',
            'is_top': False,
            'major_en': '',
            'major_cn': '',
            'minors': [],
        }
        ctx.update(kwargs)
        return ctx

    def test_simple_keyword(self):
        """Test simple keyword matching."""
        p = BoolExprParser('Nature')
        m = p.parse()
        self.assertTrue(m(self._make_ctx(name='Nature Communications')))
        self.assertTrue(m(self._make_ctx(name='Nature')))
        self.assertFalse(m(self._make_ctx(name='Science')))

    def test_and_operator(self):
        """Test AND operator."""
        p = BoolExprParser('Nature AND Science')
        m = p.parse()
        self.assertTrue(m(self._make_ctx(name='Nature and Science')))
        self.assertFalse(m(self._make_ctx(name='Nature Communications')))
        self.assertFalse(m(self._make_ctx(name='Science Advances')))
        self.assertFalse(m(self._make_ctx(name='Cell')))

    def test_or_operator(self):
        """Test OR operator."""
        p = BoolExprParser('Nature OR Science')
        m = p.parse()
        self.assertTrue(m(self._make_ctx(name='Nature')))
        self.assertTrue(m(self._make_ctx(name='Science')))
        self.assertFalse(m(self._make_ctx(name='Cell')))

    def test_not_operator(self):
        """Test NOT operator."""
        p = BoolExprParser('Nature NOT Reviews')
        m = p.parse()
        self.assertTrue(m(self._make_ctx(name='Nature')))
        self.assertFalse(m(self._make_ctx(name='Nature Reviews')))
        self.assertTrue(m(self._make_ctx(name='Nature Communications')))

    def test_parentheses(self):
        """Test parentheses grouping."""
        p = BoolExprParser('(Nature OR Science) AND Top')
        m = p.parse()
        self.assertTrue(m(self._make_ctx(name='Nature', is_top=True)))
        self.assertFalse(m(self._make_ctx(name='Nature', is_top=False)))
        self.assertFalse(m(self._make_ctx(name='Cell', is_top=True)))

    def test_field_matching(self):
        """Test field:value syntax."""
        p = BoolExprParser('name:Nature AND publisher:PORTFOLIO')
        m = p.parse()
        self.assertTrue(m(self._make_ctx(name='Nature', publisher='NATURE PORTFOLIO')))
        self.assertFalse(m(self._make_ctx(name='Nature', publisher='ELSEVIER')))

    def test_partition_matching(self):
        """Test partition matching with/without spaces."""
        p = BoolExprParser('1区 AND Top')
        m = p.parse()
        self.assertTrue(m(self._make_ctx(partition='1 区', is_top=True)))
        self.assertFalse(m(self._make_ctx(partition='2 区', is_top=True)))
        self.assertFalse(m(self._make_ctx(partition='1 区', is_top=False)))

    def test_complex_expression(self):
        """Test complex boolean expression."""
        p = BoolExprParser('(name:IEEE OR name:Springer) AND (1区 OR 2区)')
        m = p.parse()
        self.assertTrue(m(self._make_ctx(name='IEEE Transactions', partition='1 区')))
        self.assertTrue(m(self._make_ctx(name='Springer Nature', partition='2 区')))
        self.assertFalse(m(self._make_ctx(name='Springer Nature', partition='3 区')))
        self.assertFalse(m(self._make_ctx(name='Elsevier', partition='1 区')))

    def test_implicit_and(self):
        """Test implicit AND (no explicit operator)."""
        p = BoolExprParser('Nature Top')
        m = p.parse()
        self.assertTrue(m(self._make_ctx(name='Nature', is_top=True)))
        self.assertFalse(m(self._make_ctx(name='Nature', is_top=False)))

    def test_empty_expression(self):
        """Test empty expression matches everything."""
        p = BoolExprParser('')
        m = p.parse()
        self.assertTrue(m(self._make_ctx(name='Anything')))

    def test_nested_parentheses(self):
        """Test nested parentheses."""
        p = BoolExprParser('((A OR B) AND (C OR D))')
        m = p.parse()
        # A matches first group, C matches second group
        self.assertTrue(m(self._make_ctx(name='A C')))
        # B matches first group, D matches second group
        self.assertTrue(m(self._make_ctx(name='B D')))
        # A matches first group, D matches second group
        self.assertTrue(m(self._make_ctx(name='A D')))
        # E matches neither group
        self.assertFalse(m(self._make_ctx(name='E F')))


# ============================================================
# Helper Function Tests
# ============================================================

class TestHelperFunctions(unittest.TestCase):
    """Test helper functions."""

    def test_has_boolean_ops(self):
        """Test boolean operator detection."""
        self.assertTrue(_has_boolean_ops('Nature AND Science'))
        self.assertTrue(_has_boolean_ops('Nature OR Science'))
        self.assertTrue(_has_boolean_ops('NOT Reviews'))
        self.assertTrue(_has_boolean_ops('Nature and Science'))  # case-insensitive
        self.assertFalse(_has_boolean_ops('Nature Science'))
        self.assertFalse(_has_boolean_ops('BRAND'))  # 'AND' inside word
        self.assertFalse(_has_boolean_ops(''))

    def test_build_journal_context(self):
        """Test journal context building."""
        j = Journal(
            name='Test Journal',
            issn='1234-5678',
            eissn='8765-4321',
            publisher='Test Publisher',
            language='English',
            database_indexing='SCIE',
            partition='1 区',
            is_top=True,
            major_category=MajorCategory(
                english_name='Computer Science',
                chinese_name='计算机科学',
                partition='1 区',
                is_top=True,
            ),
            minor_categories=[
                MinorCategory(
                    english_name='AI',
                    chinese_name='人工智能',
                    partition='1 区',
                )
            ],
        )
        ctx = _build_journal_context(j)
        self.assertEqual(ctx['name'], 'Test Journal')
        self.assertEqual(ctx['issn'], '1234-5678')
        self.assertEqual(ctx['publisher'], 'Test Publisher')
        self.assertEqual(ctx['partition'], '1 区')
        self.assertTrue(ctx['is_top'])
        self.assertEqual(ctx['major_en'], 'Computer Science')
        self.assertEqual(len(ctx['minors']), 1)

    def test_build_result_context(self):
        """Test result context building."""
        r = {
            'name': 'Test',
            'issn': '1234-5678',
            'eissn': '8765-4321',
            'partition': '2 区',
            'is_top': False,
        }
        ctx = _build_result_context(r)
        self.assertEqual(ctx['name'], 'Test')
        self.assertEqual(ctx['issn'], '1234-5678')
        self.assertEqual(ctx['partition'], '2 区')
        self.assertFalse(ctx['is_top'])
        self.assertEqual(ctx['publisher'], '')  # Results don't have publisher

    def test_filter_journals(self):
        """Test journal filtering."""
        journals = [
            Journal(name='Nature', partition='1 区', is_top=True),
            Journal(name='Science', partition='2 区', is_top=False),
            Journal(name='Cell', partition='1 区', is_top=True),
        ]
        filtered = filter_journals(journals, '1区 AND Top')
        self.assertEqual(len(filtered), 2)
        names = [j.name for j in filtered]
        self.assertIn('Nature', names)
        self.assertIn('Cell', names)

    def test_filter_results(self):
        """Test result filtering."""
        results = [
            {'name': 'Nature', 'partition': '1 区', 'is_top': True},
            {'name': 'Science', 'partition': '2 区', 'is_top': False},
        ]
        filtered = filter_results(results, '1区')
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['name'], 'Nature')


# ============================================================
# Output Formatter Tests
# ============================================================

class TestOutputFormatters(unittest.TestCase):
    """Test output formatting functions."""

    def test_format_journal_table(self):
        """Test journal table formatting."""
        j = Journal(
            name='Test Journal',
            issn='1234-5678',
            eissn='8765-4321',
            publisher='Test Publisher',
            language='English',
            database_indexing='SCIE',
            detail_url='https://example.com/journal/1',
            major_category=MajorCategory(
                english_name='Computer Science',
                chinese_name='计算机科学',
                partition='1 区',
                is_top=True,
            ),
            minor_categories=[
                MinorCategory(english_name='AI', chinese_name='人工智能', partition='1 区'),
            ],
        )
        output = format_journal_table(j)
        self.assertIn('Test Journal', output)
        self.assertIn('1234-5678', output)
        self.assertIn('8765-4321', output)
        self.assertIn('Test Publisher', output)
        self.assertIn('1 区', output)
        self.assertIn('https://example.com/journal/1', output)

    def test_journals_to_json(self):
        """Test JSON serialization."""
        journals = [
            Journal(
                name='Test Journal',
                issn='1234-5678',
                major_category=MajorCategory(partition='1 区', is_top=True),
            ),
        ]
        json_str = journals_to_json(journals)
        data = json.loads(json_str)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], 'Test Journal')
        self.assertEqual(data[0]['issn'], '1234-5678')
        self.assertEqual(data[0]['major_category']['partition'], '1 区')
        self.assertTrue(data[0]['major_category']['is_top'])


# ============================================================
# Scraper Unit Tests (No Network)
# ============================================================

class TestScraperParsing(unittest.TestCase):
    """Test scraper parsing logic with mock HTML."""

    def setUp(self):
        self.scraper = XRScholarScraper(delay=0)

    def test_parse_search_results(self):
        """Test search results parsing."""
        # The actual website has: 刊名, ISSN, EISSN (no # column in search results)
        html = '''
        <html><body>
        <div class="card">
            <table>
                <tr><th>期刊名称</th><th>ISSN</th><th>EISSN</th></tr>
                <tr>
                    <td><a href="/Journals/abc123">Test Journal</a></td>
                    <td>1234-5678</td>
                    <td>8765-4321</td>
                </tr>
            </table>
        </div>
        </body></html>
        '''
        results = self.scraper._parse_search_results(html, 2026)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'Test Journal')
        self.assertEqual(results[0]['issn'], '1234-5678')
        self.assertEqual(results[0]['eissn'], '8765-4321')
        self.assertEqual(results[0]['journal_id'], 'abc123')

    def test_parse_journal_detail(self):
        """Test journal detail parsing."""
        html = '''
        <html><body>
        <dl>
            <dt>刊名</dt><dd>Test Journal</dd>
            <dt>ISSN</dt><dd>1234-5678</dd>
            <dt>EISSN</dt><dd>8765-4321</dd>
            <dt>出版机构</dt><dd>Test Publisher</dd>
            <dt>语种</dt><dd>English</dd>
            <dt>数据库</dt><dd>Web of Science(SCIE)</dd>
        </dl>
        <table>
            <tr><th>英文名</th><th>中文名</th><th>新锐分区</th><th>Top</th></tr>
            <tr><td>Computer Science</td><td>计算机科学</td><td>1 区</td><td>Top</td></tr>
        </table>
        <table>
            <tr><th>英文名</th><th>中文名</th><th>新锐分区</th></tr>
            <tr><td>AI</td><td>人工智能</td><td>1 区</td></tr>
            <tr><td>Software Engineering</td><td>软件工程</td><td>2 区</td></tr>
        </table>
        </body></html>
        '''
        journal = self.scraper._parse_journal_detail(html, 'https://www.xr-scholar.com/Journals/abc123')
        self.assertEqual(journal.name, 'Test Journal')
        self.assertEqual(journal.issn, '1234-5678')
        self.assertEqual(journal.eissn, '8765-4321')
        self.assertEqual(journal.publisher, 'Test Publisher')
        self.assertEqual(journal.language, 'English')
        self.assertEqual(journal.database_indexing, 'Web of Science(SCIE)')
        self.assertEqual(journal.major_category.english_name, 'Computer Science')
        self.assertEqual(journal.major_category.partition, '1 区')
        self.assertTrue(journal.major_category.is_top)
        self.assertEqual(len(journal.minor_categories), 2)
        self.assertEqual(journal.minor_categories[0].english_name, 'AI')
        self.assertEqual(journal.minor_categories[1].partition, '2 区')

    def test_parse_category_list(self):
        """Test category list parsing."""
        html = '''
        <html><body>
        <table>
            <tr><th>#</th><th>英文名</th><th>中文名</th><th>期刊数量</th></tr>
            <tr>
                <td>1</td>
                <td><a href="/Category/ZKY/Journals/2026/abc123">Computer Science</a></td>
                <td><a href="/Category/ZKY/Journals/2026/abc123">计算机科学</a></td>
                <td><a href="/Category/ZKY/Journals/2026/abc123">779</a></td>
            </tr>
        </table>
        </body></html>
        '''
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        cats = self.scraper._extract_categories_from_page(soup, 2026, 'ZKY')
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]['english_name'], 'Computer Science')
        self.assertEqual(cats[0]['chinese_name'], '计算机科学')
        self.assertEqual(cats[0]['category_id'], 'abc123')

    def test_parse_category_journals(self):
        """Test category journals parsing."""
        html = '''
        <html><body>
        <table>
            <tr><th>#</th><th>刊名</th><th>ISSN</th><th>EISSN</th><th>新锐分区</th><th>Top</th></tr>
            <tr>
                <td>1</td>
                <td><a href="/Journals/abc123">Test Journal</a></td>
                <td>1234-5678</td>
                <td>8765-4321</td>
                <td>1 区</td>
                <td>Top</td>
            </tr>
        </table>
        <p>显示第1–1条，共1条</p>
        </body></html>
        '''
        journals, has_more = self.scraper._parse_category_journals(html, 2026, 'ZKY')
        self.assertEqual(len(journals), 1)
        self.assertEqual(journals[0]['name'], 'Test Journal')
        self.assertEqual(journals[0]['partition'], '1 区')
        self.assertTrue(journals[0]['is_top'])
        self.assertFalse(has_more)


# ============================================================
# Network Tests (marked with @pytest.mark.network)
# ============================================================

class TestScraperNetwork(unittest.TestCase):
    """Test scraper with real network requests."""

    @classmethod
    def setUpClass(cls):
        """Skip network tests if SKIP_NETWORK_TESTS is set."""
        if os.environ.get('SKIP_NETWORK_TESTS'):
            raise unittest.SkipTest('Network tests skipped')
        cls.scraper = XRScholarScraper(delay=0.5)

    @unittest.skipIf(os.environ.get('SKIP_NETWORK_TESTS'), 'Network tests skipped')
    def test_search_journals(self):
        """Test searching journals by keyword."""
        results = self.scraper.search_journals('Frontiers in Remote Sensing', 2026)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['name'], 'Frontiers in Remote Sensing')

    @unittest.skipIf(os.environ.get('SKIP_NETWORK_TESTS'), 'Network tests skipped')
    def test_get_journal_detail(self):
        """Test fetching journal detail."""
        journal = self.scraper.get_journal_detail('V0nA2', 2026)
        self.assertIsNotNone(journal)
        self.assertEqual(journal.name, 'Frontiers in Remote Sensing')
        self.assertEqual(journal.eissn, '2673-6187')
        self.assertIsNotNone(journal.publisher)
        self.assertIsNotNone(journal.language)

    @unittest.skipIf(os.environ.get('SKIP_NETWORK_TESTS'), 'Network tests skipped')
    def test_list_major_categories(self):
        """Test listing major categories."""
        cats = self.scraper.list_major_categories(2026)
        self.assertGreater(len(cats), 0)
        # Check that we have expected categories
        names = [c['chinese_name'] for c in cats]
        self.assertIn('计算机科学', names)

    @unittest.skipIf(os.environ.get('SKIP_NETWORK_TESTS'), 'Network tests skipped')
    def test_list_minor_categories(self):
        """Test listing minor categories."""
        cats = self.scraper.list_minor_categories(2026)
        self.assertGreater(len(cats), 0)


if __name__ == '__main__':
    unittest.main()
