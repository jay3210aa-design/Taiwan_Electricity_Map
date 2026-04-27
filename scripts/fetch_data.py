"""
TaiPower data fetcher — runs via GitHub Actions every 10 minutes.
Outputs: data.json in repo root (served by GitHub Pages).
"""
import re
import json
import time
import requests
from datetime import datetime, timezone, timedelta

try:
    import cloudscraper
    _session = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
    )
    print('cloudscraper session created')
except ImportError:
    _session = requests.Session()
    _session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'zh-TW,zh;q=0.9',
    })
    print('cloudscraper not found, using requests')

TAIPOWER_BASE  = 'https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/'
TAIPOWER_ENTRY = 'https://www.taipower.com.tw/tc/page.aspx?mid=206'
OPENDATA_BASE  = 'https://data.taipower.com.tw/opendata/apply/file/'
TW_TZ = timezone(timedelta(hours=8))

try:
    _session.get(TAIPOWER_ENTRY, timeout=15)
    print('session established')
except Exception as e:
    print(f'session init warning: {e}')


def fetch(url, retries=3, delay=8):
    """帶重試的 GET，自動去除 UTF-8 BOM，排除 HTML 錯誤頁"""
    last_err = ''
    for i in range(retries):
        try:
            res = _session.get(url, timeout=30)
            res.raise_for_status()
            # 用 utf-8-sig 自動去除 BOM（修正 d006001 解析失敗問題）
            text = res.content.decode('utf-8-sig')
            if text.strip().startswith('<'):
                raise ValueError('收到 HTML（被擋）')
            return text
        except Exception as e:
            last_err = str(e)
            if i < retries - 1:
                print(f'  retry {i+1}/{retries-1} after {delay}s... ({e})')
                time.sleep(delay)
    raise Exception(last_err)


# ── loadpara.txt 解析（原始 JS 格式）────────────────────────────
def parse_loadpara(text):
    m = re.search(r'var\s+loadInfo\s*=\s*\[([\s\S]*?)\]', text)
    if not m:
        raise ValueError('loadpara 格式錯誤')
    vals = re.findall(r'"([^"]*)"', m.group(1))
    if len(vals) < 3:
        raise ValueError('loadpara 資料不足')
    load_mw     = float(vals[0].replace(',', ''))
    capacity_mw = float(vals[2].replace(',', ''))
    util_rate   = round(load_mw / capacity_mw * 100, 1) if capacity_mw else 0
    spare_rate  = round((capacity_mw - load_mw) / load_mw * 100, 1) if load_mw else 0
    update_time = vals[3] if len(vals) > 3 else '--'
    return {
        'load':       round(load_mw / 10, 1),
        'capacity':   round(capacity_mw / 10, 1),
        'utilRate':   util_rate,
        'spareRate':  spare_rate,
        'updateTime': update_time,
    }


# ── d006001 開放資料解析（JSON 格式，含 BOM）────────────────────
def parse_d006001_json(text):
    data = json.loads(text)
    if isinstance(data, list):
        data = data[0]
    # 嘗試多種可能的欄位名稱
    def _f(d, *keys):
        for k in keys:
            if k in d:
                try: return float(str(d[k]).replace(',', ''))
                except: pass
        return None

    load_mw     = _f(data, 'curr_load', 'load', '尖峰負載')
    capacity_mw = _f(data, 'net_peak_supply_capacity', 'capacity', '淨尖峰供電能力')
    spare_rate  = _f(data, 'spare_capacity_rate', 'spareRate', '備轉容量率')
    update_time = data.get('update_time', data.get('updateTime', '--'))

    if load_mw is None or capacity_mw is None:
        raise ValueError(f'd006001 找不到負載欄位，keys={list(data.keys())}')

    util_rate = round(load_mw / capacity_mw * 100, 1) if capacity_mw else 0
    if spare_rate is None:
        spare_rate = round((capacity_mw - load_mw) / load_mw * 100, 1) if load_mw else 0

    # d006001 單位已是萬瓩
    if load_mw > 1000:          # 若值很大代表單位是 MW，需換算
        load_mw     = round(load_mw / 10, 1)
        capacity_mw = round(capacity_mw / 10, 1)

    return {
        'load':       load_mw,
        'capacity':   capacity_mw,
        'utilRate':   util_rate,
        'spareRate':  spare_rate,
        'updateTime': str(update_time),
    }


