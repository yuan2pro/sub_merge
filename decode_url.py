import base64
import json
import logging
import socket
import sys
import uuid
from urllib.parse import parse_qs, urlparse

import geoip2.database
import requests
import yaml

# 载入 MaxMind 提供的数据库文件
reader = geoip2.database.Reader('GeoLite2-Country.mmdb')

# 配置日志记录器
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(lineno)d - %(message)s')


# Clash 和 sing-box 支持的加密方式列表
supported_ciphers = [
    'rc4-md5', 'aes-128-cfb', 'aes-128-gcm', 'aes-256-gcm',
    'aes-256-cfb', 'chacha20-ietf-poly1305',
    '2022-blake3-aes-128-gcm', '2022-blake3-aes-256-gcm'
]

# 支持的 XTLS flow 类型映射 (xtls-rprx-direct 已废弃，使用 xtls-rprx-origin 或移除)
supported_xtls_flows = {
    'xtls-rprx-vision': 'xtls-rprx-vision',
    'xtls-rprx-origin': 'xtls-rprx-origin',
    'xtls-rprx-origin-udp443': 'xtls-rprx-origin-udp443',
    'xtls-rprx-direct': 'xtls-rprx-origin'  # 映射废弃的 direct 到 origin
}

def decode_vless_link(vless_link):
    """Parse VLESS protocol URL and return Clash-compatible format"""
    try:
        # 尝试解析为YAML格式
        try:
            node_yaml = yaml.safe_load(vless_link)
            if isinstance(node_yaml, dict) and node_yaml.get('type') == 'vless':
                # 生成基础名称和获取国旗
                base_name = node_yaml.get('name', f"Node-{str(uuid.uuid4())[:8]}")
                node = {
                    'type': 'vless',
                    'name': base_name,
                    'server': node_yaml['server'],
                    'port': int(node_yaml['port']),
                    'uuid': node_yaml['uuid'],
                    'network': node_yaml.get('network', 'tcp'),
                    'tls': node_yaml.get('tls', False),
                    'udp': node_yaml.get('udp', True),
                    'skip-cert-verify': node_yaml.get('skip-cert-verify', True),
                }
                if 'flow' in node_yaml:
                    flow = node_yaml['flow']
                    if flow in supported_xtls_flows:
                        node['flow'] = supported_xtls_flows[flow]
                    else:
                        logging.warning(f"不支持的 XTLS flow 类型 {flow}，已移除")
                if 'servername' in node_yaml:
                    node['sni'] = node_yaml['servername']
                
                # 根据不同传输方式添加对应配置
                if node['network'] == 'ws' and 'ws-opts' in node_yaml:
                    node['ws-opts'] = node_yaml['ws-opts']
                elif node['network'] == 'grpc' and 'grpc-opts' in node_yaml:
                    node['grpc-opts'] = node_yaml['grpc-opts']
                elif node['network'] == 'http' and 'http-opts' in node_yaml:
                    node['http-opts'] = node_yaml['http-opts']
                elif node['network'] == 'h2' and 'h2-opts' in node_yaml:
                    node['h2-opts'] = node_yaml['h2-opts']
                    node['tls'] = True
                elif node['network'] == 'quic' and 'quic-opts' in node_yaml:
                    node['quic-opts'] = node_yaml['quic-opts']
                
                # 支持 reality
                if 'reality-opts' in node_yaml:
                    node['reality-opts'] = node_yaml['reality-opts']
                if 'client-fingerprint' in node_yaml:
                    node['client-fingerprint'] = node_yaml['client-fingerprint']
                
                return node
        except:
            pass

        # 如果不是YAML格式，按URL格式解析
        parsed_url = urlparse(vless_link)
        params = parse_qs(parsed_url.query)

        # 生成基础名称
        random_name = f"Node-{str(uuid.uuid4())[:8]}"
        # 获取国旗 emoji
        server = parsed_url.hostname.strip()
        # 设置加密方式，VLESS 默认使用 none
        encryption = params.get('encryption', ['none'])[0]
        security = params.get('security', ['tls'])[0]  # 默认使用 tls
        # 支持 flow 参数（例如 xtls-rprx-vision）
        flow = params.get('flow', [''])[0]

        # 检查必要字段
        if not parsed_url.hostname or not parsed_url.port or not parsed_url.username:
            return None

        node = {
            'type': 'vless',
            'name': random_name,
            'server': parsed_url.hostname.strip(),
            'port': int(parsed_url.port),
            'uuid': parsed_url.username,
            'tls': True if security == 'tls' else False,
            'network': params.get('type', ['tcp'])[0],
            'udp': True,
            'skip-cert-verify': True,  # 默认跳过证书验证以提高连接成功率
            'alpn': ['h2', 'http/1.1'],  # 添加 ALPN 支持
        }
        if flow:
            if flow in supported_xtls_flows:
                node['flow'] = supported_xtls_flows[flow]
            else:
                logging.warning(f"不支持的 XTLS flow 类型 {flow}，已移除")

        sni = params.get('sni', [''])[0] or parsed_url.hostname
        if sni:
            node['sni'] = sni

        # 根据不同传输方式添加对应配置
        net = node.get('network')
        if net == 'ws':
            ws_opts = {'path': params.get('path', ['/'])[0]}
            headers = {}
            if 'host' in params:
                headers['Host'] = params['host'][0]
            for k, v in params.items():
                if k.lower().startswith('header-'):
                    headers[k[7:]] = v[0]
            if headers:
                ws_opts['headers'] = headers
            node['ws-opts'] = ws_opts
        elif net == 'grpc':
            grpc_opts = {}
            service_name = params.get('serviceName', [''])[0]
            if service_name:
                grpc_opts['grpc-service-name'] = service_name
            if grpc_opts:
                node['grpc-opts'] = grpc_opts
        elif net == 'http':
            http_opts = {}
            # 只在参数非空时才添加到数组
            if 'path' in params and params['path'][0].strip():
                http_opts['path'] = [params['path'][0].strip()]
            if 'host' in params and params['host'][0].strip():
                http_opts['headers'] = {'Host': [params['host'][0].strip()]}  # Host 需要是一个数组
            # 只有当http_opts有内容时才添加
            if http_opts:
                node['http-opts'] = http_opts
        elif net == 'h2':
            h2_opts = {}
            # 确保path和host参数非空
            if 'path' in params and params['path'][0].strip():
                h2_opts['path'] = params['path'][0].strip()
            if 'host' in params and params['host'][0].strip():
                h2_opts['host'] = [params['host'][0].strip()]
            if h2_opts:
                node['h2-opts'] = h2_opts
            node['tls'] = True
        elif net == 'quic':
            quic_opts = {}
            if 'quicSecurity' in params:
                quic_opts['security'] = params['quicSecurity'][0]
            if 'key' in params:
                quic_opts['key'] = params['key'][0]
            if 'type' in params:
                quic_opts['type'] = params['type'][0]
            if quic_opts:
                node['quic-opts'] = quic_opts

        # 支持 reality
        if security == 'reality':
            node['reality-opts'] = {
                'public-key': params.get('pbk', [''])[0],
                'short-id': params.get('sid', [''])[0]
            }
            if 'fp' in params:
                node['utls'] = {
                    'enabled': True,
                    'fingerprint': params['fp'][0]
                }
            else:
                # 默认使用chrome指纹以提高兼容性
                node['utls'] = {
                    'enabled': True,
                    'fingerprint': 'chrome'
                }

        return node
    except Exception as e:
        logging.error(f"Error parsing VLESS link: {e}")
        return None

