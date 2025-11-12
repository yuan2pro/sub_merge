#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import atexit
import json
import os
import subprocess
import tempfile
import time
from multiprocessing import Pool, cpu_count

import requests
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

def test_single_proxy_multiprocess(args):
    """
    多进程中测试单个代理，确保临时文件被正确清理
    """
    proxy, timeout = args

    # 创建临时配置文件
    config = create_mihomo_config(proxy)
    config_path = None

    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as config_file:
            yaml.dump(config, config_file, allow_unicode=True)
            config_path = config_file.name

        # 测试代理
        result = test_single_proxy(proxy, timeout)
        return result

    finally:
        # 确保临时文件被删除
        if config_path and os.path.exists(config_path):
            try:
                os.unlink(config_path)
            except:
                pass

def test_proxies_with_mihomo_api(input_yaml, output_yaml, timeout=10, max_delay=500):
    """
    读取yaml文件，使用多进程并行测试代理，确保临时文件被正确清理
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

    # 使用多进程并行测试代理
    print(f"开始多进程测试 {len(valid_proxies)} 个代理...")

    # 确定进程数
    max_workers = min(len(valid_proxies), max(1, cpu_count() - 1))
    print(f"使用 {max_workers} 个进程进行并行测试")

    proxy_args = [(proxy, timeout) for proxy in valid_proxies]

    results = []
    with Pool(processes=max_workers, maxtasksperchild=1) as pool:
        # 使用imap_unordered获取结果
        async_results = pool.imap_unordered(test_single_proxy_multiprocess, proxy_args)

        # 使用tqdm显示进度
        for result in tqdm(async_results, total=len(proxy_args), desc="测试进度", unit="个"):
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

    def _find_available_port(self, start_port=9090, max_attempts=1000):
        """查找可用的端口，在多进程环境中确保唯一性"""
        import socket
        import os

        # 在多进程中，通过进程ID增加随机性
        process_offset = (os.getpid() % 100) * 10
        actual_start = start_port + process_offset

        for port in range(actual_start, actual_start + max_attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(('127.0.0.1', port))
                    return port
            except OSError:
                continue

        # 如果在偏移范围内找不到，尝试原始范围
        for port in range(start_port, start_port + max_attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(('127.0.0.1', port))
                    return port
            except OSError:
                continue

        raise RuntimeError(f"无法找到可用端口 (尝试了 {start_port} 到 {start_port + max_attempts - 1})")

    def start(self, timeout=60):
        """启动mihomo服务"""
        try:
            # 先测试配置
            cmd_test = ['mihomo', '-f', self.config_path, '-t']
            result = subprocess.run(cmd_test, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                error_msg = f"配置测试失败: {result.stderr}"
                if result.stdout:
                    error_msg += f"\n标准输出: {result.stdout}"
                raise Exception(error_msg)

            # 启动mihomo服务
            cmd_start = ['mihomo', '-f', self.config_path, '-ext-ctl', f"127.0.0.1:{self.api_port}"]
            self.process = subprocess.Popen(cmd_start, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # 等待服务启动，增加重试次数和更短的检查间隔
            start_time = time.time()
            check_count = 0
            max_checks = int(timeout / 0.2)  # 每200ms检查一次

            while check_count < max_checks:
                try:
                    # 使用更短的超时时间进行检查
                    response = requests.get(f"{self.api_url}/version", timeout=1)
                    if response.status_code == 200:
                        return True
                except requests.exceptions.RequestException:
                    # 忽略连接错误，继续等待
                    pass

                check_count += 1
                time.sleep(0.2)  # 200ms间隔

            # 如果超时，获取进程状态信息
            if self.process.poll() is None:
                # 进程还在运行，可能是启动中
                raise Exception(f"mihomo服务启动超时 (进程仍在运行，PID: {self.process.pid})")
            else:
                # 进程已经退出，获取退出信息
                stdout, stderr = self.process.communicate()
                error_msg = f"mihomo服务启动失败 (退出码: {self.process.returncode})"
                if stderr:
                    error_msg += f"\n错误输出: {stderr}"
                if stdout:
                    error_msg += f"\n标准输出: {stdout}"
                raise Exception(error_msg)

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

    def health_check(self, proxy_name, timeout=10, url="https://www.gstatic.com/generate_204"):
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
            # 配置有效，使用MihomoManager进行健康检查
            manager = MihomoManager(config_path)
            try:
                manager.start(timeout=30)
                health_result = manager.health_check(proxy['name'], timeout=timeout)
                return {
                    'name': proxy['name'],
                    'config_valid': True,
                    'health_status': health_result['status'],
                    'message': health_result['message'],
                    'delay': health_result['delay']
                }
            finally:
                manager.stop()
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