# ── d006001 開放資料解析（CSV 格式）─────────────────────────────
def parse_d006001_csv(text):
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        raise ValueError('d006001 CSV 資料不足')
    headers = [h.strip() for h in lines[0].split(',')]
    vals    = [v.strip() for v in lines[-1].split(',')]
    row = dict(zip(headers, vals))

    def _col(d, *keys):
        for k in keys:
            for dk in d:
                if k in dk:
                    try: return float(d[dk].replace(',', '').replace('%', ''))
                    except: pass
        return None

    load_mw     = _col(row, '尖峰負載', 'load', 'Load')
    capacity_mw = _col(row, '淨尖峰供電能力', '供電能力', 'capacity')
    spare_rate  = _col(row, '備轉容量率', 'spare_rate')
    update_time = row.get('更新時間', row.get('時間', '--'))

    if load_mw is None or capacity_mw is None:
        raise ValueError(f'd006001 CSV 找不到欄位，headers={headers}')

    util_rate = round(load_mw / capacity_mw * 100, 1) if capacity_mw else 0
    if spare_rate is None:
        spare_rate = round((capacity_mw - load_mw) / load_mw * 100, 1) if load_mw else 0

    return {
        'load':       round(load_mw, 1),
        'capacity':   round(capacity_mw, 1),
        'utilRate':   util_rate,
        'spareRate':  spare_rate,
        'updateTime': str(update_time),
    }


# ── 燃料別解析 ───────────────────────────────────────────────────
FUEL_NAMES = ['核能', '燃煤', '民營燃煤', '燃氣', '民營燃氣', '重油', '太陽能', '水力', '輕油', '風力', '汽電共生', '抽蓄']

def parse_loadfueltype(text):
    last_row = None
    for line in text.strip().splitlines():
        parts = line.strip().split(',')
        if len(parts) >= 13:
            last_row = parts
    if not last_row:
        raise ValueError('loadfueltype 無有效資料列')
    fuels = []
    for i, name in enumerate(FUEL_NAMES):
        try:
            mw = float(last_row[i + 1])
        except (ValueError, IndexError):
            mw = 0.0
        if mw > 0:
            fuels.append({'name': name, 'mw': round(mw, 1)})
    return fuels


def parse_fuel_json(text):
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError('燃料 JSON 非陣列格式')
    fuels = []
    for item in data:
        name = item.get('fuel_type', item.get('name', item.get('能源別', '')))
        mw   = None
        for k in ('gen_mw', 'mw', 'MW', '發電量', 'power'):
            if k in item:
                try: mw = float(str(item[k]).replace(',', '')); break
                except: pass
        if name and mw and mw > 0:
            fuels.append({'name': name, 'mw': round(mw, 1)})
    return fuels


def estimate_regions(total_load):
    ratios = {'北區': 0.42, '中區': 0.24, '南區': 0.29, '東區': 0.05}
    return {k: round(total_load * v, 1) for k, v in ratios.items()}


def main():
    now = datetime.now(TW_TZ).isoformat()
    result = {'fetchTime': now, 'error': None, 'fuels': [], 'regions': {}}

    # ── 負載資料（依序嘗試多個來源）────────────────────────────
    LOAD_SOURCES = [
        (TAIPOWER_BASE + 'loadpara.txt',                   'loadpara',      parse_loadpara),
        (OPENDATA_BASE + 'd006001/001.json',               'd006001-json',  parse_d006001_json),
        (OPENDATA_BASE + 'd006001/001.csv',                'd006001-csv',   parse_d006001_csv),
        ('https://data.gov.tw/api/v2/rest/datastore/19995','gov-open-data', parse_d006001_json),
    ]
    for url, label, parser in LOAD_SOURCES:
        try:
            text = fetch(url, retries=2, delay=5)
            result.update(parser(text))
            print(f'load OK ({label}): load={result["load"]} 萬瓩, spare={result["spareRate"]}%')
            break
        except Exception as e:
            result['error'] = str(e)
            print(f'load FAIL ({label}): {e}')

    # ── 燃料別資料 ───────────────────────────────────────────────
    FUEL_SOURCES = [
        (TAIPOWER_BASE + 'loadfueltype.csv',   'loadfueltype',      parse_loadfueltype),
        (TAIPOWER_BASE + 'loadfueltype_1.csv', 'loadfueltype_1',    parse_loadfueltype),
        (OPENDATA_BASE + 'd006003/001.json',   'd006003-json',      parse_fuel_json),
        (OPENDATA_BASE + 'd006003/001.csv',    'd006003-csv',       parse_loadfueltype),
    ]
    for url, label, parser in FUEL_SOURCES:
        try:
            text = fetch(url, retries=1, delay=3)
            fuels = parser(text)
            if fuels:
                result['fuels'] = fuels
                print(f'fuel OK ({label}): {len(fuels)} 能源別')
                break
        except Exception as e:
            print(f'fuel FAIL ({label}): {e}')
    else:
        print('所有 fuel 路徑均失敗')

    # ── 分區估算 ─────────────────────────────────────────────────
    result['regions'] = estimate_regions(result.get('load', 0))

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'data.json saved at {now}')


if __name__ == '__main__':
    main()