def decode_ss_link(ss_link):
    """Parse Shadowsocks protocol URL and return Clash-compatible format"""
    try:
        if ss_link.startswith('ss://'):
            ss_link = ss_link[5:]
        
        # 移除名称部分，我们将使用随机名称
        if '#' in ss_link:
            ss_link = ss_link.split('#', 1)[0]
        
        # 生成基础名称
        base_name = f"Node-{str(uuid.uuid4())[:8]}"
        
        # Try to decode the main part
        try:
            decoded = base64.b64decode(ss_link).decode('utf-8')
            if '@' in decoded:
                method_pass, server_port = decoded.split('@', 1)
                method, password = method_pass.split(':', 1)
                server, port = server_port.split(':', 1)
            else:
                raise ValueError("Invalid SS link format")
        except:
            # If the first decode fails, try parsing the traditional format
            if '@' in ss_link:
                first_part, server_port = ss_link.split('@', 1)
                try:
                    method_pass = base64.b64decode(first_part).decode('utf-8')
                    method, password = method_pass.split(':', 1)
                except:
                    method, password = first_part.split(':', 1)
                if '#' in server_port:
                    server_port, name = server_port.split('#', 1)
                server, port = server_port.split(':', 1)
            else:
                raise ValueError("Invalid SS link format")
        
        # 修正 cipher 字段，去除可能的 'ss' 前缀
        cipher = method.lower()
        if cipher.startswith('ss') and cipher != 'ssr':
            cipher = cipher.replace('ss', '', 1)
            cipher = cipher.strip('-')

        # 对于2022协议，需要将password从base64解码并转换为hex格式
        if cipher.startswith('2022'):
            decoded_key = base64.b64decode(password)
            if len(decoded_key) != 32:
                logging.warning(f"Invalid key length {len(decoded_key)} for {cipher}, expected 32 bytes. Skipping node.")
                return None
            password = decoded_key.hex()

        if cipher not in supported_ciphers:
            logging.warning(f"SS节点加密方式 {cipher} 不被Clash和sing-box同时支持，已丢弃")
            return None
        return {
            'type': 'ss',
            'name': base_name,
            'server': server,
            'port': int(port),
            'cipher': cipher,
            'password': password,
            'udp': True
        }
    except Exception as e:
        logging.error(f"Error parsing SS link: {e}")
        return None

