import concurrent.futures
import logging
import os
import socket
import time
from multiprocessing import cpu_count

import requests
import yaml
from requests.exceptions import RequestException, Timeout
from tqdm import tqdm

# 配置日志记录器 (保留用于代理测试时的警告信息)
logging.basicConfig(level=logging.WARNING, format='%(message)s')

def test_single_proxy_process(proxy_config, timeout=10, test_url='http://speed.cloudflare.com/__down?bytes=10485760', retry_count=3):
    """
    为多进程执行准备的独立测试函数
    """
    tester = ProxySpeedTester(timeout, test_url, retry_count)
    return tester.test_single_proxy(proxy_config)

class ProxySpeedTester:
    def __init__(self, timeout=10, test_url='http://speed.cloudflare.com/__down?bytes=10485760', retry_count=3):
        """
        初始化速度测试器
        :param timeout: 超时时间(秒)
        :param test_url: 用于测试的URL，使用10MB下载测试真实速度
        :param retry_count: 重试次数
        """
        self.timeout = timeout
        self.test_url = test_url
        self.retry_count = retry_count

    def create_proxy_dict(self, proxy_config):
        """
        根据代理配置创建requests使用的代理字典
        """
        proxy_type = proxy_config.get('type')
        server = proxy_config.get('server')
        port = proxy_config.get('port')

        if not proxy_type or not server or not port:
            return None

        proxy_url = f"{proxy_type}://{server}:{port}"

        # 根据代理类型可能需要认证信息
        if 'username' in proxy_config and 'password' in proxy_config:
            proxy_url = f"{proxy_type}://{proxy_config['username']}:{proxy_config['password']}@{server}:{port}"

        # 对于某些代理类型，可能需要额外的参数
        if proxy_type in ['ss', 'ssr']:
            # Shadowsocks 代理需要特殊的URL格式
            cipher = proxy_config.get('cipher', 'aes-256-gcm')
            password = proxy_config.get('password', '')
            proxy_url = f"socks5://{server}:{port}"  # 简化处理，实际应该使用专用库

        # Clash 格式的映射到标准格式
        type_mapping = {
            'ss': 'socks5',
            'vmess': 'http',  # VMess 通常不支持HTTP代理
            'vless': 'http',  # VLESS 通常不支持HTTP代理
            'trojan': 'http',  # Trojan 通常不支持HTTP代理
            'hysteria2': 'http'
        }

        if proxy_type in type_mapping:
            if proxy_type in ['vmess', 'vless', 'trojan', 'hysteria2']:
                # 这些高级代理通常用于Tunneling，不适合HTTP代理测试
                # 使用直接连接测试服务端可达性
                return None
            else:
                proxy_url = f"{type_mapping[proxy_type]}://{server}:{port}"
                return {'http': proxy_url, 'https': proxy_url}
        else:
            return None

    def test_download_speed(self, proxies=None, test_size=10485760):  # 10MB default
        """
        测试下载速度，返回下载时间和速度(Mbps)
        """
        speeds = []
        response_times = []

        for i in range(self.retry_count):
            try:
                start_time = time.time()
                response = requests.get(
                    self.test_url,
                    timeout=self.timeout,
                    proxies=proxies,
                    stream=True
                )

                total_size = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        total_size += len(chunk)

                download_time = time.time() - start_time
                response_times.append(download_time)

                # 计算速度 (Mbps)
                speed_mbps = (total_size * 8) / (download_time * 1000000) if download_time > 0 else 0
                speeds.append(speed_mbps)

            except (Timeout, RequestException) as e:
                if i == self.retry_count - 1:  # 最后一次重试
                    return None, None, str(e)
                continue

        if not speeds:
            return None, None, "所有重试都失败"

        # 取平均值作为最终结果
        avg_speed = sum(speeds) / len(speeds)
        avg_response_time = sum(response_times) / len(response_times)

        return avg_speed, avg_response_time, None

    def test_upload_speed(self, proxies=None, upload_size=1048576):  # 1MB default
        """
        测试上传速度，返回上传时间和速度(Mbps)
        """
        speeds = []
        response_times = []

        for i in range(self.retry_count):
            try:
                # 生成测试数据
                upload_data = b'0' * upload_size

                start_time = time.time()
                response = requests.post(
                    'http://httpbin.org/post',  # 使用httpbin.org作为上传测试端点
                    data=upload_data,
                    timeout=self.timeout,
                    proxies=proxies,
                    headers={'Content-Type': 'application/octet-stream'}
                )

                upload_time = time.time() - start_time
                response_times.append(upload_time)

                if response.status_code == 200:
                    # 计算速度 (Mbps)
                    speed_mbps = (upload_size * 8) / (upload_time * 1000000) if upload_time > 0 else 0
                    speeds.append(speed_mbps)
                else:
                    continue

            except (Timeout, RequestException) as e:
                if i == self.retry_count - 1:  # 最后一次重试
                    return None, None, str(e)
                continue

        if not speeds:
            return None, None, "所有重试都失败"

        # 取平均值作为最终结果
        avg_speed = sum(speeds) / len(speeds)
        avg_response_time = sum(response_times) / len(response_times)

        return avg_speed, avg_response_time, None

    def test_single_proxy(self, proxy_config):
        """
        测试单个代理的上传下载速度和可连接性
        """
        name = proxy_config.get('name', 'Unknown')
        server = proxy_config.get('server', '')
        port = proxy_config.get('port', '')
        proxy_type = proxy_config.get('type', '')

        # 对于支持HTTP代理的类型，使用HTTP请求进行上传下载测试
        http_proxy_types = ['ss', 'ssr']

        if proxy_type in http_proxy_types:
            proxies = self.create_proxy_dict(proxy_config)
            if proxies:
                # 测试下载速度
                download_speed, download_time, download_error = self.test_download_speed(proxies)
                # 测试上传速度
                upload_speed, upload_time, upload_error = self.test_upload_speed(proxies)

                # 如果任一测试失败，则认为代理不可用
                if download_speed is None or upload_speed is None:
                    error_reason = download_error or upload_error or "下载或上传测试失败"
                    return {
                        'name': name,
                        'server': server,
                        'port': port,
                        'response_time': None,
                        'download_mbps': 0,
                        'upload_mbps': 0,
                        'speed_score': 0,
                        'status': 'fail',
                        'reason': error_reason
                    }

                # 计算综合速度评分（下载权重70%，上传权重30%）
                combined_score = (download_speed * 0.7 + upload_speed * 0.3) * 10
                response_time = max(download_time, upload_time) if download_time and upload_time else (download_time or upload_time)

                return {
                    'name': name,
                    'server': server,
                    'port': port,
                    'response_time': round(response_time, 2),
                    'download_mbps': round(download_speed, 2),
                    'upload_mbps': round(upload_speed, 2),
                    'speed_score': min(100, max(0, round(combined_score, 1))),
                    'status': 'pass'
                }
            else:
                # 回退到socket连接测试
                socket_result = self.test_socket_connection(proxy_config)
                return {
                    'name': name,
                    'server': server,
                    'port': port,
                    'response_time': socket_result.get('response_time'),
                    'download_mbps': 0,
                    'upload_mbps': 0,
                    'speed_score': socket_result.get('speed_score', 0),
                    'status': socket_result.get('status', 'fail'),
                    'reason': socket_result.get('reason')
                }

        elif proxy_type in ['vmess', 'vless', 'trojan', 'hysteria2']:
            # 对于高级代理类型，首先测试连接性，然后尝试模拟下载
            socket_result = self.test_socket_connection(proxy_config)
            if socket_result['status'] == 'pass':
                # 对于这些不支持HTTP代理的类型，使用连接时间作为基础评分
                # 并结合重试机制降低误判率
                connect_times = []
                for i in range(min(self.retry_count, 3)):  # 最多重试3次
                    test_result = self.test_socket_connection(proxy_config)
                    if test_result['status'] == 'pass' and test_result['response_time']:
                        connect_times.append(test_result['response_time'])

                if connect_times:
                    avg_connect_time = sum(connect_times) / len(connect_times)
                    # 使用连接时间计算评分：越快分数越高
                    speed_score = max(0, 100 - (avg_connect_time * 25))
                    return {
                        'name': name,
                        'server': server,
                        'port': port,
                        'response_time': round(avg_connect_time, 2),
                        'download_mbps': 0,  # 不支持HTTP代理，无法测试真实速度
                        'upload_mbps': 0,
                        'speed_score': round(speed_score, 1),
                        'status': 'pass'
                    }

            return socket_result
        else:
            # 对于其他类型，使用socket连接测试
            socket_result = self.test_socket_connection(proxy_config)
            return {
                'name': name,
                'server': server,
                'port': port,
                'response_time': socket_result.get('response_time'),
                'download_mbps': 0,
                'upload_mbps': 0,
                'speed_score': socket_result.get('speed_score', 0),
                'status': socket_result.get('status'),
                'reason': socket_result.get('reason')
            }

    def test_socket_connection(self, proxy_config):
        """
        使用socket连接测试代理服务器的可达性
        """
        name = proxy_config.get('name', 'Unknown')
        server = proxy_config.get('server', '')
        port = proxy_config.get('port', '')

        if not server or not port:
            return {
                'name': name,
                'server': server,
                'port': port,
                'response_time': None,
                'speed_score': 0,
                'status': 'fail',
                'reason': 'missing server or port'
            }

        try:
            start_time = time.time()

            # 创建socket连接
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)

            # 解析域名到IP
            try:
                ip_address = socket.gethostbyname(server)
            except socket.gaierror:
                return {
                    'name': name,
                    'server': server,
                    'port': port,
                    'response_time': None,
                    'speed_score': 0,
                    'status': 'fail',
                    'reason': 'DNS resolution failed'
                }

            # 尝试连接
            result = sock.connect_ex((ip_address, int(port)))
            connect_time = time.time() - start_time

            sock.close()

            if result == 0:  # 连接成功
                speed_score = max(0, 100 - (connect_time * 20))  # 简化的速度评分
                return {
                    'name': name,
                    'server': server,
                    'port': port,
                    'response_time': round(connect_time, 2),
                    'speed_score': round(speed_score, 1),
                    'status': 'pass'
                }
            else:
                return {
                    'name': name,
                    'server': server,
                    'port': port,
                    'response_time': None,
                    'speed_score': 0,
                    'status': 'fail',
                    'reason': f'connection refused (error code: {result})'
                }

        except socket.timeout:
            return {
                'name': name,
                'server': server,
                'port': port,
                'response_time': None,
                'speed_score': 0,
                'status': 'fail',
                'reason': 'socket timeout'
            }
        except Exception as e:
            return {
                'name': name,
                'server': server,
                'port': port,
                'response_time': None,
                'speed_score': 0,
                'status': 'fail',
                'reason': f'socket error: {str(e)}'
            }

    def test_proxies_batch(self, proxies_list, max_workers=None):
        """
        批量测试代理
        """
        results = []
        failed_proxies = []

        # 如果未指定max_workers，根据CPU核心数自动设置
        if max_workers is None:
            cpu_cores = cpu_count()
            max_workers = cpu_cores if cpu_cores else 4  # 默认为4个进程

        print(f"开始测试 {len(proxies_list)} 个代理... (使用 {max_workers} 个进程)")

        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_to_proxy = {
                executor.submit(test_single_proxy_process, proxy, self.timeout, self.test_url, self.retry_count): proxy
                for proxy in proxies_list
            }

            with tqdm(total=len(proxies_list), desc="测试进度", unit="个") as pbar:
                for future in concurrent.futures.as_completed(future_to_proxy):
                    result = future.result()
                    if result:
                        results.append(result)
                        if result['status'] == 'fail':
                            failed_proxies.append({
                                'name': result['name'],
                                'server': result['server'],
                                'port': result['port'],
                                'reason': result.get('reason', 'unknown')
                            })
                    pbar.update(1)

        # 按速度评分排序
        results.sort(key=lambda x: x['speed_score'] if x['speed_score'] else 0, reverse=True)

        passed_count = len([r for r in results if r['status'] == 'pass'])
        fail_count = len([r for r in results if r['status'] == 'fail'])
        print(f"测试完成: 总共 {len(results)}, 通过 {passed_count}, 失败 {fail_count}")

        return results, failed_proxies

