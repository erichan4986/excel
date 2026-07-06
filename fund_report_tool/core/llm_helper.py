import json
import os
import yaml
from openai import OpenAI, APIError, APITimeoutError

from core.paths import CONFIG_PATH


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_llm_client():
    config = load_config()
    llm_config = config.get('llm', {})
    if not llm_config.get('enabled'):
        return None
    # Prefer env var to avoid storing secrets in config files
    api_key = os.environ.get('DEEPSEEK_API_KEY', '') or llm_config.get('api_key', '')
    if api_key in ('', 'your-api-key', 'sk-你的key', 'sk-your-key'):
        return None
    base_url = llm_config.get('api_base', 'https://api.deepseek.com/v1')
    return OpenAI(api_key=api_key, base_url=base_url)


def generate(prompt, system=""):
    client = get_llm_client()
    if not client:
        return ""
    config = load_config()
    llm_config = config.get('llm', {})
    model = llm_config.get('model', 'deepseek-chat')
    timeout = llm_config.get('timeout', 30)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system or "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            timeout=timeout
        )
        return response.choices[0].message.content or ""
    except (APIError, APITimeoutError, Exception):
        return ""


def smart_match_single(header, candidates):
    prompt = (
        f'请判断以下表头最可能对应的标准指标名称，仅返回JSON格式：'
        f'{{"matched": "指标名"}} 或 {{"matched": null}}。\n'
        f'表头："{header}"\n'
        f'候选指标：{candidates}'
    )
    result = generate(prompt, "你是一个财务数据清洗助手，擅长识别中文财务表头的标准名称。")
    try:
        data = json.loads(result)
        matched = data.get("matched")
        if matched in candidates:
            return matched
    except (json.JSONDecodeError, Exception):
        pass
    return None


def smart_match_batch(headers, candidates):
    prompt = (
        f'请将以下表头映射到标准指标名称。返回JSON格式：'
        f'{{"表头1": "指标1", "表头2": null, ...}}，无法匹配的填null。\n'
        f'表头列表：{headers}\n'
        f'候选指标：{candidates}'
    )
    result = generate(prompt, "你是一个财务数据清洗助手，擅长识别中文财务表头的标准名称。")
    try:
        data = json.loads(result)
        valid = {k: v for k, v in data.items() if v in candidates}
        return valid
    except (json.JSONDecodeError, Exception):
        return {}


def explain_error(error_info):
    prompt = f"请用中文简要解释以下数据清洗错误的可能原因：\n{error_info}"
    return generate(prompt, "你是一个数据质量分析专家。")


def generate_mapping_suggestion(headers):
    prompt = (
        f"根据以下表头，生成YAML格式的映射配置建议：\n"
        f"表头：{headers}\n"
        f"返回格式：\n"
        f"- standard: \"标准名\"\n"
        f"  aliases:\n"
        f"    - \"表头名\"\n"
    )
    return generate(prompt, "你是一个财务数据映射专家。")