def decode_trojan_link(trojan_link):
    """Parse Trojan protocol URL and return Clash-compatible format"""
    try:
        parsed_url = urlparse(trojan_link)
        params = parse_qs(parsed_url.query)

        # 生成基础名称
        base_name = f"Node-{str(uuid.uuid4())[:8]}"
        
        # 检查必要字段
        if not parsed_url.hostname or not parsed_url.port or not parsed_url.username:
            return None
            
        node = {
            'type': 'trojan',
            'name': base_name,
            'server': parsed_url.hostname.strip(),
            'port': int(parsed_url.port),
            'password': parsed_url.username,
            'sni': params.get('sni', [''])[0] or parsed_url.hostname,
            'skip-cert-verify': True,  # 默认跳过证书验证以提高连接成功率
            'udp': True,
            'network': params.get('type', ['tcp'])[0],
            'alpn': ['h2', 'http/1.1'],  # 添加 ALPN 支持
        }
        if 'client-fingerprint' in params:
            node['client-fingerprint'] = params['client-fingerprint'][0]
            
        # 处理不同的传输协议
        if node['network'] == 'ws':
            ws_opts = {'path': params.get('path', ['/'])[0]}
            headers = {}
            if 'host' in params:
                headers['Host'] = params['host'][0]
            for k, v in params.items():
                if k.lower().startswith('header-'):
                    headers[k[7:]] = v[0]
            if headers:
                ws_opts['headers'] = headers
            node['ws-opts'] = ws_opts
        elif node['network'] == 'grpc':
            grpc_opts = {}
            service_name = params.get('serviceName', [''])[0]
            if service_name:
                grpc_opts['grpc-service-name'] = service_name
            node['grpc-opts'] = grpc_opts
        elif node['network'] == 'http':
            http_opts = {}
            # 只在参数非空时才添加到数组
            if 'path' in params and params['path'][0].strip():
                http_opts['path'] = [params['path'][0].strip()]
            if 'host' in params and params['host'][0].strip():
                http_opts['headers'] = {'Host': [params['host'][0].strip()]}  # Host 需要是一个数组
            # 只有当http_opts有内容时才添加
            if http_opts:
                node['http-opts'] = http_opts
        elif node['network'] == 'h2':
            h2_opts = {}
            # 确保path和host参数非空
            if 'path' in params and params['path'][0].strip():
                h2_opts['path'] = params['path'][0].strip()
            if 'host' in params and params['host'][0].strip():
                h2_opts['host'] = [params['host'][0].strip()]
            if h2_opts:
                node['h2-opts'] = h2_opts
            node['tls'] = True
        return node
    except Exception as e:
        logging.error(f"Error parsing Trojan link: {e}")
        return None

