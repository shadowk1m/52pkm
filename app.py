import re
import os
from flask import Flask, Response
import requests
import yaml
import concurrent.futures
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

app = Flask(__name__)
port = int(os.getenv('PORT', 8000))
subs = os.getenv('SUBS')
subUrlTemplate = os.getenv('SUB_URL_TEMPLATE')
ignoreLabelKeywords = [k.strip() for k in os.getenv('IGNORE_LABEL_KEYWORDS', '').split(',') if k.strip()]
ignoreProxyNames = {n.strip() for n in os.getenv('IGNORE_PROXY_NAMES', '').split(',') if n.strip()}

if subs is None:
    raise ValueError("Environment variable 'SUBS' is not set")

if subUrlTemplate is None:
    raise ValueError("Environment variable 'SUB_URL_TEMPLATE' is not set, it should be like 'https://example.com/sub?token={token}'")

# Shared requests Session for connection pooling and retries
session = requests.Session()
# Configure retries for transient network issues
retries = Retry(total=2, backoff_factor=0.3, status_forcelist=(500, 502, 503, 504))
adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=retries)
session.mount('http://', adapter)
session.mount('https://', adapter)

def generate_name(label, label_count):
    if label not in label_count:
        label_count[label] = 0
    label_count[label] += 1
    return f"{label} {label_count[label]:03}"

def _build_sub_url(sub):
    sub = sub.strip()
    if not sub:
        return None
    return sub if sub.startswith(('https://', 'http://')) else subUrlTemplate.format(token=sub)

def _fetch_and_parse(sub_url, headers, timeout=10):
    """
    Fetch a subscription URL and return the parsed config dict or None on error.
    Only does network I/O and YAML parsing (no name/label mutation).
    """
    try:
        resp = session.get(sub_url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return yaml.safe_load(resp.text)
    except Exception as e:
        app.logger.warning(f"Error fetching config from {sub_url}: {e}")
        return None

@app.route('/config.yml', methods=['GET'])
def get_config():
    # load template
    with open('config.template.yml', 'r') as file:
        config = yaml.safe_load(file.read())

    label_count = {}
    subs_list = [s.strip() for s in subs.split(',') if s.strip()]
    headers = {'User-Agent': 'clash', 'Accept': 'application/yaml'}
    proxies = []

    # Prepare list of subscription URLs
    sub_urls = []
    for sub in subs_list:
        u = _build_sub_url(sub)
        if u:
            sub_urls.append(u)

    # Fetch in parallel (network + parse)
    max_workers = min(32, max(1, len(sub_urls)))
    fetched_configs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(_fetch_and_parse, url, headers): url for url in sub_urls}
        for fut in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[fut]
            result = fut.result()
            if result is None:
                continue
            fetched_configs.append((url, result))

    # Process fetched configs sequentially to safely mutate label_count and preserve deterministic ordering
    for url, sub_config in fetched_configs:
        sub_proxies = sub_config.get('proxies', []) if isinstance(sub_config, dict) else []
        # If any proxy name matches ignoreProxyNames set, skip the entire subscription
        if any(isinstance(p, dict) and p.get('name') in ignoreProxyNames for p in sub_proxies):
            matched = [p.get('name') for p in sub_proxies if isinstance(p, dict) and p.get('name') in ignoreProxyNames]
            app.logger.warning(f"Ignored subscription {url} because it contains ignored proxy names: {matched}")
            continue

        for proxy in sub_proxies:
            if isinstance(proxy, dict) and 'name' in proxy:
                # Remove trailing numeric suffixes from label (preserve non-numeric parts)
                label = re.sub(r'\s*\d.*$', '', proxy['name']).strip()
                # Ignore by label keywords
                if any(keyword and (keyword in label) for keyword in ignoreLabelKeywords):
                    continue
                proxy['name'] = generate_name(label if label else proxy['name'], label_count)
                proxies.append(proxy)

    # Attach proxies to template config
    config['proxies'] = proxies
    # Extend each proxy-group's proxies list with the new names
    proxy_names = [p['name'] for p in proxies]
    for proxy_group in config.get('proxy-groups', []):
        if 'proxies' not in proxy_group or not isinstance(proxy_group['proxies'], list):
            proxy_group['proxies'] = []
        proxy_group['proxies'].extend(proxy_names)

    config_yaml = yaml.dump(config, allow_unicode=True)
    return Response(config_yaml, mimetype='text/yaml', status=200)

@app.route('/health', methods=['GET'])
def health():
    return Response('OK', status=200)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=port)