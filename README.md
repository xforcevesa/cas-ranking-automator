# CAS Ranking Automator (新锐期刊分区表爬虫工具)

基于 Python `requests` + `BeautifulSoup` 的新锐期刊分区表爬虫工具，无需浏览器即可抓取期刊信息。

## 功能特性

- ✅ 纯 requests 实现，无需 Selenium/浏览器
- ✅ 支持按关键词搜索期刊
- ✅ 支持按大类/小类学科浏览期刊
- ✅ 支持大类 + 小类 + 关键词组合筛选
- ✅ **布尔逻辑表达式**: AND, OR, NOT, 括号, 字段匹配
- ✅ 多维度匹配: 名称, ISSN, 出版商, 语言, 数据库, 分区, Top
- ✅ 提取完整期刊信息：
  - ISSN / eISSN
  - 出版商 (Publisher)
  - 语言
  - 数据库收录 (SCIE, Scopus 等)
  - 大类学科分区 (1区/2区/3区/4区)
  - 小类学科分区
  - 是否 Top 期刊
  - 期刊详情链接
- ✅ 多种输出格式：表格 / JSON / CSV
- ✅ 可配置请求延迟，避免被封禁
- ✅ 支持指定年份（如 2026）

## 安装依赖

```bash
pip install -r requirements.txt
```

## 快速开始

### 关键词搜索

```bash
# 基本搜索
python xr_scholar_scraper.py --keyword "Frontiers in Remote Sensing"

# 指定年份
python xr_scholar_scraper.py --keyword "Nature" --year 2026

# JSON 输出
python xr_scholar_scraper.py --keyword "IEEE" --output json

# 导出到 CSV
python xr_scholar_scraper.py --keyword "Springer" --output csv --output-file results.csv
```

### 布尔逻辑表达式

支持 `AND`, `OR`, `NOT`, `()` 以及 `field:value` 字段匹配。

```bash
# AND: 同时包含
python xr_scholar_scraper.py --keyword "Nature AND Science"

# OR: 包含任一
python xr_scholar_scraper.py --keyword "IEEE OR Springer"

# NOT: 排除
python xr_scholar_scraper.py --keyword "Nature NOT Reviews"

# 括号分组
python xr_scholar_scraper.py --keyword "(Nature OR Science) AND Top"

# 复杂表达式
python xr_scholar_scraper.py --keyword "(name:IEEE OR name:Springer) AND (1区 OR 2区) NOT Reviews"

# 字段匹配
python xr_scholar_scraper.py --keyword "name:Nature AND publisher:PORTFOLIO"
python xr_scholar_scraper.py --keyword "publisher:ELSEVIER AND 1区 AND Top"
```

**支持的字段**:
| 字段 | 说明 | 示例 |
|------|------|------|
| `name` | 期刊名称 | `name:Nature` |
| `issn` | ISSN | `issn:0028-0836` |
| `eissn` | eISSN | `eissn:1476-4687` |
| `publisher` | 出版商 | `publisher:ELSEVIER` |
| `language` | 语言 | `language:English` |
| `database` | 数据库收录 | `database:SCIE` |
| `major` | 大类学科 | `major:计算机科学` |
| `minor` | 小类学科 | `minor:人工智能` |
| `partition` | 分区 | `partition:1区` |
| `top` | Top期刊 | `top` |

### 按大类学科浏览

```bash
# 浏览某大类下的所有期刊
python xr_scholar_scraper.py --category-major "计算机科学"

# 大类 + 布尔关键词筛选
python xr_scholar_scraper.py --category-major "计算机科学" --keyword "name:IEEE AND 1区"

# 快速模式（仅列表信息，不获取详情）
python xr_scholar_scraper.py --category-major "医学" --fast --output csv -f medicine.csv
```

### 按小类学科浏览

```bash
# 浏览某小类下的所有期刊
python xr_scholar_scraper.py --category-minor "REMOTE SENSING"

# 小类 + 布尔关键词筛选
python xr_scholar_scraper.py --category-minor "COMPUTER SCIENCE, ARTIFICIAL INTELLIGENCE" --keyword "1区 AND Top"
```

### 组合使用

