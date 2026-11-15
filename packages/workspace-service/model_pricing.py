"""Official API list prices, per million tokens. Refresh the dated dictionary here.

These are current-price estimates, not an invoice ledger. Unknown providers and
model revisions are deliberately not fuzzy-matched to a similarly named model.
"""
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit

CHECKED_AT = '2026-10-28'
SOURCES = {
    'deepseek': 'https://api-docs.deepseek.com/quick_start/pricing/',
    'openai': 'https://developers.openai.com/api/docs/pricing',
    'anthropic': 'https://platform.claude.com/docs/en/about-claude/pricing',
    'google': 'https://ai.google.dev/gemini-api/docs/pricing',
    'zai': 'https://docs.z.ai/guides/overview/pricing',
    'minimax': 'https://platform.minimax.io/subscribe/token-plan?tab=api-enterprise',
    'bailian-cn': 'https://help.aliyun.com/zh/model-studio/model-pricing',
}
MODEL_PRICES = {}


def _add(provider, model, input, output, cached, *, aliases=(), currency='USD', **rules):
    MODEL_PRICES[provider + '/' + model] = {
        'provider': provider, 'model': model, 'input': input, 'output': output,
        'cached': cached, 'currency': currency, 'unit': 'per_million_tokens',
        'checked_at': CHECKED_AT, 'source': SOURCES[provider], 'aliases': list(aliases),
        **rules,
    }


_add('deepseek', 'deepseek-flash', .15, .6, .003,
     aliases=('deepseek-v4-flash', 'deepseek-v4-flash-vision-exp', 'deepseek-v4.1-flash', 'deepseek-v4.1-flash-1017'),
     peak={'input': .3, 'output': 1.2, 'cached': .006},
     note='空闲价；UTC 工作日 01–04、06–10 为高峰。法定节假日未判定时显示峰谷区间。')
_add('deepseek', 'deepseek-v4-pro', .66, 1.98, .022, aliases=('deepseek-v4-pro-0813',),
     peak={'input': 1.32, 'output': 3.96, 'cached': .044},
     note='空闲价；高峰时段显示峰谷区间，覆盖法定节假日例外。')

for model, input, output, cached in [
    ('gpt-6-astra', 10, 50, 1), ('gpt-6.1-sol', 2, 10, .1),
    ('gpt-6.1-terra', 1, 5, .05), ('gpt-6-luna', .1, .5, .01),
    ('gpt-5.6-sol', 4, 20, .4), ('gpt-5.6-cyber', 12.5, 75, 1.25),
]:
    _add('openai', model, input, output, cached, max_input=128000,
         cache_write=[input * 1.25, input * 2.5],
         note='Standard 短上下文参考价；当前仅自动估算 ≤128K 输入。缓存写入 TTL 未知时显示区间；不含 Fast、区域加价。')

for model, input, output, cached in [
    ('claude-fable-5-1', 10, 50, .25), ('claude-opus-5-5', 4, 20, .2),
    ('claude-sonnet-5-5', 2, 10, .1), ('claude-haiku-5-5', .1, .5, .01),
    ('claude-fable-5', 10, 50, 1), ('claude-opus-5', 5, 25, .5),
    ('claude-opus-4-8', 5, 25, .5), ('claude-opus-4-7', 5, 25, .5),
    ('claude-opus-4-6', 5, 25, .5), ('claude-opus-4-5', 5, 25, .5),
    ('claude-sonnet-5', 2, 10, .2), ('claude-sonnet-4-6', 3, 15, .3),
    ('claude-sonnet-4-5', 3, 15, .3), ('claude-haiku-4-5', 1, 5, .1),
]:
    dated = {'claude-opus-4-5': '20251101', 'claude-sonnet-4-5': '20250929', 'claude-haiku-4-5': '20251001'}
    aliases = [model + '-' + dated[model]] if model in dated else []
    rules = {'cache_write': [input * 1.25, input * 2]}
    if model == 'claude-haiku-5-5':
        rules['tiers'] = [{'above': 100000, 'input': .5, 'output': 2.5, 'cached': .05, 'cache_write': [.625, 1]}]
    _add('anthropic', model, input, output, cached, aliases=aliases,
         note='标准 API；缓存写入 5 分钟 / 1 小时，TTL 未知时显示费用区间。', **rules)

_add('google', 'gemini-4-argon', 2, 10, .1,
     schedule=[{'from': '2026-12-01', 'input': 4, 'output': 20, 'cached': .2}], note='促销至 2026-11-30；此后自动使用已公布标准价。')
_add('google', 'gemini-3.8-flash', .75, 3.75, .075,
     schedule=[{'from': '2027-01-01', 'input': 1.5, 'output': 7.5, 'cached': .15}], note='促销至 2026-12-31；不含缓存存储和搜索费用。')
_add('google', 'gemini-3.5-flash-lite', .3, 2.5, .03, note='Standard；不含缓存存储和搜索费用。')
_add('google', 'gemini-3.1-flash-lite', .25, 1.5, .025, note='文本 / 图片 / 视频输入；不含音频、缓存存储和搜索费用。')
_add('google', 'gemini-3.1-pro-preview', 2, 12, .2, aliases=('gemini-3.1-pro-preview-customtools',),
     tiers=[{'above': 200000, 'input': 4, 'output': 18, 'cached': .4}], note='按总输入长度分档；不含缓存存储和搜索费用。')

