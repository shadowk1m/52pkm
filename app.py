import re
from flask import Flask, Response
import requests
import yaml
import json
import os

app = Flask(__name__)
port = os.getenv('PORT', 8000)
subs = os.getenv('SUBS')
subUrlTemplate = os.getenv('SUB_URL_TEMPLATE')
ignoreLabelKeywords = os.getenv('IGNORE_LABEL_KEYWORDS', '').split(',')

if subs is None:
    raise ValueError("Environment variable 'SUBS' is not set")

if subUrlTemplate is None:
    raise ValueError("Environment variable 'SUB_URL_TEMPLATE' is not set, it should be like 'https://example.com/sub?token={token}'")

def generate_name(label, label_count):
    if label not in label_count:
        label_count[label] = 0
    label_count[label] += 1
    return f"{label} {label_count[label]:03}"

@app.route('/config.yml', methods=['GET'])
def get_config():
    with open('config.template.yml', 'r') as file:
        config = yaml.safe_load(file.read())
    label_count = {}
    subs_list = subs.split(',')
    headers = { 'User-Agent': 'clash', 'Accept': 'application/yaml' }
    proxies = []

    for sub in subs_list:
        sub = sub.strip()
        if not sub:
            continue
        subUrl = sub.startswith(('https://', 'http://')) and sub or subUrlTemplate.format(token=sub)
        try:
            response = requests.get(subUrl, headers=headers)
            response.raise_for_status()
            sub_config = yaml.safe_load(response.text)
            
            sub_proxies = sub_config.get('proxies', [])
            for idx, proxy in enumerate(sub_proxies):
                if isinstance(proxy, dict) and 'name' in proxy:
                    label = re.sub(r'\s*\d.+', '', proxy['name'])
                    if any(keyword in label for keyword in ignoreLabelKeywords):
                        continue
                    proxy['name'] = generate_name(label, label_count)
                    proxies.append(proxy)        
        except requests.RequestException as e:
            print(f"Error fetching config from {sub}: {e}")

    config['proxies'] = proxies
    for proxy_group in config['proxy-groups']:
        proxy_group['proxies'].extend(map(lambda p: p['name'], proxies))
    config_yaml = yaml.dump(config, allow_unicode=True)
    return Response(config_yaml, mimetype='text/yaml', status=200)

@app.route('/health', methods=['GET'])
def health():
    return Response('OK', status=200)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=port)