```bash
# 大类 + 小类 + 关键词
python xr_scholar_scraper.py --category-major "计算机科学" --category-minor "人工智能" --keyword "Neural"

# 列出所有可用分类
python xr_scholar_scraper.py --list-categories
```

## 命令行参数

| 参数 | 简写 | 说明 | 默认值 |
|------|------|------|--------|
| `--keyword` | `-k` | 搜索关键词（支持布尔表达式） | - |
| `--year` | `-y` | 分区年份 | 2026 |
| `--output` | `-o` | 输出格式: table/json/csv | table |
| `--output-file` | `-f` | 输出文件路径 | 无 |
| `--delay` | `-d` | 请求间隔（秒） | 1.0 |
| `--timeout` | `-t` | 请求超时（秒） | 30 |
| `--fast` | | 跳过详情页获取（更快） | False |
| `--category-major` | `-M` | 大类学科名称 | - |
| `--category-minor` | `-m` | 小类学科名称 | - |
| `--page-size` | `-ps` | 每页条数 (10/20/50/100) | 100 |
| `--list-categories` | `-l` | 列出所有分类并退出 | False |

## 输出示例

### 表格格式

```
============================================================
期刊名称: EXPERT SYSTEMS WITH APPLICATIONS
============================================================
ISSN:              0957-4174
eISSN:             1873-6793
出版商:            PERGAMON-ELSEVIER SCIENCE LTD
语言:              English
数据库收录:        Web of Science(SCIE)Scopus
详情链接:          https://www.xr-scholar.com/Journals/e9aBV

大类学科:
  英文: Computer Science
  中文: 计算机科学
  分区: 1 区
  Top:  是

小类学科:
  1. COMPUTER SCIENCE, ARTIFICIAL INTELLIGENCE (计算机：人工智能)
     分区: 2 区
  2. ENGINEERING, ELECTRICAL & ELECTRONIC (工程：电子与电气)
     分区: 1 区
  3. OPERATIONS RESEARCH & MANAGEMENT SCIENCE (运筹学与管理科学)
     分区: 2 区
```

### JSON 格式

```json
[
  {
    "name": "EXPERT SYSTEMS WITH APPLICATIONS",
    "issn": "0957-4174",
    "eissn": "1873-6793",
    "publisher": "PERGAMON-ELSEVIER SCIENCE LTD",
    "language": "English",
    "database_indexing": "Web of Science(SCIE)Scopus",
    "major_category": {
      "english_name": "Computer Science",
      "chinese_name": "计算机科学",
      "partition": "1 区",
      "is_top": true
    },
    "minor_categories": [
      {
        "english_name": "COMPUTER SCIENCE, ARTIFICIAL INTELLIGENCE",
        "chinese_name": "计算机：人工智能",
        "partition": "2 区"
      }
    ],
    "detail_url": "https://www.xr-scholar.com/Journals/e9aBV"
  }
]
```

## 作为 Python 模块使用

```python
from xr_scholar_scraper import XRScholarScraper, journals_to_json, filter_journals

scraper = XRScholarScraper(timeout=30, delay=1.0)

# 关键词搜索
journals = scraper.search_and_get_details("Nature", year=2026)

# 按大类浏览
journals = scraper.browse_and_get_details("eBR1kna3x3Ca9", "ZKY", year=2026, keyword="Nature")

# 布尔表达式过滤
from xr_scholar_scraper import filter_journals
filtered = filter_journals(journals, "1区 AND Top")

# 输出 JSON
print(journals_to_json(filtered))

# 访问期刊数据
for j in filtered:
    print(f"名称: {j.name}")
    print(f"ISSN: {j.issn}, eISSN: {j.eissn}")
    print(f"出版商: {j.publisher}")
    print(f"大类分区: {j.major_category.partition}")
    print(f"详情链接: {j.detail_url}")
```

## 注意事项

1. **请求频率**: 默认 1 秒延迟，请勿高频请求以免被封 IP
2. **大数据量**: 大类/小类下可能有数千本期刊，建议使用 `--fast` 模式或导出到文件
3. **布尔表达式**: 当关键词包含 `AND`/`OR`/`NOT` 时会自动启用本地过滤；否则使用服务端搜索
4. **网站结构变化**: 如网站改版导致解析失败，需更新解析逻辑

## License

MIT
