"""
TaiPower data fetcher — runs via GitHub Actions every 10 minutes.
Outputs: data.json in repo root (served by GitHub Pages).
"""
import re
import json
import requests
from datetime import datetime, timezone, timedelta

TAIPOWER_BASE = 'https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Referer': 'https://www.taipower.com.tw/tc/page.aspx?mid=206',
    'Accept-Language': 'zh-TW,zh;q=0.9',
}
TW_TZ = timezone(timedelta(hours=8))


def fetch(path):
    url = TAIPOWER_BASE + path
    res = requests.get(url, headers=HEADERS, timeout=30)
    res.raise_for_status()
    res.encoding = 'utf-8'
    text = res.text
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


def parse_genary(text):
    fuels = []
    for line in text.strip().splitlines():
        parts = line.strip().split(',')
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        if not name:
            continue
        mw = 0.0
        for p in parts[1:]:
            try:
                v = float(p.strip())
                if v != 0:
                    mw = v
                    break
            except ValueError:
                continue
        fuels.append({'name': name, 'mw': round(mw, 1)})
    return fuels


def estimate_regions(total_load):
    """台電無公開分區即時 API，以歷史比例估算"""
    ratios = {'北區': 0.42, '中區': 0.24, '南區': 0.29, '東區': 0.05}
    return {k: round(total_load * v, 1) for k, v in ratios.items()}


def main():
    now = datetime.now(TW_TZ).isoformat()
    result = {'fetchTime': now, 'error': None, 'fuels': [], 'regions': {}}

    # --- loadpara (required) ---
    try:
        text = fetch('loadpara.txt')
        result.update(parse_loadpara(text))
        print(f"loadpara OK: load={result['load']} 萬瓩, spare={result['spareRate']}%")
    except Exception as e:
        result['error'] = str(e)
        print(f'loadpara FAIL: {e}')

    # --- fuel generation (try multiple possible filenames) ---
    FUEL_CANDIDATES = [
        'genary.csv',
        'fueltype.csv',
        'fueltype_curr.csv',
        'genloadareachart.txt',
        'fueltype.txt',
    ]
    for candidate in FUEL_CANDIDATES:
        try:
            text = fetch(candidate)
            fuels = parse_genary(text)
            if fuels:
                result['fuels'] = fuels
                print(f"fuel OK ({candidate}): {len(fuels)} 能源別")
                break
        except Exception as e:
            print(f'fuel FAIL ({candidate}): {e}')
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
