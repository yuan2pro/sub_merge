#!/usr/bin/env python3
"""
mihomo_test.py - 使用mihomo API测试代理节点延迟并筛选可用节点

用法:
    python mihomo_test.py <input_yaml> <output_yaml> [options]

参数:
    input_yaml: 输入的YAML配置文件路径
    output_yaml: 输出的筛选后YAML配置文件路径

选项:
    --max-delay <ms>: 最大延迟阈值(毫秒), 默认3000
    --api-url <url>: mihomo API地址, 默认http://127.0.0.1:9090
    --timeout <sec>: 测试超时时间(秒), 默认10
    --test-url <url>: 测试URL, 默认https://www.gstatic.com/generate_204
"""

import argparse
import sys
import urllib.parse
from typing import Any, Dict, List

import requests
import yaml


def validate_proxy_config(proxy: Dict[str, Any]) -> bool:
    """
    验证单个代理配置是否有效
    """
    required_fields = ['name', 'type', 'server', 'port']
    if not all(field in proxy for field in required_fields):
        return False

    # 检查server是否有效
    server = proxy.get('server', '').strip()
    if not server or server == '':
        return False

    # 检查port是否有效
    try:
        port = int(proxy.get('port', 0))
        if port <= 0 or port > 65535:
            return False
    except (ValueError, TypeError):
        return False

    # 特殊检查REALITY协议
    if proxy.get('type') == 'vless':
        reality_opts = proxy.get('reality-opts', {})
        if reality_opts:
            # 检查REALITY必需字段
            required_reality = ['public-key', 'short-id']
            for field in required_reality:
                if field not in reality_opts or not reality_opts[field]:
                    print(f"  ⚠ REALITY配置缺失或为空: {field}")
                    return False

            # 验证public-key格式 (应该是以=结尾的base64字符串)
            public_key = reality_opts.get('public-key', '')
            if not public_key.endswith('='):
                print(f"  ⚠ REALITY public-key格式无效: {public_key[:20]}...")
                return False

            # 验证short-id格式
            short_id = reality_opts.get('short-id', '')
            if not short_id or len(short_id) < 4:
                print(f"  ⚠ REALITY short-id格式无效: {short_id}")
                return False

    return True


def test_proxy_delay(proxy_name: str, api_url: str, test_url: str, timeout: int, api_secret: str = None, max_retries: int = 2) -> tuple[bool, int]:
    """
    测试单个代理的延迟，支持重试机制

    返回: (是否成功, 延迟毫秒)
    """
    # 多个测试URL备选
    test_urls = [
        test_url,
        "https://www.google.com/generate_204",
        "https://connectivitycheck.gstatic.com/generate_204",
        "https://www.gstatic.com/generate_204",
        "https://httpbin.org/status/204",  # 更稳定的测试URL
        "https://httpstat.us/204"  # 另一个备选
    ]

    # 对代理名称进行URL编码
    encoded_name = urllib.parse.quote(proxy_name)
    api_endpoint = f"{api_url}/proxies/{encoded_name}/delay"

    print(f"    API endpoint: {api_endpoint}")

    # 准备认证头
    headers = {}
    if api_secret:
        headers['Authorization'] = f'Bearer {api_secret}'

    for attempt in range(max_retries):
        for i, url in enumerate(test_urls):
            try:
                print(f"    Attempt {attempt + 1}, URL {i + 1}: {url}")

                # 使用mihomo API进行延迟测试
                response = requests.get(
                    api_endpoint,
                    params={'timeout': timeout * 1000, 'url': url},
                    headers=headers,
                    timeout=timeout + 2
                )

                print(f"    Response status: {response.status_code}")

                if response.status_code in [200, 204]:
                    try:
                        data = response.json()
                        delay = data.get('delay', 0)
                        print(f"    Response data: {data}")
                        if delay > 0:  # 只要有延迟值就认为成功
                            return True, delay
                        else:
                            print(f"    Delay is 0, trying next URL")
                    except Exception as e:
                        print(f"    Failed to parse JSON response: {e}")
                        print(f"    Raw response: {response.text}")
                else:
                    print(f"    Bad status code: {response.status_code}")
                    print(f"    Response: {response.text[:200]}")

            except requests.exceptions.Timeout:
                print(f"    Timeout after {timeout + 2} seconds")
                continue
            except requests.exceptions.ConnectionError as e:
                print(f"    Connection error: {e}")
                continue
            except Exception as e:
                print(f"    Unexpected error: {e}")
                continue

    print(f"    All attempts failed for proxy {proxy_name}")
    return False, 0


