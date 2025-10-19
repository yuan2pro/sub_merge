import base64
import json
import logging
import sys
import uuid
from urllib.parse import parse_qs, urlparse

import requests
import yaml

# 配置日志记录器
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(lineno)d - %(message)s')


def decode_vless_link(vless_link):
    """Parse VLESS protocol URL and return Clash-compatible format"""
    try:
        parsed_url = urlparse(vless_link)
        params = parse_qs(parsed_url.query)

        # 生成随机名称
        random_name = f"Node-{str(uuid.uuid4())[:8]}"
        # 设置加密方式，VLESS 默认使用 none
        encryption = params.get('encryption', ['none'])[0]
        security = params.get('security', ['tls'])[0]  # 默认使用 tls
        
        node = {
            'type': 'vless',
            'name': random_name,
            'server': parsed_url.hostname,
            'port': int(parsed_url.port),
            'uuid': parsed_url.username,
            'network': params.get('type', ['tcp'])[0],
            'tls': True if security == 'tls' else False,
            'udp': True,  # 启用 UDP 支持
            'skip-cert-verify': params.get('allowInsecure', ['0'])[0] == '1',
            'servername': params.get('sni', [''])[0] or parsed_url.hostname  # 优先使用 SNI，否则使用服务器地址
        }

        # Add ws-opts if network is websocket
        if node['network'] == 'ws':
            node['ws-opts'] = {
                'path': params.get('path', [''])[0],
                'headers': {
                    'Host': params.get('host', [''])[0]
                }
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
        
        # 生成随机名称
        random_name = f"Node-{str(uuid.uuid4())[:8]}"
            
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
        # Clash 和 sing-box 支持的加密方式列表
        supported_ciphers = [
            'aes-128-gcm', 'aes-256-gcm', 'chacha20-ietf-poly1305',
            '2022-blake3-aes-128-gcm', '2022-blake3-aes-256-gcm'
        ]
        if cipher not in supported_ciphers:
            logging.warning(f"SS节点加密方式 {cipher} 不被Clash和sing-box同时支持，已丢弃")
            return None
        return {
            'type': 'ss',
            'name': random_name,
            'server': server,
            'port': int(port),
            'cipher': cipher,
            'password': password,
            'udp': True  # 启用 UDP 支持
        }
    except Exception as e:
        logging.error(f"Error parsing SS link: {e}")
        return None

def decode_trojan_link(trojan_link):
    """Parse Trojan protocol URL and return Clash-compatible format"""
    try:
        parsed_url = urlparse(trojan_link)
        params = parse_qs(parsed_url.query)

        # 生成随机名称
        random_name = f"Node-{str(uuid.uuid4())[:8]}"
        node = {
            'type': 'trojan',
            'name': random_name,
            'server': parsed_url.hostname,
            'port': int(parsed_url.port),
            'password': parsed_url.username,
            'udp': True,  # 启用 UDP 支持
            'sni': params.get('sni', [''])[0] or parsed_url.hostname,  # 优先使用 SNI，否则使用服务器地址
            'skip-cert-verify': params.get('allowInsecure', ['0'])[0] == '1',
            'network': params.get('type', ['tcp'])[0],
            'tls': True,  # Trojan 必须启用 TLS
            'alpn': ['h2', 'http/1.1']  # 添加 ALPN 支持
        }

        # Add ws-opts if network is websocket
        if node['network'] == 'ws':
            node['ws-opts'] = {
                'path': params.get('path', [''])[0],
                'headers': {
                    'Host': params.get('host', [''])[0]
                }
            }

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

        # 生成随机名称
        random_name = f"Node-{str(uuid.uuid4())[:8]}"
        # Construct node
        cipher = method.lower()
        # Clash 和 sing-box 支持的加密方式列表
        supported_ciphers = [
            'aes-128-gcm', 'aes-256-gcm', 'chacha20-ietf-poly1305',
            '2022-blake3-aes-128-gcm', '2022-blake3-aes-256-gcm'
        ]
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
            'protocol': protocol.lower(),  # 确保协议为小写
            'obfs': obfs.lower(),  # 确保混淆为小写
            'udp': True  # 启用 UDP 支持
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

        # 生成随机名称
        random_name = f"Node-{str(uuid.uuid4())[:8]}"
        node = {
            'type': 'hysteria2',
            'name': random_name,
            'server': parsed_url.hostname,
            'port': int(parsed_url.port),
            'password': parsed_url.username,  # Hysteria2 使用 password 而不是 uuid
            'sni': params.get('sni', [''])[0] or parsed_url.hostname,  # 优先使用 SNI，否则使用服务器地址
            'skip-cert-verify': params.get('insecure', ['0'])[0] == '1',
            'tls': True,  # Hysteria2 必须启用 TLS
            'alpn': ['h3'],  # Hysteria2 默认使用 HTTP/3
            'udp': True,  # 启用 UDP 支持
            'hop-interval': 10,  # 添加默认的连接保活间隔
        }

        return node
    except Exception as e:
        logging.error(f"Error parsing Hysteria2 link: {e}")
        return None

def decode_url_to_nodes(url):
    try:
        # Fetch content from URL
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        # Get content and decode if it's base64 encoded
        content = response.text.strip()
        try:
            decoded_content = base64.b64decode(content).decode('utf-8')
        except:
            decoded_content = content
        
        # Parse the content
        nodes = []
        for line in decoded_content.splitlines():
            line = line.strip()
            if line.startswith(('vmess://', 'vless://', 'hysteria2://', 'ss://', 'ssr://', 'trojan://')):
                # Convert the node to Clash format
                try:
                    if line.startswith('vmess://'):
                        node_data = json.loads(base64.b64decode(line[8:]).decode())
                        # 生成随机名称
                        random_name = f"Node-{str(uuid.uuid4())[:8]}"
                        # 设置默认加密方式为 auto，确保与 Clash 兼容
                        cipher = node_data.get('security', 'auto')
                        # 如果加密方式为 none，改为 auto
                        if cipher == 'none':
                            cipher = 'auto'
                            
                        node = {
                            'type': 'vmess',
                            'name': random_name,
                            'server': node_data.get('add', ''),
                            'port': int(node_data.get('port', 0)),
                            'uuid': node_data.get('id', ''),
                            'alterId': int(node_data.get('aid', 0)),
                            'cipher': cipher,
                            'tls': True if node_data.get('tls') == 'tls' else False
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