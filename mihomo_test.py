#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import atexit
import json
import os
import subprocess
import tempfile
import time

import yaml
from tqdm import tqdm


def cleanup_mihomo_processes():
    """清理所有运行中的mihomo进程"""
    try:
        result = subprocess.run(['pgrep', '-f', 'mihomo'], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                subprocess.run(['kill', '-TERM', pid], timeout=5, capture_output=True)
                subprocess.run(['kill', '-KILL', pid], timeout=5, capture_output=True)
        time.sleep(1)
    except:
        pass

# 程序启动和退出时清理mihomo进程
cleanup_mihomo_processes()
atexit.register(cleanup_mihomo_processes)

def find_available_port(start_port=9090, max_attempts=100):
    """查找可用的端口"""
    import socket
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"无法找到可用端口 (尝试了 {start_port} 到 {start_port + max_attempts - 1})")

def start_mihomo_service(config_path, api_port):
    """启动mihomo服务"""
    # 测试配置
    result = subprocess.run(['mihomo', '-f', config_path, '-t'], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        error_msg = f"配置测试失败:\n"
        error_msg += f"退出代码: {result.returncode}\n"
        if result.stdout:
            error_msg += f"标准输出: {result.stdout}\n"
        if result.stderr:
            error_msg += f"标准错误: {result.stderr}\n"
        raise Exception(error_msg)

    # 启动服务 - 使用mihomo正确的API参数
    cmd = ['mihomo', '-f', config_path, '-ext-ctl', f"127.0.0.1:{api_port}"]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # 等待服务启动
    start_time = time.time()
    while time.time() - start_time < 30:
        try:
            result = subprocess.run(['curl', '-s', f"http://127.0.0.1:{api_port}/version"], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                return process
        except:
            time.sleep(0.5)

    # 如果启动超时，获取进程的错误输出
    process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=5)
        error_msg = "mihomo服务启动超时\n"
        if stdout:
            error_msg += f"标准输出: {stdout}\n"
        if stderr:
            error_msg += f"标准错误: {stderr}\n"
    except subprocess.TimeoutExpired:
        process.kill()
        error_msg = "mihomo服务启动超时且无法终止进程"

    raise Exception(error_msg)

def test_proxy_with_curl(proxy_name, api_port, timeout):
    """使用curl调用mihomo API测试代理"""
    cmd = [
        'curl', '-s', '--max-time', str(timeout + 2),
        f"http://127.0.0.1:{api_port}/proxies/{proxy_name}/delay",
        '-G',
        '--data-urlencode', f"timeout={timeout * 1000}",
        '--data-urlencode', "url=http://www.google.com/generate_204"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)

    if result.returncode == 0 and result.stdout:
        try:
            data = json.loads(result.stdout.strip())
            delay = data.get('delay', 0)
            return {
                'name': proxy_name,
                'status': 'pass',
                'message': 'Health check passed',
                'delay': delay
            }
        except json.JSONDecodeError:
            return {
                'name': proxy_name,
                'status': 'fail',
                'message': f'Invalid JSON response: {result.stdout}',
                'delay': 0
            }
    else:
        error_msg = result.stderr.strip() if result.stderr else f'curl failed with code {result.returncode}'
        return {
            'name': proxy_name,
            'status': 'fail',
            'message': f'API call failed: {error_msg}',
            'delay': 0
        }

def validate_proxy_config(proxy):
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

    return True

def test_proxies_with_mihomo_api(input_yaml, output_yaml, timeout=10, max_delay=500):
    """
    读取yaml文件，使用mihomo命令行测试代理
    """
    print(f"读取配置文件: {input_yaml}")

    # 读取代理配置
    with open(input_yaml, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
        all_proxies = data.get('proxies', [])

    print(f"读取到 {len(all_proxies)} 个代理")

    # 过滤有效的代理配置
    valid_proxies = []
    invalid_proxies = []

    for proxy in all_proxies:
        if validate_proxy_config(proxy):
            valid_proxies.append(proxy)
        else:
            invalid_proxies.append({
                'name': proxy.get('name', 'Unknown'),
                'reason': 'Invalid proxy configuration'
            })

    print(f"有效代理: {len(valid_proxies)}, 无效代理: {len(invalid_proxies)}")

    if not valid_proxies:
        print("没有有效的代理需要测试")
        # 创建空文件
        os.makedirs(os.path.dirname(output_yaml), exist_ok=True)
        with open(output_yaml, 'w', encoding='utf-8') as f:
            yaml.safe_dump({'proxies': []}, f, allow_unicode=True, default_flow_style=False)
        return 0, len(all_proxies)

    # 测试所有有效代理
    results = []
    print(f"开始测试 {len(valid_proxies)} 个代理...")

    for proxy in tqdm(valid_proxies, desc="测试进度", unit="个"):
        result = test_single_proxy(proxy, timeout)
        results.append(result)

    # 过滤和保存结果
    passed_proxies = []
    failed_proxies = []

    for result in results:
        if result['config_valid'] and result['health_status'] == 'pass' and result['delay'] <= max_delay:
            # 找到原始代理配置
            original_proxy = next((p for p in valid_proxies if p['name'] == result['name']), None)
            if original_proxy:
                passed_proxies.append(original_proxy)
        else:
            failed_proxies.append({
                'name': result['name'],
                'reason': result['message'],
                'delay': result['delay']
            })

    # 添加无效代理到失败列表
    failed_proxies.extend(invalid_proxies)

    # 保存过滤后的代理
    os.makedirs(os.path.dirname(output_yaml), exist_ok=True)
    with open(output_yaml, 'w', encoding='utf-8') as f:
        yaml.safe_dump({'proxies': passed_proxies}, f, allow_unicode=True, default_flow_style=False)

    # 输出统计信息
    print(f"过滤结果: 原始 {len(all_proxies)} 个 → 保留 {len(passed_proxies)} 个")

    # 输出失败的代理
    if failed_proxies:
        print("\n失败的代理节点:")
        for proxy in failed_proxies:
            if proxy['delay'] > 0:
                print(f"  - {proxy['name']}: 延迟 {proxy['delay']} ms - {proxy['reason']}")
            else:
                print(f"  - {proxy['name']}: {proxy['reason']}")

    # 输出通过的代理
    if passed_proxies:
        print("\n通过的代理节点:")
        passed_results = [r for r in results if r['config_valid'] and r['health_status'] == 'pass' and r['delay'] <= max_delay]
        passed_results.sort(key=lambda x: x['delay'])
        for result in passed_results:
            delay_info = f"{result['delay']} ms" if result['delay'] > 0 else "N/A"
            print(f"  - {result['name']}: 延迟 {delay_info}")

    return len(passed_proxies), len(all_proxies) - len(passed_proxies)

class MihomoManager:
    """
    Mihomo进程管理器
    """
    def __init__(self, config_path, api_port=None):
        self.config_path = config_path
        self.api_port = api_port or self._find_available_port()
        self.process = None
        self.api_url = f"http://127.0.0.1:{self.api_port}"

    def _find_available_port(self, start_port=9090, max_attempts=100):
        """查找可用的端口"""
        import socket
        for port in range(start_port, start_port + max_attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('127.0.0.1', port))
                    return port
            except OSError:
                continue
        raise RuntimeError(f"无法找到可用端口 (尝试了 {start_port} 到 {start_port + max_attempts - 1})")

    def start(self, timeout=30):
        """启动mihomo服务"""
        try:
            # 先测试配置
            cmd_test = ['mihomo', '-f', self.config_path, '-t']
            result = subprocess.run(cmd_test, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise Exception(f"配置测试失败: {result.stderr}")

            # 启动mihomo服务
            cmd_start = ['mihomo', '-f', self.config_path, '-ext-ctl', f"127.0.0.1:{self.api_port}"]
            self.process = subprocess.Popen(cmd_start, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 等待服务启动
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    response = requests.get(f"{self.api_url}/version", timeout=2)
                    if response.status_code == 200:
                        return True
                except:
                    time.sleep(0.5)
                    continue

            raise Exception("mihomo服务启动超时")

        except Exception as e:
            self.stop()
            raise e

    def stop(self):
        """停止mihomo服务"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

    def health_check(self, proxy_name, timeout=10, url="http://www.google.com/generate_204"):
        """使用API进行健康检查"""
        try:
            # 使用mihomo API进行延迟测试
            response = requests.get(
                f"{self.api_url}/proxies/{proxy_name}/delay",
                params={
                    'timeout': timeout * 1000,  # 转换为毫秒
                    'url': url
                },
                timeout=timeout + 2
            )

            if response.status_code == 200:
                data = response.json()
                delay = data.get('delay', 0)
                return {
                    'status': 'pass',
                    'message': 'Health check passed',
                    'delay': delay
                }
            else:
                return {
                    'status': 'fail',
                    'message': f'API返回错误: {response.status_code}',
                    'delay': 0
                }

        except requests.exceptions.Timeout:
            return {
                'status': 'fail',
                'message': f'Health check timeout after {timeout} seconds',
                'delay': 0
            }
        except Exception as e:
            return {
                'status': 'fail',
                'message': f'Health check error: {str(e)}',
                'delay': 0
            }

def create_mihomo_config(proxy):
    """
    为单个代理创建mihomo配置文件
    """
    config = {
        "proxies": [proxy],
        "proxy-groups": [
            {
                "name": "Proxy",
                "type": "select",
                "proxies": [proxy["name"]]
            }
        ],
        "rules": [
            "MATCH,Proxy"
        ]
    }
    return config


def test_single_proxy(proxy, timeout=15):
    """
    测试单个代理的可用性
    """
    # 创建临时配置文件
    config = create_mihomo_config(proxy)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as config_file:
        yaml.dump(config, config_file, allow_unicode=True)
        config_path = config_file.name

    try:
        # 启动mihomo测试配置是否有效
        cmd = ['mihomo', '-f', config_path, '-t']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

        # 解析结果
        if result.returncode == 0:
            # 配置有效，进行mihomo内置健康检查
            health_result = mihomo_health_check(proxy, config_path, timeout)
            return {
                'name': proxy['name'],
                'config_valid': True,
                'health_status': health_result['status'],
                'message': health_result['message'],
                'delay': health_result['delay']
            }
        else:
            # 测试失败
            error_msg = result.stderr.strip() if result.stderr else 'Unknown error'
            return {
                'name': proxy['name'],
                'config_valid': False,
                'health_status': 'fail',
                'message': error_msg,
                'delay': 0
            }

    except subprocess.TimeoutExpired:
        return {
            'name': proxy['name'],
            'config_valid': False,
            'health_status': 'fail',
            'message': f'Test timeout after {timeout} seconds',
            'delay': 0
        }
    except FileNotFoundError:
        return {
            'name': proxy['name'],
            'config_valid': False,
            'health_status': 'fail',
            'message': 'Mihomo executable not found. Please install mihomo first.',
            'delay': 0
        }
    except Exception as e:
        return {
            'name': proxy['name'],
            'config_valid': False,
            'health_status': 'fail',
            'message': str(e),
            'delay': 0
        }
    finally:
        # 清理临时文件
        if os.path.exists(config_path):
            os.unlink(config_path)


def test_proxy_with_mihomo(proxy, timeout=15):
    """
    使用mihomo测试单个代理的可用性
    """
    result = test_single_proxy(proxy, timeout)
    
    # 转换为之前使用的格式以保持兼容性
    return {
        'name': result['name'],
        'status': result['health_status'] if result['config_valid'] else 'fail',
        'message': result['message'],
        'delay': result['delay']
    }

def test_single_proxy_api(args):
    """
    使用API测试单个代理（用于多进程，每个进程独立启动服务）
    """
    proxy_name, config_path, timeout = args

    manager = None
    try:
        # 每个进程独立启动mihomo服务
        manager = MihomoManager(config_path)
        manager.start(timeout=30)

        health_result = manager.health_check(proxy_name, timeout=timeout)
        return {
            'name': proxy_name,
            'status': health_result['status'],
            'message': health_result['message'],
            'delay': health_result['delay']
        }
    except Exception as e:
        return {
            'name': proxy_name,
            'status': 'fail',
            'message': f'测试错误: {str(e)}',
            'delay': 0
        }
    finally:
        if manager:
            manager.stop()

def test_proxies_batch(proxies, timeout=10, max_workers=None):
    """
    批量测试代理（每个代理独立启动mihomo服务进行API测试）
    """
    results = []
    print(f"开始测试 {len(proxies)} 个代理...")

    if not proxies:
        return results

    # 确定进程数
    if max_workers is None:
        max_workers = min(len(proxies), max(1, cpu_count() - 1))
    else:
        max_workers = min(max_workers, len(proxies))

    if max_workers > 1:
        print(f"使用多进程独立API测试 (进程数: {max_workers})")
        # 多进程测试 - 每个进程独立测试一个代理
        proxy_args = [(proxy, timeout) for proxy in proxies]

        with Pool(processes=max_workers, maxtasksperchild=1) as pool:
            # 使用imap_unordered获取结果
            async_results = pool.imap_unordered(test_single_proxy_independent_api, proxy_args)

            # 使用tqdm显示进度
            for result in tqdm(async_results, total=len(proxy_args), desc="测试进度", unit="个"):
                results.append(result)
    else:
        print("使用单进程独立API测试")
        # 单进程测试 - 顺序测试每个代理
        for proxy in tqdm(proxies, desc="测试进度", unit="个"):
            result = test_single_proxy_independent_api((proxy, timeout))
            results.append(result)

    return results

def test_single_proxy_commandline(args):
    """
    多进程中使用curl命令测试单个代理（避免守护进程创建子进程问题）
    """
    proxy, timeout = args

    try:
        # 使用curl命令直接测试代理连通性
        start_time = time.time()

        # 构建curl命令，根据代理类型使用不同的参数
        proxy_type = proxy.get('type', '').lower()

        if proxy_type == 'http' or proxy_type == 'https':
            # HTTP/HTTPS代理
            proxy_url = f"http://{proxy['server']}:{proxy['port']}"
            cmd = [
                'curl', '-s', '--max-time', str(timeout),
                '--proxy', proxy_url,
                'http://www.google.com/generate_204'
            ]
        elif proxy_type in ['socks5', 'socks4']:
            # SOCKS代理
            proxy_url = f"socks5://{proxy['server']}:{proxy['port']}"
            cmd = [
                'curl', '-s', '--max-time', str(timeout),
                '--socks5', f"{proxy['server']}:{proxy['port']}",
                'http://www.google.com/generate_204'
            ]
        elif proxy_type == 'ss':
            # Shadowsocks，需要通过mihomo测试
            return test_single_proxy_via_mihomo(proxy, timeout)
        elif proxy_type in ['vmess', 'vless', 'trojan']:
            # V2Ray协议，需要通过mihomo测试
            return test_single_proxy_via_mihomo(proxy, timeout)
        else:
            # 未知类型，使用mihomo测试
            return test_single_proxy_via_mihomo(proxy, timeout)

        # 执行curl命令
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)

        elapsed_time = int((time.time() - start_time) * 1000)  # 毫秒

        if result.returncode == 0:
            return {
                'name': proxy['name'],
                'status': 'pass',
                'message': 'Health check passed',
                'delay': elapsed_time
            }
        else:
            error_msg = result.stderr.strip() if result.stderr else 'Connection failed'
            return {
                'name': proxy['name'],
                'status': 'fail',
                'message': f'curl error: {error_msg}',
                'delay': 0
            }

    except subprocess.TimeoutExpired:
        return {
            'name': proxy['name'],
            'status': 'fail',
            'message': f'Health check timeout after {timeout} seconds',
            'delay': 0
        }
    except Exception as e:
        return {
            'name': proxy['name'],
            'status': 'fail',
            'message': f'Health check error: {str(e)}',
            'delay': 0
        }

def test_single_proxy_curl(args):
    """
    多进程中使用curl命令测试单个代理
    """
    proxy, timeout = args

    try:
        # 使用curl命令直接测试代理连通性
        start_time = time.time()

        # 构建curl命令，根据代理类型使用不同的参数
        proxy_type = proxy.get('type', '').lower()

        if proxy_type == 'http' or proxy_type == 'https':
            # HTTP/HTTPS代理
            cmd = [
                'curl', '-s', '--max-time', str(timeout),
                '--proxy', f"http://{proxy['server']}:{proxy['port']}",
                'http://www.google.com/generate_204'
            ]
        elif proxy_type in ['socks5', 'socks4']:
            # SOCKS代理
            cmd = [
                'curl', '-s', '--max-time', str(timeout),
                '--socks5', f"{proxy['server']}:{proxy['port']}",
                'http://www.google.com/generate_204'
            ]
        else:
            # 其他协议（VMess, VLESS, Trojan, SS等）使用mihomo命令行测试
            return test_single_proxy_via_mihomo(proxy, timeout)

        # 执行curl命令
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)

        elapsed_time = int((time.time() - start_time) * 1000)  # 毫秒

        if result.returncode == 0:
            return {
                'name': proxy['name'],
                'status': 'pass',
                'message': 'Health check passed',
                'delay': elapsed_time
            }
        else:
            error_msg = result.stderr.strip() if result.stderr else 'Connection failed'
            return {
                'name': proxy['name'],
                'status': 'fail',
                'message': f'curl error: {error_msg}',
                'delay': 0
            }

    except subprocess.TimeoutExpired:
        return {
            'name': proxy['name'],
            'status': 'fail',
            'message': f'Health check timeout after {timeout} seconds',
            'delay': 0
        }
    except Exception as e:
        return {
            'name': proxy['name'],
            'status': 'fail',
            'message': f'Health check error: {str(e)}',
            'delay': 0
        }

def test_single_proxy_api_mode(args):
    """
    单进程中使用mihomo API测试单个代理
    """
    proxy, timeout = args

    # 创建单代理配置文件
    config = create_mihomo_config(proxy)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as config_file:
        yaml.dump(config, config_file, allow_unicode=True)
        config_path = config_file.name

    manager = None
    try:
        # 启动mihomo服务
        manager = MihomoManager(config_path)
        manager.start(timeout=30)

        # 使用API进行健康检查
        health_result = manager.health_check(proxy['name'], timeout=timeout)
        return {
            'name': proxy['name'],
            'status': health_result['status'],
            'message': health_result['message'],
            'delay': health_result['delay']
        }
    except Exception as e:
        # 如果API测试失败，回退到命令行测试
        try:
            result = test_single_proxy(proxy, timeout)
            return {
                'name': result['name'],
                'status': result['health_status'] if result['config_valid'] else 'fail',
                'message': result['message'],
                'delay': result['delay']
            }
        except Exception as fallback_error:
            return {
                'name': proxy['name'],
                'status': 'fail',
                'message': f'API测试失败: {str(e)}, 回退测试也失败: {str(fallback_error)}',
                'delay': 0
            }
    finally:
        if manager:
            manager.stop()
        # 清理临时文件
        if os.path.exists(config_path):
            os.unlink(config_path)

def test_single_proxy_curl_api(args):
    """
    使用curl调用mihomo API进行healthcheck
    """
    proxy_name, api_port, timeout = args

    try:
        # 使用curl调用mihomo API进行延迟测试
        cmd = [
            'curl', '-s', '--max-time', str(timeout + 2),
            f"http://127.0.0.1:{api_port}/proxies/{proxy_name}/delay",
            '-G',  # GET请求
            '--data-urlencode', f"timeout={timeout * 1000}",
            '--data-urlencode', "url=http://www.google.com/generate_204"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)

        if result.returncode == 0 and result.stdout:
            try:
                # 解析JSON响应
                data = json.loads(result.stdout.strip())
                delay = data.get('delay', 0)
                return {
                    'name': proxy_name,
                    'status': 'pass',
                    'message': 'Health check passed',
                    'delay': delay
                }
            except json.JSONDecodeError:
                return {
                    'name': proxy_name,
                    'status': 'fail',
                    'message': f'Invalid JSON response: {result.stdout}',
                    'delay': 0
                }
        else:
            error_msg = result.stderr.strip() if result.stderr else f'curl failed with code {result.returncode}'
            return {
                'name': proxy_name,
                'status': 'fail',
                'message': f'API call failed: {error_msg}',
                'delay': 0
            }

    except subprocess.TimeoutExpired:
        return {
            'name': proxy_name,
            'status': 'fail',
            'message': f'Health check timeout after {timeout} seconds',
            'delay': 0
        }
    except Exception as e:
        return {
            'name': proxy_name,
            'status': 'fail',
            'message': f'Health check error: {str(e)}',
            'delay': 0
        }

def test_single_proxy_independent_api(args):
    """
    每个代理独立启动mihomo服务进行API测试
    """
    proxy, timeout = args

    # 创建单代理配置文件
    config = create_mihomo_config(proxy)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as config_file:
        yaml.dump(config, config_file, allow_unicode=True)
        config_path = config_file.name

    manager = None
    try:
        # 为这个代理独立启动mihomo服务
        manager = MihomoManager(config_path)
        manager.start(timeout=30)

        # 使用API进行健康检查
        health_result = manager.health_check(proxy['name'], timeout=timeout)
        return {
            'name': proxy['name'],
            'status': health_result['status'],
            'message': health_result['message'],
            'delay': health_result['delay']
        }
    except Exception as e:
        # 如果API测试失败，回退到命令行测试
        try:
            result = test_single_proxy(proxy, timeout)
            return {
                'name': result['name'],
                'status': result['health_status'] if result['config_valid'] else 'fail',
                'message': result['message'],
                'delay': result['delay']
            }
        except Exception as fallback_error:
            return {
                'name': proxy['name'],
                'status': 'fail',
                'message': f'API测试失败: {str(e)}, 回退测试也失败: {str(fallback_error)}',
                'delay': 0
            }
    finally:
        if manager:
            manager.stop()
        # 清理临时文件
        if os.path.exists(config_path):
            os.unlink(config_path)

def test_single_proxy_via_mihomo(proxy, timeout):
    """
    通过mihomo命令行测试代理（用于不支持curl的代理类型）
    """
    # 创建单代理配置文件
    config = create_mihomo_config(proxy)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as config_file:
        yaml.dump(config, config_file, allow_unicode=True)
        config_path = config_file.name

    try:
        # 使用命令行方式测试代理
        result = test_single_proxy(proxy, timeout)
        return {
            'name': result['name'],
            'status': result['health_status'] if result['config_valid'] else 'fail',
            'message': result['message'],
            'delay': result['delay']
        }
    except Exception as e:
        return {
            'name': proxy['name'],
            'status': 'fail',
            'message': f'命令行测试失败: {str(e)}',
            'delay': 0
        }
    finally:
        # 清理临时文件
        if os.path.exists(config_path):
            os.unlink(config_path)


def mihomo_health_check(proxy, config_path, timeout=10):
    """
    使用mihomo内置的healthcheck功能
    """
    try:
        # 使用mihomo的delay测试功能（不带-url参数）
        cmd = ['mihomo', '-f', config_path, '-d', proxy['name']]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        
        # 解析结果
        if result.returncode == 0 and result.stdout:
            # 解析输出中的延迟信息
            output = result.stdout.strip()
            # 查找延迟信息
            if 'ms' in output:
                # 提取延迟数值
                import re
                delay_matches = re.findall(r'(\d+)\s*ms', output)
                if delay_matches:
                    delay = int(delay_matches[0])  # 取第一个匹配的数值
                    return {
                        'status': 'pass',
                        'message': 'Health check passed',
                        'delay': delay
                    }
            
            # 如果没有找到明确的延迟信息，但命令成功执行
            return {
                'status': 'pass',
                'message': 'Health check passed (no delay info)',
                'delay': 0
            }
        else:
            error_msg = result.stderr.strip() if result.stderr else 'Health check failed'
            return {
                'status': 'fail',
                'message': error_msg,
                'delay': 0
            }
            
    except subprocess.TimeoutExpired:
        return {
            'status': 'fail',
            'message': f'Health check timeout after {timeout} seconds',
            'delay': 0
        }
    except Exception as e:
        return {
            'status': 'fail',
            'message': f'Health check error: {str(e)}',
            'delay': 0
        }


def filter_and_save_proxies(input_yaml, output_yaml, timeout=15, max_delay=5000):
    """
    过滤代理并保存结果
    """
    # 读取原始代理
    with open(input_yaml, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
        proxies = data.get('proxies', [])

    print(f"读取到 {len(proxies)} 个代理")

    # 测试代理
    test_results = test_proxies_batch(proxies, timeout=timeout)

    # 过滤通过的代理
    passed_proxies = []
    failed_proxies = []
    for result in test_results:
        if result['status'] == 'pass' and result['delay'] <= max_delay:
            # 找到原始代理配置
            original_proxy = next((p for p in proxies if p['name'] == result['name']), None)
            if original_proxy:
                passed_proxies.append(original_proxy)
        else:
            # 记录失败的代理及原因
            failed_proxies.append({
                'name': result['name'],
                'reason': result['message'],
                'delay': result['delay']
            })

    # 保存过滤后的代理，即使为空也要创建文件
    os.makedirs(os.path.dirname(output_yaml), exist_ok=True)
    with open(output_yaml, 'w', encoding='utf-8') as f:
        yaml.safe_dump({'proxies': passed_proxies}, f, allow_unicode=True, default_flow_style=False)

    # 输出统计信息
    print(f"过滤结果: 原始 {len(proxies)} 个 → 保留 {len(passed_proxies)} 个")
    
    # 输出失败的代理及原因
    if failed_proxies:
        print("\n失败的代理节点:")
        for proxy in failed_proxies:
            if proxy['delay'] > 0:
                print(f"  - {proxy['name']}: 延迟 {proxy['delay']} ms - {proxy['reason']}")
            else:
                print(f"  - {proxy['name']}: {proxy['reason']}")
    
    # 输出通过的代理及延迟
    if passed_proxies:
        print("\n通过的代理节点:")
        passed_results = [r for r in test_results if r['status'] == 'pass' and r['delay'] <= max_delay]
        # 按延迟排序
        passed_results.sort(key=lambda x: x['delay'])
        for result in passed_results:
            delay_info = f"{result['delay']} ms" if result['delay'] > 0 else "N/A"
            print(f"  - {result['name']}: 延迟 {delay_info}")
    else:
        print("\n没有通过测试的代理节点")
    
    return len(passed_proxies), len(proxies) - len(passed_proxies)


def process_single_file(args):
    """
    处理单个文件，用于多进程执行
    """
    file_info, timeout = args
    input_path = file_info['input_path']
    output_path = file_info['output_path']
    filename = file_info['filename']

    try:
        print(f"开始处理文件: {filename}")
        passed, filtered = filter_and_save_proxies(
            input_path, output_path,
            timeout=timeout
        )
        print(f"完成处理文件: {filename} (保留 {passed}, 过滤 {filtered})")
        return {
            'filename': filename,
            'success': True,
            'passed': passed,
            'filtered': filtered
        }
    except Exception as e:
        print(f"错误处理文件 {filename}: {str(e)}")
        return {
            'filename': filename,
            'success': False,
            'passed': 0,
            'filtered': 0,
            'error': str(e)
        }


def main():
    """
    主函数：读取yaml文件，启动mihomo，然后通过curl调用mihomo的healthcheck进行测速
    """
    proxy_dir = 'sub'
    timeout = 3  # 测试超时时间

    # 查找所有 merged_proxies_*.yaml 文件
    file_list = []
    for filename in os.listdir(proxy_dir):
        if filename.startswith('merged_proxies_') and filename.endswith('.yaml'):
            input_path = os.path.join(proxy_dir, filename)
            output_path = input_path.replace('.yaml', '_mihomo_test.yaml')
            file_list.append({
                'filename': filename,
                'input_path': input_path,
                'output_path': output_path
            })

    print(f"发现 {len(file_list)} 个YAML文件")

    if not file_list:
        print("没有找到需要处理的YAML文件")
        return

    print("开始顺序处理文件...")

    processed_results = []

    # 顺序处理文件
    for file_info in tqdm(file_list, desc="文件处理进度", unit="个"):
        try:
            print(f"\n开始处理文件: {file_info['filename']}")
            passed, filtered = test_proxies_with_mihomo_api(
                file_info['input_path'],
                file_info['output_path'],
                timeout=timeout
            )
            processed_results.append({
                'filename': file_info['filename'],
                'success': True,
                'passed': passed,
                'filtered': filtered
            })
            print(f"完成处理文件: {file_info['filename']} (保留 {passed}, 过滤 {filtered})")
        except Exception as e:
            print(f"错误处理文件 {file_info['filename']}: {str(e)}")
            processed_results.append({
                'filename': file_info['filename'],
                'success': False,
                'passed': 0,
                'filtered': 0,
                'error': str(e)
            })

    # 统计结果
    processed_count = len([r for r in processed_results if r['success']])
    total_passed = sum(r['passed'] for r in processed_results if r['success'])
    total_filtered = sum(r['filtered'] for r in processed_results if r['success'])
    error_count = len([r for r in processed_results if not r['success']])

    print(f"\n顺序处理完成: {processed_count} 个文件成功, {error_count} 个失败")
    print(f"总计: 保留 {total_passed} 个代理, 过滤 {total_filtered} 个代理")

    if error_count > 0:
        print("\n失败的文件:")
        for result in processed_results:
            if not result['success']:
                print(f"  - {result['filename']}: {result.get('error', '未知错误')}")

    # 删除原始文件
    for file_info in file_list:
        if os.path.exists(file_info['input_path']):
            os.remove(file_info['input_path'])
            print(f"已删除: {file_info['input_path']}")

if __name__ == '__main__':
    main()