def test_mihomo_api_connection(api_url: str, api_secret: str = None) -> bool:
    """
    测试mihomo API连接是否正常
    """
    try:
        print(f"测试mihomo API连接: {api_url}")

        # 准备认证头
        headers = {}
        if api_secret:
            headers['Authorization'] = f'Bearer {api_secret}'

        response = requests.get(f"{api_url}/version", headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"mihomo API连接成功，版本: {data.get('version', 'unknown')}")
            return True
        else:
            print(f"mihomo API响应异常: {response.status_code}")
            print(f"响应内容: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"mihomo API连接失败: {e}")
        return False


def filter_proxies(input_file: str, output_file: str, max_delay: int,
                  api_url: str, timeout: int, test_url: str, api_secret: str = None) -> tuple[int, int]:
    """
    筛选代理节点

    返回: (通过的节点数, 总节点数)
    """
    try:
        # 首先测试mihomo API连接
        if not test_mihomo_api_connection(api_url, api_secret):
            print("错误: mihomo API不可用，无法进行测速")
            sys.exit(1)

        # 读取输入配置文件
        with open(input_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # 如果没有指定API secret，尝试从配置文件中读取
        if not api_secret:
            api_secret = config.get('secret')

        proxies = config.get('proxies', [])
        if not proxies:
            print(f"警告: {input_file} 中没有找到代理配置")
            return 0, 0

        print(f"开始测试 {len(proxies)} 个代理节点...")
        if api_secret:
            print(f"使用API认证: {api_secret[:10]}...")

        # 验证并筛选代理
        valid_proxies = []
        passed_proxies = []

        for proxy in proxies:
            if not validate_proxy_config(proxy):
                print(f"  ✗ {proxy.get('name', 'Unknown')}: 配置无效")
                continue

            valid_proxies.append(proxy)

        print(f"有效代理: {len(valid_proxies)} 个")

        # 测试每个代理的延迟
        for proxy in valid_proxies:
            proxy_name = proxy.get('name', 'Unknown')
            print(f"测试 {proxy_name}...")

            success, delay = test_proxy_delay(proxy_name, api_url, test_url, timeout, api_secret)

            if success and delay > 0:
                if delay <= max_delay:
                    passed_proxies.append(proxy)
                    print(f"  ✓ {proxy_name}: {delay}ms")
                else:
                    print(f"  ⚠ {proxy_name}: {delay}ms (延迟较高，但可用)")
            else:
                print(f"  ✗ {proxy_name}: 连接失败")


        # 保存筛选后的配置
        filtered_config = {
            'proxies': passed_proxies,
            'proxy-groups': [{
                'name': 'Proxy',
                'type': 'select',
                'proxies': [p['name'] for p in passed_proxies]
            }],
            'rules': ['MATCH,Proxy']
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.safe_dump(filtered_config, f, allow_unicode=True, default_flow_style=False)

        print(f"\n筛选完成: {len(passed_proxies)}/{len(valid_proxies)} 个代理通过测试")
        print(f"结果已保存到: {output_file}")

        return len(passed_proxies), len(valid_proxies)

    except FileNotFoundError:
        print(f"错误: 找不到文件 {input_file}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"错误: YAML文件解析失败 {input_file}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"错误: 处理文件时发生异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="使用mihomo API测试代理节点延迟并筛选可用节点",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument('input_yaml', help='输入的YAML配置文件路径')
    parser.add_argument('output_yaml', help='输出的筛选后YAML配置文件路径')

    parser.add_argument('--max-delay', type=int, default=3000,
                       help='最大延迟阈值(毫秒), 默认3000')
    parser.add_argument('--api-url', default='http://127.0.0.1:9090',
                       help='mihomo API地址, 默认http://127.0.0.1:9090')
    parser.add_argument('--api-secret', default=None,
                       help='mihomo API认证密钥, 默认从配置文件读取')
    parser.add_argument('--timeout', type=int, default=10,
                       help='测试超时时间(秒), 默认10')
    parser.add_argument('--test-url', default='https://www.gstatic.com/generate_204',
                       help='测试URL, 默认https://www.gstatic.com/generate_204')

    args = parser.parse_args()

    # 执行筛选
    passed, total = filter_proxies(
        args.input_yaml,
        args.output_yaml,
        args.max_delay,
        args.api_url,
        args.timeout,
        args.test_url,
        args.api_secret
    )

    # 返回退出码：如果有节点通过测试则为0，否则为1
    sys.exit(0 if passed > 0 else 1)


if __name__ == '__main__':
    main()