for model, input, output, cached in [
    ('glm-5.3', 1.4, 4.4, .26), ('glm-5.3-flash', .15, .5, .03),
    ('glm-5.3-flashx', .37, 1.25, .075), ('glm-5.3-flashx-legal', .37, 1.25, .075),
    ('glm-5.2', 1.4, 4.4, .26), ('glm-5.1', 1.4, 4.4, .26),
    ('glm-5', 1, 3.2, .2), ('glm-4.7', .6, 2.2, .11), ('glm-4.6', .6, 2.2, .11),
    ('glm-4.7-flashx', .07, .4, .01), ('glm-4.5', .6, 2.2, .11),
    ('glm-4.5-air', .2, 1.1, .03), ('glm-4.7-flash', 0, 0, 0), ('glm-4.5-flash', 0, 0, 0),
]:
    _add('zai', model, input, output, cached, note='Z.AI 国际 API；不适用于智谱中国站或 Coding Plan。')

_add('minimax', 'MiniMax-M3', .3, 1.2, .06,
     tiers=[{'above': 512000, 'input': .6, 'output': 2.4, 'cached': .12}], note='Standard；512K 以上使用第二档，非 Token Plan。')
_add('minimax', 'MiniMax-M2.7', .3, 1.2, .06, cache_write=.375)
_add('minimax', 'MiniMax-M2.7-highspeed', .6, 2.4, .06, cache_write=.375)
_add('bailian-cn', 'qwen3.8-max', 12, 36, None, currency='CNY', aliases=('qwen3.8-max-0902',),
     note='北京地域；缓存单价须查百炼控制台，存在缓存时需手动补充。')
_add('bailian-cn', 'qwen3.8-flash', .8, 2.7, None, currency='CNY',
     note='北京地域；缓存单价须查百炼控制台，存在缓存时需手动补充。')


def provider_for(profile):
    """Match a billing endpoint, not a display name or wire protocol."""
    url = urlsplit(str(profile.get('base_url') or ''))
    host = (url.hostname or '').lower()
    if url.scheme != 'https':
        return 'custom'
    if '/coding' in url.path or profile.get('api') in ('openai-codex', 'claude-code'):
        return 'custom'
    provider = {'api.deepseek.com': 'deepseek', 'api.openai.com': 'openai',
                'api.anthropic.com': 'anthropic', 'generativelanguage.googleapis.com': 'google',
                'api.z.ai': 'zai', 'api.minimax.io': 'minimax',
                'dashscope.aliyuncs.com': 'bailian-cn'}.get(host)
    if not provider and host.endswith('.cn-beijing.maas.aliyuncs.com'):
        provider = 'bailian-cn'
    return provider or 'custom'


def lookup(provider, model):
    model = str(model).strip().casefold()
    for price in MODEL_PRICES.values():
        if price['provider'] == provider and model in [price['model'].casefold(), *[a.casefold() for a in price['aliases']]]:
            return deepcopy(price)
    return None


def effective(price, prompt=0, at=None):
    price = deepcopy(price)
    date = (at or datetime.now(timezone.utc).isoformat())[:10]
    for item in price.get('schedule', []):
        if date >= item['from']:
            price.update(item)
    for tier in price.get('tiers', []):
        if prompt > tier['above']:
            price.update(tier)
    return price


def estimate(row, price):
    result = {'cost': None, 'cost_min': None, 'currency': (price or {}).get('currency', 'USD'), 'cost_reason': ''}
    if row['prompt_tokens'] is None or row['output_tokens'] is None:
        return {**result, 'cost_reason': '未返回完整用量'}
    if not price:
        return {**result, 'cost_reason': '未匹配价格'}
    prompt, output = row['prompt_tokens'], row['output_tokens']
    price = effective(price, prompt, row.get('created_at'))
    if prompt > price.get('max_input', float('inf')):
        return {**result, 'cost_reason': '超出已核实计价范围'}
    cached, written = row.get('cached_tokens'), row.get('cache_write_tokens') or 0
    if cached is None and prompt and price.get('cached') != price['input']:
        return {**result, 'cost_reason': '未返回缓存用量'}
    cached = cached or 0
    if min(prompt, output, cached, written) < 0 or cached + written > prompt:
        return {**result, 'cost_reason': '用量明细不一致'}
    if cached and price.get('cached') is None or written and price.get('cache_write') is None:
        return {**result, 'cost_reason': '缺少缓存单价'}
    variants = [price]
    if price.get('peak'):
        date = datetime.fromisoformat(row['created_at']).astimezone(timezone.utc)
        if date.weekday() < 5 and (1 <= date.hour < 4 or 6 <= date.hour < 10):
            variants.append({**price, **price['peak']})
    costs = []
    for variant in variants:
        writes = variant.get('cache_write') or 0
        for write in writes if isinstance(writes, list) else [writes]:
            costs.append(((prompt - cached - written) * variant['input'] + cached * (variant.get('cached') or 0)
                          + written * write + output * variant['output']) / 1e6)
    return {**result, 'cost': max(costs), 'cost_min': min(costs),
            'cost_reason': '峰谷 / 缓存写入区间' if min(costs) != max(costs) else '按当前单价估算'}