def filter_and_save_proxies(input_yaml, output_yaml, min_speed_score=10, max_failures=None):
    """
    过滤代理并保存结果
    """
    # 下载测试需要更长的超时时间
    tester = ProxySpeedTester(timeout=30, retry_count=3)

    # 读取原始代理
    with open(input_yaml, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
        proxies = data.get('proxies', [])

    print(f"读取到 {len(proxies)} 个代理")

    # 测试代理速度
    test_results, failed_proxies = tester.test_proxies_batch(proxies)

    # 过滤通过的代理
    passed_proxies = []
    speed_stats = []

    for result in test_results:
        if result['status'] == 'pass' and result['speed_score'] >= min_speed_score:
            # 找到原始代理配置
            original_proxy = next((p for p in proxies if p['name'] == result['name']), None)
            if original_proxy:
                passed_proxies.append(original_proxy)
                speed_stats.append({
                    'name': result['name'],
                    'download_mbps': result.get('download_mbps', 0),
                    'upload_mbps': result.get('upload_mbps', 0),
                    'speed_score': result['speed_score'],
                    'response_time': result['response_time']
                })

    # 保存过滤后的代理
    os.makedirs(os.path.dirname(output_yaml), exist_ok=True)
    with open(output_yaml, 'w', encoding='utf-8') as f:
        yaml.safe_dump({'proxies': passed_proxies}, f, allow_unicode=True)

    # 输出统计信息
    print(f"过滤结果: 原始 {len(proxies)} 个 → 保留 {len(passed_proxies)} 个")

    if speed_stats:
        avg_speed_score = sum(s['speed_score'] for s in speed_stats) / len(speed_stats)
        avg_response = sum(s['response_time'] for s in speed_stats if s['response_time']) / len(speed_stats)

        # 计算平均下载和上传速度（只对有速度数据的代理）
        download_stats = [s['download_mbps'] for s in speed_stats if s['download_mbps'] > 0]
        upload_stats = [s['upload_mbps'] for s in speed_stats if s['upload_mbps'] > 0]

        stats_info = f"平均速度评分: {avg_speed_score:.1f}, 平均响应: {avg_response:.2f}s"

        if download_stats and upload_stats:
            avg_download = sum(download_stats) / len(download_stats)
            avg_upload = sum(upload_stats) / len(upload_stats)
            stats_info += f", 平均下载: {avg_download:.2f}Mbps, 平均上传: {avg_upload:.2f}Mbps"
        elif download_stats:
            avg_download = sum(download_stats) / len(download_stats)
            stats_info += f", 平均下载: {avg_download:.2f}Mbps"

        print(stats_info)

    return len(passed_proxies), len(proxies) - len(passed_proxies)

def process_single_file(file_info, min_speed_score=10):
    """
    处理单个文件，用于多进程执行
    :param file_info: 包含输入输出路径的字典
    :param min_speed_score: 最低速度评分
    """
    input_path = file_info['input_path']
    output_path = file_info['output_path']
    filename = file_info['filename']

    try:
        print(f"开始处理文件: {filename}")
        passed, filtered = filter_and_save_proxies(
            input_path, output_path,
            min_speed_score=min_speed_score
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
    主函数，使用多进程并行处理所有 merged_proxies 文件
    """
    proxy_dir = 'sub'
    min_speed_score = 10

    # 查找所有 merged_proxies_*.yaml 文件
    file_list = []
    for filename in os.listdir(proxy_dir):
        if filename.endswith('.yaml'):
            input_path = os.path.join(proxy_dir, filename)
            output_path = input_path.replace('.yaml', '_speed_test.yaml')
            file_list.append({
                'filename': filename,
                'input_path': input_path,
                'output_path': output_path
            })

    if not file_list:
        print("没有找到需要处理的YAML文件")
        return

    # 使用多进程并行处理文件
    max_workers = min(len(file_list), cpu_count() * 2)  # 根据文件数量和CPU核心数设置进程数

    print(f"发现 {len(file_list)} 个YAML文件，开始并行处理... (使用 {max_workers} 个进程)")

    processed_results = []

    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {
            executor.submit(process_single_file, file_info, min_speed_score): file_info
            for file_info in file_list
        }

        with tqdm(total=len(file_list), desc="文件处理进度", unit="个") as pbar:
            for future in concurrent.futures.as_completed(future_to_file):
                result = future.result()
                processed_results.append(result)
                pbar.update(1)

    # 统计结果
    processed_count = len([r for r in processed_results if r['success']])
    total_passed = sum(r['passed'] for r in processed_results if r['success'])
    total_filtered = sum(r['filtered'] for r in processed_results if r['success'])
    error_count = len([r for r in processed_results if not r['success']])

    print(f"\n并行处理完成: {processed_count} 个文件成功, {error_count} 个失败")
    print(f"总计: 保留 {total_passed} 个代理, 过滤 {total_filtered} 个代理")

    if error_count > 0:
        print("\n失败的文件:")
        for result in processed_results:
            if not result['success']:
                print(f"  - {result['filename']}: {result.get('error', '未知错误')}")
    for file_info in file_list:
        if os.path.exists(file_info['input_path']):
            os.remove(file_info['input_path'])
            print(f"已删除: {file_info['input_path']}")
if __name__ == '__main__':
    main()