def decode_ssr_link(ssr_link):
    """Parse ShadowsocksR protocol URL and return Clash-compatible format"""
    try:
        if ssr_link.startswith('ssr://'):
            ssr_link = ssr_link[6:]
        
        decoded = base64.b64decode(ssr_link).decode('utf-8')
        if not decoded:
            return None

        # SSR link format: server:port:protocol:method:obfs:base64pass/?obfsparam=base64param&protoparam=base64param&remarks=base64remarks&group=base64group
        # Split main part and params part
        if '?' in decoded:
            main_part, params_str = decoded.split('?', 1)
        else:
            main_part, params_str = decoded, ''

        # Parse main part
        parts = main_part.split(':')
        if len(parts) < 6:
            raise ValueError("Invalid SSR link format")
            
        server, port, protocol, method, obfs = parts[:5]
        password_b64 = parts[5].split('/?')[0] if '/' in parts[5] else parts[5]
        password = base64.b64decode(password_b64 + '=' * (-len(password_b64) % 4)).decode()

        # Parse parameters
        params = {}
        if params_str:
            for param in params_str.split('&'):
                if '=' in param:
                    key, value = param.split('=', 1)
                    # Add padding
                    value = value + '=' * (-len(value) % 4)
                    try:
                        params[key] = base64.b64decode(value).decode()
                    except:
                        params[key] = value

        # 生成基础名称
        random_name = f"Node-{str(uuid.uuid4())[:8]}"
        # Construct node
        cipher = method.lower()

        if cipher not in supported_ciphers:
            logging.warning(f"SSR节点加密方式 {cipher} 不被Clash和sing-box同时支持，已丢弃")
            return None
        node = {
            'type': 'ssr',
            'name': random_name,
            'server': server,
            'port': int(port),
            'cipher': cipher,
            'password': password,
            'protocol': protocol.lower(),
            'obfs': obfs.lower(),
            'udp': True
        }

        # Add optional parameters if they exist
        if 'obfsparam' in params:
            node['obfs-param'] = params['obfsparam']
        if 'protoparam' in params:
            node['protocol-param'] = params['protoparam']

        return node
    except Exception as e:
        logging.error(f"Error parsing SSR link: {e}")
        return None

def decode_hysteria2_link(hy2_link):
    """Parse Hysteria2 protocol URL and return Clash-compatible format"""
    try:
        parsed_url = urlparse(hy2_link)
        params = parse_qs(parsed_url.query)

        # 生成基础名称
        random_name = f"Node-{str(uuid.uuid4())[:8]}"
        node = {
            'type': 'hysteria2',
            'name': random_name,
            'server': parsed_url.hostname,
            'port': int(parsed_url.port),
            'password': parsed_url.username,
            'sni': params.get('sni', [''])[0] or parsed_url.hostname,
            'skip-cert-verify': params.get('insecure', ['0'])[0] == '1',
            'tls': True,
            'alpn': ['h3'],
            'udp': True,
            'hop-interval': int(params.get('hop', ['10'])[0]),
        }
        
        # 添加可选的 Hysteria2 特定参数
        if 'obfs' in params:
            node['obfs'] = params['obfs'][0]
        if 'obfs-password' in params:
            node['obfs-password'] = params['obfs-password'][0]
        if 'client-fingerprint' in params:
            node['client-fingerprint'] = params['client-fingerprint'][0]
        if 'download-bandwidth' in params:
            node['down'] = int(params['download-bandwidth'][0])
        if 'upload-bandwidth' in params:
            node['up'] = int(params['upload-bandwidth'][0])

        return node
    except Exception as e:
        logging.error(f"Error parsing Hysteria2 link: {e}")
        return None

