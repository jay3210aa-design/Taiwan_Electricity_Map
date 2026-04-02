"""
TaiPower data fetcher — runs via GitHub Actions every 10 minutes.
Outputs: data.json in repo root (served by GitHub Pages).
"""
import re
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# 主要資料來源：台電 loadGraph（有 Cloudflare 保護，偶爾可過）
TAIPOWER_BASE  = 'https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/'
TAIPOWER_ENTRY = 'https://www.taipower.com.tw/tc/page.aspx?mid=206'
# 備用來源：台電開放資料平台（不同子網域，較少限制）
OPENDATA_BASE  = 'https://data.taipower.com.tw/opendata/apply/file/'
TW_TZ = timezone(timedelta(hours=8))

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'zh-TW,zh;q=0.9',
    'Referer': TAIPOWER_ENTRY,
}

_session = requests.Session()
_session.headers.update(HEADERS)
try:
    _session.get(TAIPOWER_ENTRY, timeout=15)
    print('session established')
except Exception as e:
    print(f'session init warning: {e}')


def fetch(url, retries=3, delay=8):
    """帶重試的 GET，自動排除 HTML 錯誤頁"""
    last_err = ''
    for i in range(retries):
        try:
            res = _session.get(url, timeout=30)
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
    if text.strip().startswith('<'):
        raise ValueError(f'{path} 回傳 HTML（可能被 Cloudflare 擋）')
    return text


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


FUEL_NAMES = ['核能', '燃煤', '民營燃煤', '燃氣', '民營燃氣', '重油', '太陽能', '水力', '輕油', '風力', '汽電共生', '抽蓄']


def parse_loadfueltype(text):
    """解析 loadfueltype.csv：無表頭，每列 = 時間 + 12 個燃料 MW 值，取最後一筆"""
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
    now = datetime.now(TW_TZ).isoformat()
    result = {'fetchTime': now, 'error': None, 'fuels': [], 'regions': {}}

    # --- loadpara (主要來源 → 開放資料備用) ---
    LOADPARA_CANDIDATES = [
        TAIPOWER_BASE + 'loadpara.txt',
        OPENDATA_BASE + 'd006001/001.txt',   # 台電開放資料平台備用
    ]
    for url in LOADPARA_CANDIDATES:
        try:
            text = fetch(url, retries=2, delay=5)
            result.update(parse_loadpara(text))
            print(f"loadpara OK ({url.split('/')[-1]}): load={result['load']} 萬瓩, spare={result['spareRate']}%")
            break
        except Exception as e:
            result['error'] = str(e)
            print(f'loadpara FAIL ({url.split("/")[-1]}): {e}')

    # --- fuel generation ---
    FUEL_CANDIDATES = [
        TAIPOWER_BASE + 'loadfueltype.csv',
        TAIPOWER_BASE + 'loadfueltype_1.csv',
    ]
    for url in FUEL_CANDIDATES:
        try:
            text = fetch(url, retries=1, delay=3)
            fuels = parse_loadfueltype(text)
            if fuels:
                result['fuels'] = fuels
                print(f"fuel OK ({url.split('/')[-1]}): {len(fuels)} 能源別")
                break
        except Exception as e:
            print(f'fuel FAIL ({url.split("/")[-1]}): {e}')
    else:
        print('所有 fuel 路徑均失敗')

    # --- regions (estimated) ---
    result['regions'] = estimate_regions(result.get('load', 0))

    # --- write output ---
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f'data.json saved at {now}')


if __name__ == '__main__':
    main()
