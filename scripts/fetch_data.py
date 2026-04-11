"""
TaiPower data fetcher — runs via GitHub Actions every 10 minutes.
Outputs: data.json in repo root (served by GitHub Pages).

資料來源優先順序：
  load/capacity : loadpara.txt（加 Referer + XHR headers）→ d006001 開放資料 JSON
  fuel breakdown: loadfueltype.csv（目前仍可存取）
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
    print('cloudscraper not found, using requests')

_session.headers.update({
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept': '*/*',
})

TAIPOWER_BASE    = 'https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/'
TAIPOWER_ENTRY   = 'https://www.taipower.com.tw/d006/loadGraph/loadGraph/load_briefing_main.html'
TAIPOWER_D006001 = 'https://service.taipower.com.tw/data/opendata/apply/file/d006001/001.json'
TW_TZ = timezone(timedelta(hours=8))

# 建立 session / cookie（帶正確 Referer）
try:
    _session.get(TAIPOWER_ENTRY, timeout=15,
                 headers={'Referer': 'https://www.taipower.com.tw/'})
    print('session established')
except Exception as e:
    print(f'session init warning: {e}')


def fetch(url, retries=3, delay=8, extra_headers=None):
    """帶重試的 GET，自動排除 HTML 錯誤頁"""
    last_err = ''
    hdrs = extra_headers or {}
    for i in range(retries):
        try:
            res = _session.get(url, timeout=30, headers=hdrs)
            res.raise_for_status()
            res.encoding = 'utf-8'
            text = res.text
            if text.strip().startswith('<'):
                raise ValueError('收到 HTML（被擋）')
            return text
        except Exception as e:
            last_err = str(e)
            if i < retries - 1:
                print(f'  retry {i+1}/{retries-1} after {delay}s... ({e})')
                time.sleep(delay)
    raise Exception(last_err)


# loadpara.txt 需要模擬瀏覽器 XHR 行為
_LOADPARA_HEADERS = {
    'Referer':          TAIPOWER_ENTRY,
    'X-Requested-With': 'XMLHttpRequest',
    'Accept':           'text/plain, */*; q=0.01',
}


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


def parse_d006001(text):
    """從發電機組開放資料 JSON 計算系統負載與可供電力（備用來源）。

    各燃料類型「小計」行的 淨發電量(MW) 加總 = 系統負載。
    可供電力 = 實際有發電之燃料類型的 裝置容量(MW) 加總
    （排除太陽能等夜間輸出為 0 的燃料，避免虛增備轉率）。

    備注：所得備轉率為估計值，通常高於台電官方公告數字。
    """
    data = json.loads(text)
    rows = data.get('aaData', [])

    subtotals = [r for r in rows if '小計' in str(r.get('機組名稱', ''))]
    if not subtotals:
        raise ValueError('d006001 無小計資料列')

    def _mw(row, key):
        val = str(row.get(key, '0')).replace(',', '').strip()
        if val in ('-', '', 'None', 'nan'):
            return 0.0
        try:
            return float(val)
        except ValueError:
            return 0.0

    total_gen = sum(_mw(r, '淨發電量(MW)') for r in subtotals)
    # 只計算實際有發電的燃料類型裝置容量
    total_cap = sum(
        _mw(r, '裝置容量(MW)') for r in subtotals
        if _mw(r, '淨發電量(MW)') > 0
    )

    if total_gen == 0:
        raise ValueError('d006001 無有效發電量資料')

    util_rate  = round(total_gen / total_cap * 100, 1) if total_cap else 0
    spare_rate = round((total_cap - total_gen) / total_gen * 100, 1) if total_gen else 0

    dt_str      = data.get('DateTime', '')
    update_time = dt_str[11:16] if len(dt_str) >= 16 else '--'

    return {
        'load':       round(total_gen / 10, 1),
        'capacity':   round(total_cap / 10, 1),
        'utilRate':   util_rate,
        'spareRate':  spare_rate,
        'updateTime': update_time,
    }


# loadfueltype.csv 欄位順序（與台電 d006001 小計類型對應，驗證於 2026-04）
# 單位：萬瓩（÷10 = MW 百位數）
FUEL_NAMES = ['燃氣', '民營燃氣', '燃煤', '民營燃煤', '汽電共生', '重油',
              '太陽能', '風力', '水力', '儲能', '其它再生能源', '核能']


def parse_loadfueltype(text):
    """解析 loadfueltype.csv：無表頭，每列 = 時間 + 12 欄位值（萬瓩），取最後一筆"""
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


def estimate_regions(total_load):
    """台電無公開分區即時 API，以歷史比例估算"""
    ratios = {'北區': 0.42, '中區': 0.24, '南區': 0.29, '東區': 0.05}
    return {k: round(total_load * v, 1) for k, v in ratios.items()}


def main():
    now    = datetime.now(TW_TZ).isoformat()
    result = {'fetchTime': now, 'error': None, 'fuels': [], 'regions': {}}

    # ── load / capacity（loadpara.txt 優先；403 時改用 d006001）────────────
    got_load = False
    try:
        text = fetch(TAIPOWER_BASE + 'loadpara.txt',
                     retries=2, delay=5, extra_headers=_LOADPARA_HEADERS)
        result.update(parse_loadpara(text))
        print(f"loadpara OK: load={result['load']} 萬瓩, spare={result['spareRate']}%")
        got_load = True
    except Exception as e:
        result['error'] = str(e)
        print(f'loadpara FAIL: {e}')

    if not got_load:
        print('嘗試備用來源 d006001...')
        try:
            text   = fetch(TAIPOWER_D006001, retries=2, delay=5)
            parsed = parse_d006001(text)
            result.update(parsed)
            result['error'] = None
            print(f"d006001 OK: load={result['load']} 萬瓩, spare={result['spareRate']}% (估計值)")
        except Exception as e:
            result['error'] = f'd006001 FAIL: {e}'
            print(result['error'])

    # ── fuel breakdown（loadfueltype.csv）────────────────────────────────
    for url in [TAIPOWER_BASE + 'loadfueltype.csv',
                TAIPOWER_BASE + 'loadfueltype_1.csv']:
        try:
            text  = fetch(url, retries=1, delay=3)
            fuels = parse_loadfueltype(text)
            if fuels:
                result['fuels'] = fuels
                print(f"fuel OK ({url.split('/')[-1]}): {len(fuels)} 能源別")
                break
        except Exception as e:
            print(f'fuel FAIL ({url.split("/")[-1]}): {e}')
    else:
        print('所有 fuel 路徑均失敗')

    # ── regions（歷史比例估算）────────────────────────────────────────────
    result['regions'] = estimate_regions(result.get('load', 0))

    # ── write output ─────────────────────────────────────────────────────
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f'data.json saved at {now}')


if __name__ == '__main__':
    main()