from threading import Lock

_url_lock = Lock()

# def get_country_emoji(ip_address):
#     try:
#         ip_address = socket.gethostbyname(ip_address)
#         # 查询 IP 地址的地理位置信息
#         response = reader.country(ip_address)
#         # 获取国家代码
#         country_code = response.country.iso_code
#         # 将国家代码转换为 emoji
#         if country_code:
#             # 国家代码转换为 emoji
#             emoji = chr(ord(country_code[0]) + 127397) + chr(ord(country_code[1]) + 127397)
#             logging.debug(f"{ip_address} emoji is {emoji}")
#             return emoji
#         else:
#             logging.debug(f"{ip_address} emoji is None")
#             return "🌍"
#     except Exception as e:
#         logging.error(f"Error getting country emoji for {ip_address}: {e}")
#         return "🌍"

def decode_url_to_nodes(url):
    try:
        # 使用锁确保多进程环境下URL请求安全
        with _url_lock:
            # Fetch content from URL
            response = requests.get(url)
            response.raise_for_status()  # Raise an exception for bad status codes
            
            # Get content and decode if it's base64 encoded
        content = response.text.strip()
        try:
            decoded_content = base64.b64decode(content).decode('utf-8')
        except:
            decoded_content = content

        # 优先尝试解析为 YAML，若包含 proxies 字段则直接返回
        try:
            yaml_obj = yaml.safe_load(decoded_content)
            if isinstance(yaml_obj, dict) and 'proxies' in yaml_obj and isinstance(yaml_obj['proxies'], list):
                logging.info('检测到 YAML 格式')
                proxies = yaml_obj['proxies']
                return proxies
        except Exception as e:
            pass

        # 否则按原有方式逐行解析
        nodes = []
        for line in decoded_content.splitlines():
            line = line.strip()
            if line.startswith(('vmess://', 'vless://', 'hysteria2://', 'ss://', 'ssr://', 'trojan://')):
                # Convert the node to Clash format
                try:
                    if line.startswith('vmess://'):
                        node_data = json.loads(base64.b64decode(line[8:]).decode())
                        # 生成基础名称
                        random_name = f"Node-{str(uuid.uuid4())[:8]}"
                        # 设置默认加密方式为 auto，确保与 Clash 兼容
                        cipher = node_data.get('security', 'auto')
                        # 如果加密方式为 none，改为 auto
                        if cipher == 'none':
                            cipher = 'auto'
                        # 检查必要字段
                        if not node_data.get('add') or not node_data.get('port') or not node_data.get('id'):
                            continue
                            
                        node = {
                            'type': 'vmess',
                            'name': random_name,
                            'server': node_data.get('add', '').strip(),
                            'port': int(node_data.get('port', 0)),
                            'uuid': node_data.get('id', ''),
                            'alterId': int(node_data.get('aid', 0)),
                            'cipher': cipher,
                            'tls': True if node_data.get('tls') == 'tls' else False,
                            'udp': True,
                            'skip-cert-verify': True,  # 默认跳过证书验证以提高连接成功率
                        }
                        # 支持 network 字段
                        if 'net' in node_data:
                            node['network'] = node_data['net']
                            
                        # 根据不同传输方式添加对应配置
                        if node.get('network') == 'ws':
                            ws_opts = {'path': node_data.get('path', '/')}
                            headers = {}
                            if 'host' in node_data:
                                headers['Host'] = node_data['host']
                            if headers:
                                ws_opts['headers'] = headers
                            node['ws-opts'] = ws_opts
                        elif node.get('network') == 'grpc':
                            grpc_opts = {}
                            if 'serviceName' in node_data:
                                grpc_opts['grpc-service-name'] = node_data['serviceName']
                            node['grpc-opts'] = grpc_opts
                        elif node.get('network') == 'http':
                            http_opts = {}
                            # 只在参数非空时才添加到数组
                            if 'path' in node_data and node_data['path'].strip():
                                http_opts['path'] = [node_data['path'].strip()]
                            if 'host' in node_data and node_data['host'].strip():
                                http_opts['headers'] = {'Host': [node_data['host'].strip()]}  # Host 需要是一个数组
                            # 只有当http_opts有内容时才添加
                            if http_opts:
                                node['http-opts'] = http_opts
                        elif node.get('network') == 'h2':
                            h2_opts = {}
                            # 确保path和host参数非空
                            if 'path' in node_data and node_data['path'].strip():
                                h2_opts['path'] = node_data['path'].strip()
                            if 'host' in node_data and node_data['host'].strip():
                                h2_opts['host'] = [node_data['host'].strip()]
                            if h2_opts:
                                node['h2-opts'] = h2_opts
                                node['tls'] = True
                        elif node.get('network') == 'quic':
                            quic_opts = {}
                            if 'quicSecurity' in node_data:
                                quic_opts['security'] = node_data['quicSecurity']
                            if 'key' in node_data:
                                quic_opts['key'] = node_data['key']
                            if 'type' in node_data:
                                quic_opts['type'] = node_data['type']
                            node['quic-opts'] = quic_opts
                        
                        # 支持 sni 字段
                        if 'sni' in node_data:
                            node['sni'] = node_data['sni']
                        
                        # 支持 reality
                        if node_data.get('security') == 'reality':
                            node['reality-opts'] = {
                                'public-key': node_data.get('pbk', ''),
                                'short-id': node_data.get('sid', '')
                            }
                            if 'fp' in node_data:
                                node['utls'] = {
                                    'enabled': True,
                                    'fingerprint': node_data['fp']
                                }
                            else:
                                # 默认使用chrome指纹以提高兼容性
                                node['utls'] = {
                                    'enabled': True,
                                    'fingerprint': 'chrome'
                                }
                        nodes.append(node)
                    elif line.startswith('vless://'):
                        node = decode_vless_link(line)
                        if node:
                            nodes.append(node)
                    elif line.startswith('hysteria2://'):
                        node = decode_hysteria2_link(line)
                        if node:
                            nodes.append(node)
                    elif line.startswith('ss://'):
                        node = decode_ss_link(line)
                        if node:
                            nodes.append(node)
                    elif line.startswith('trojan://'):
                        node = decode_trojan_link(line)
                        if node:
                            nodes.append(node)
                    elif line.startswith('ssr://'):
                        node = decode_ssr_link(line)
                        if node:
                            nodes.append(node)
                except Exception as e:
                    logging.error(f"Error parsing line '{line[:50]}...': {e}")
                    continue
        return nodes
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching URL: {e}")
        return []
    except Exception as e:
        logging.error(f"Error processing nodes: {e}")
        return []

if __name__ == "__main__":
    try:
        nodes = decode_url_to_nodes(url = "https://raw.githubusercontent.com/ripaojiedian/freenode/main/sub")
        yaml_output = yaml.dump({'proxies': nodes}, allow_unicode=True)
        print(yaml_output)  # 保留这一个print用于输出YAML内容
    except ImportError as e:
        logging.error(f"缺少必要的依赖库: {e}")
        logging.error("请运行以下命令安装所需依赖:")
        logging.error("pip install -r requirements.txt")
        sys.exit(1)
    except Exception as e:
        logging.error(f"程序执行出错: {e}")
        sys.exit(1)
