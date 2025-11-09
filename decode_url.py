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
                emoji = get_country_emoji(node_yaml['server'])
                node = {
                    'type': 'vless',
                    'name': f"{emoji} {base_name}",
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
                
                # 支持 reality (仅限 VLESS 和其他支持的协议)
                if 'reality-opts' in node_yaml:
                    node['reality-opts'] = node_yaml['reality-opts']
                if 'reality-opts' in node:
                    pbk = node['reality-opts'].get('public-key', '')
                    sid = node['reality-opts'].get('short-id', '')
                    try:
                        if pbk:
                            # Try to decode base64, add padding if needed
                            try:
                                base64.b64decode(pbk)
                                node['reality-opts']['public-key'] = pbk
                            except:
                                # Try with padding
                                missing_padding = len(pbk) % 4
                                if missing_padding:
                                    padded_pbk = pbk + '=' * (4 - missing_padding)
                                    base64.b64decode(padded_pbk)
                                    node['reality-opts']['public-key'] = padded_pbk
                                else:
                                    raise ValueError("Invalid base64 format")
                        if sid:
                            sid_bytes = bytes.fromhex(sid)
                            if len(sid_bytes) > 8:
                                raise ValueError("short-id too long")
                    except Exception as e:
                        logging.debug(f"Invalid REALITY params in VLESS YAML: {e}")
                        if 'reality-opts' in node:
                            del node['reality-opts']
                if 'client-fingerprint' in node_yaml:
                    node['client-fingerprint'] = node_yaml['client-fingerprint']
                
                return node
        except:
            pass

        # 如果不是YAML格式，按URL格式解析
        parsed_url = urlparse(vless_link)
        params = parse_qs(parsed_url.query)

        # 生成基础名称
        base_name = f"Node-{str(uuid.uuid4())[:8]}"
        # 获取国旗 emoji
        server = parsed_url.hostname.strip()
        emoji = get_country_emoji(server)
        # 组合名称和国旗
        random_name = f"{emoji} {base_name}"
        # 检查必要字段
        if not parsed_url.hostname or not parsed_url.port or not parsed_url.username:
            return None

        node = {
            'type': 'vless',
            'name': random_name,
            'server': parsed_url.hostname.strip(),
            'port': int(parsed_url.port),
            'uuid': parsed_url.username,
        }
        
        # 只有在原始链接中明确提供时才添加这些参数
        if 'security' in params:
            node['tls'] = True if params['security'][0] == 'tls' else False
        if 'type' in params:
            node['network'] = params['type'][0]
        if 'skip-cert-verify' in params:
            node['skip-cert-verify'] = params['skip-cert-verify'][0].lower() == 'true'
            
        # 支持 flow 参数（例如 xtls-rprx-vision）
        flow = params.get('flow', [''])[0]
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

        # 支持 reality (仅限 VLESS 和其他支持的协议)
        security = params.get('security', [''])[0]
        if security == 'reality':
            pbk = params.get('pbk', [''])[0]
            sid = params.get('sid', [''])[0]
            reality_opts = {'public-key': pbk, 'short-id': sid}
            try:
                if pbk:
                    # Fix base64 padding
                    missing_padding = len(pbk) % 4
                    if missing_padding:
                        padded_pbk = pbk + '=' * (4 - missing_padding)
                    else:
                        padded_pbk = pbk
                    # Verify the padded string is valid base64
                    base64.b64decode(padded_pbk)
                    # Use the padded version
                    reality_opts['public-key'] = padded_pbk
                if sid:
                    sid_bytes = bytes.fromhex(sid)
                    if len(sid_bytes) > 8:
                        raise ValueError("short-id too long")
                # Only add reality-opts if both pbk and sid are valid or if at least one is valid
                if (pbk and 'public-key' in reality_opts) or sid:
                    node['reality-opts'] = reality_opts
                    # 处理 fingerprint
                    if 'fp' in params:
                        node['client-fingerprint'] = params['fp'][0]
            except Exception as e:
                logging.debug(f"Invalid REALITY params in VLESS URL: {e}")
                if 'reality-opts' in node:
                    del node['reality-opts']
        return node
    except Exception as e:
        logging.error(f"Error parsing VLESS link: {e}")
        return None

def decode_vmess_link(vmess_link):
    """Parse VMess protocol URL and return Clash-compatible format"""
    try:
        node_data = json.loads(base64.b64decode(vmess_link[8:]).decode())
        # 生成基础名称
        base_name = f"Node-{str(uuid.uuid4())[:8]}"
        # 获取国旗 emoji
        server = node_data.get('add', '').strip()
        emoji = get_country_emoji(server)
        # 组合名称和国旗
        random_name = f"{emoji} {base_name}"
        # 设置默认加密方式为 auto，确保与 Clash 兼容
        cipher = node_data.get('security', 'auto')
        # 如果加密方式为 none，改为 auto
        if cipher == 'none':
            cipher = 'auto'
        # 检查必要字段
        if not node_data.get('add') or not node_data.get('port') or not node_data.get('id'):
            return None
            
        node = {
            'type': 'vmess',
            'name': random_name,
            'server': node_data.get('add', '').strip(),
            'port': int(node_data.get('port', 0)),
            'uuid': node_data.get('id', ''),
            'alterId': int(node_data.get('aid', 0)),
            'cipher': cipher,
        }
        
        # 只有在原始配置中明确提供时才添加这些参数
        if 'tls' in node_data:
            node['tls'] = True if node_data.get('tls') == 'tls' else False
        if 'udp' in node_data:
            node['udp'] = node_data['udp']
        if 'skip-cert-verify' in node_data:
            node['skip-cert-verify'] = node_data['skip-cert-verify']
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
        
        # VMess 不支持 REALITY，如果存在相关参数应当忽略
        # 如果有 reality-opts 或 client-fingerprint 字段，应该移除它们以避免混淆
        
        return node
    except Exception as e:
        logging.error(f"Error parsing VMess link: {e}")
        return None

def decode_ss_link(ss_link):
    """Parse Shadowsocks protocol URL and return Clash-compatible format"""
    try:
        if ss_link.startswith('ss://'):
            ss_link = ss_link[5:]

        # 生成基础名称
        base_name = f"Node-{str(uuid.uuid4())[:8]}"

        method = None
        password = None
        server = None
        port = None

        # 解析URL中的查询参数
        parsed_url = urlparse(ss_link)
        params = parse_qs(parsed_url.query)
        
        # 如果URL中有参数，处理plugin参数
        plugin = None
        if 'plugin' in params:
            plugin = params['plugin'][0]

        # 移除查询参数部分，只保留主要部分
        if '?' in ss_link:
            ss_link = ss_link.split('?', 1)[0]

        # 移除名称部分
        if '#' in ss_link:
            ss_link = ss_link.split('#', 1)[0]

        # 尝试多种解析方法
        parsed = False
        
        # 方法1: 尝试标准SS格式: base64(method:password)@server:port
        if not parsed:
            try:
                # Validate base64 format - must be multiple of 4 or valid with padding
                link_len = len(ss_link)
                if link_len % 4 != 1:  # 只有当长度模4不等于1时才可能是有效base64
                    # Add padding to base64 if needed
                    missing_padding = link_len % 4
                    if missing_padding:
                        padded_link = ss_link + '=' * (4 - missing_padding)
                    else:
                        padded_link = ss_link

                    decoded_bytes = base64.b64decode(padded_link, validate=False)
                    decoded = decoded_bytes.decode('utf-8')
                    
                    if '@' in decoded:
                        method_pass, server_port = decoded.split('@', 1)
                        if ':' in method_pass:
                            method, password = method_pass.split(':', 1)
                        else:
                            raise ValueError("Invalid method:password format")

                        # 安全地分割服务器和端口
                        if ':' in server_port:
                            server, port = server_port.rsplit(':', 1)
                            parsed = True
                        else:
                            raise ValueError("Invalid server:port format")
            except Exception as e:
                logging.debug(f"Method 1 (standard base64) failed: {e}")
        
        # 方法2: 尝试直接解析格式: method:password@server:port
        if not parsed:
            try:
                if '@' in ss_link and ':' in ss_link:
                    method_pass, server_port = ss_link.split('@', 1)
                    if ':' in method_pass:
                        method, password = method_pass.split(':', 1)
                    else:
                        raise ValueError("Invalid method:password format")

                    if ':' in server_port:
                        server, port = server_port.rsplit(':', 1)
                        parsed = True
                    else:
                        raise ValueError("Invalid server:port format")
            except Exception as e:
                logging.debug(f"Method 2 (direct) failed: {e}")
        
        # 方法3: 尝试base64解码method_pass部分: base64(method:password)@server:port
        if not parsed:
            try:
                if '@' in ss_link and ':' in ss_link:
                    parts = ss_link.split('@', 1)
                    if len(parts) == 2:
                        method_pass_b64, server_port = parts
                        
                        # 解码method_pass部分
                        link_len = len(method_pass_b64)
                        missing_padding = link_len % 4
                        if missing_padding:
                            padded_method_pass = method_pass_b64 + '=' * (4 - missing_padding)
                        else:
                            padded_method_pass = method_pass_b64
                            
                        method_pass_bytes = base64.b64decode(padded_method_pass, validate=False)
                        method_pass = method_pass_bytes.decode('utf-8')
                        
                        if ':' in method_pass:
                            method, password = method_pass.split(':', 1)
                        else:
                            raise ValueError("Invalid method:password format")

                        if ':' in server_port:
                            server, port = server_port.rsplit(':', 1)
                            parsed = True
                        else:
                            raise ValueError("Invalid server:port format")
            except Exception as e:
                logging.debug(f"Method 3 (base64 method_pass) failed: {e}")

        # 如果所有方法都失败了
        if not parsed:
            logging.debug(f"Skipping SS link due to parsing failure: {ss_link[:50]}...")
            return None

        # Validate required fields
        if not all([method, password, server, port]):
            logging.warning(f"Skipping SS link due to missing required fields: method={method}, password={password}, server={server}, port={port}")
            return None

        # 修正 cipher 字段，去除可能的 'ss' 前缀
        cipher = method.lower()
        if cipher.startswith('ss') and cipher != 'ssr':
            cipher = cipher.replace('ss', '', 1)
            cipher = cipher.strip('-')

        # 检查 cipher 是否为空
        if not cipher or cipher == '':
            logging.warning(f"SS节点加密方式为空，已丢弃")
            return None

        # 对于2022协议，需要处理密码
        if cipher.startswith('2022'):
            try:
                decoded_key = base64.b64decode(password)
            except:
                try:
                    decoded_key = bytes.fromhex(password)
                except:
                    raise ValueError("Invalid password format for 2022 cipher")
            expected_len = 32 if 'aes-256' in cipher else 16
            if len(decoded_key) != expected_len:
                logging.warning(f"Invalid key length {len(decoded_key)} for {cipher}, expected {expected_len} bytes. Skipping node.")
                return None
            # 重新编码为base64以保持一致性
            password = base64.b64encode(decoded_key).decode()

        if cipher not in supported_ciphers:
            logging.warning(f"SS节点加密方式 {cipher} 不被Clash和sing-box同时支持，已丢弃")
            return None
            
        # 清理端口字符串，移除可能的查询参数和其他干扰字符
        def clean_port(port_str):
            if not port_str:
                return port_str
                
            # 移除查询参数
            if '?' in port_str:
                port_str = port_str.split('?', 1)[0]
                
            # 移除末尾的斜杠
            if port_str.endswith('/'):
                port_str = port_str[:-1]
                
            # 移除其他可能的干扰字符（如路径分隔符等）
            port_str = port_str.strip()
            
            return port_str
        
        port = clean_port(port)
            
        # 添加国旗 emoji
        emoji = get_country_emoji(server)
        
        # 构建返回节点
        node = {
            'type': 'ss',
            'name': f"{emoji} {base_name}",
            'server': server.strip(),
            'port': int(port),
            'cipher': cipher,
            'password': password,
            'udp': True
        }
        
        # 如果有plugin参数，则添加到节点配置中
        if plugin:
            node['plugin'] = plugin
            # 如果plugin有选项，也添加plugin-opts
            if 'plugin-opts' in params:
                # 简化处理，实际应该解析plugin-opts的值
                node['plugin-opts'] = params['plugin-opts'][0]
            
        return node
    except ValueError as e:
        if "invalid literal for int() with base 10" in str(e):
            logging.error(f"Error parsing SS link: Port is not a valid integer. Original error: {e}")
        else:
            logging.error(f"Error parsing SS link: {e}")
        return None
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
        # 获取国旗 emoji
        emoji = get_country_emoji(parsed_url.hostname)
        # 检查必要字段
        if not parsed_url.hostname or not parsed_url.port or not parsed_url.username:
            return None
            
        node = {
            'type': 'trojan',
            'name': f"{emoji} {base_name}",
            'server': parsed_url.hostname.strip(),
            'port': int(parsed_url.port),
            'password': parsed_url.username,
        }
        
        # 只有在原始链接中明确提供时才添加这些参数
        if 'sni' in params:
            node['sni'] = params['sni'][0] or parsed_url.hostname
        if 'skip-cert-verify' in params:
            node['skip-cert-verify'] = params['skip-cert-verify'][0].lower() == 'true'
        if 'udp' in params:
            node['udp'] = params['udp'][0].lower() == 'true'
        if 'type' in params:
            node['network'] = params['type'][0]
        if 'client-fingerprint' in params:
            node['client-fingerprint'] = params['client-fingerprint'][0]
            
        # 处理不同的传输协议
        if node.get('network') == 'ws':
            ws_opts = {}
            if 'path' in params:
                ws_opts['path'] = params['path'][0]
            headers = {}
            if 'host' in params:
                headers['Host'] = params['host'][0]
            for k, v in params.items():
                if k.lower().startswith('header-'):
                    headers[k[7:]] = v[0]
            if headers:
                ws_opts['headers'] = headers
            if ws_opts:
                node['ws-opts'] = ws_opts
        elif node.get('network') == 'grpc':
            grpc_opts = {}
            if 'serviceName' in params:
                grpc_opts['grpc-service-name'] = params['serviceName'][0]
            if grpc_opts:
                node['grpc-opts'] = grpc_opts
        elif node.get('network') == 'http':
            http_opts = {}
            # 只在参数非空时才添加到数组
            if 'path' in params and params['path'][0].strip():
                http_opts['path'] = [params['path'][0].strip()]
            if 'host' in params and params['host'][0].strip():
                http_opts['headers'] = {'Host': [params['host'][0].strip()]}  # Host 需要是一个数组
            # 只有当http_opts有内容时才添加
            if http_opts:
                node['http-opts'] = http_opts
        elif node.get('network') == 'h2':
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
        base_name = f"Node-{str(uuid.uuid4())[:8]}"
        # 获取国旗 emoji
        emoji = get_country_emoji(server)
        # 组合名称和国旗
        random_name = f"{emoji} {base_name}"
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
        base_name = f"Node-{str(uuid.uuid4())[:8]}"
        # 获取国旗 emoji
        emoji = get_country_emoji(parsed_url.hostname)
        # 组合名称和国旗
        random_name = f"{emoji} {base_name}"
        node = {
            'type': 'hysteria2',
            'name': random_name,
            'server': parsed_url.hostname,
            'port': int(parsed_url.port),
            'password': parsed_url.username,
        }
        
        # 只有在原始链接中明确提供时才添加这些参数
        if 'sni' in params:
            node['sni'] = params['sni'][0] or parsed_url.hostname
        if 'insecure' in params:
            node['skip-cert-verify'] = params['insecure'][0] == '1'
        if 'tls' in params:
            node['tls'] = params['tls'][0].lower() == 'true'
        if 'hop' in params:
            node['hop-interval'] = int(params['hop'][0])
        
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

def get_country_emoji(ip_address):
    try:
        ip_address = socket.gethostbyname(ip_address)
        # 查询 IP 地址的地理位置信息
        response = reader.country(ip_address)
        # 获取国家代码
        country_code = response.country.iso_code
        # 将国家代码转换为 emoji
        if country_code:
            # 国家代码转换为 emoji
            emoji = chr(ord(country_code[0]) + 127397) + chr(ord(country_code[1]) + 127397)
            logging.debug(f"{ip_address} emoji is {emoji}")
            return emoji
        else:
            logging.debug(f"{ip_address} emoji is None")
            return "🌍"
    except Exception as e:
        #logging.error(f"Error getting country emoji for {ip_address}: {e}")
        return "🌍"

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
                logging.info('检测到 YAML 格式，为节点添加国旗')
                proxies = yaml_obj['proxies']
                # 为每个节点添加国旗
                for proxy in proxies:
                    if 'server' in proxy and 'name' in proxy:
                        emoji = get_country_emoji(proxy['server'])
                        proxy['name'] = f"{emoji} {proxy['name']}"
                return proxies
        except Exception as e:
            pass

        # 否则按原有方式逐行解析
        nodes = []
        for line in decoded_content.splitlines():
            line = line.strip()
            if line.startswith(('vmess://', 'vless://', 'hysteria2://', 'ss://', 'trojan://')):
                # Convert the node to Clash format
                try:
                    if line.startswith('vmess://'):
                        node = decode_vmess_link(line)
                        if node:
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
                    # elif line.startswith('ssr://'):
                    #     node = decode_ssr_link(line)
                    #     if node:
                    #         nodes.append(node)
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
        nodes = decode_url_to_nodes(url = "https://raw.githubusercontent.com/mheidari98/.proxy/refs/heads/main/all")
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
