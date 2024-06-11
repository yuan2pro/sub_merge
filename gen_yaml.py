#!/usr/bin/env python3

import logging
import multiprocessing
import random
import socket
import threading
import urllib.parse

import emoji
import geoip2.database
import requests
import yaml
from requests.adapters import HTTPAdapter

# 载入 MaxMind 提供的数据库文件
reader = geoip2.database.Reader('GeoLite2-Country.mmdb')

# 配置日志记录器
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(lineno)d - %(message)s')

url_file = "./sub/url.txt"
server_host = 'http://127.0.0.1:25500'
# server_host = 'http://192.168.100.1:25500'
# config_url = 'https://raw.githubusercontent.com/zzcabc/Rules/master/MyConvert/MyRules.ini'

include = ".*香港.*|.*HK.*|.*Hong Kong.*|.*🇭🇰.*"
exclude = ".*测速.*|.*禁止.*|.*过期.*|.*剩余.*|.*CN.*|.*备用.*|:"

exce_url = ['1.1.1.1', '8.8.8.8', '0.0.0.0',
            '127.0.0.1', '127.0.0.2', 'google.com', 'localhost', 'github.com']

with open(url_file, 'r', encoding='utf-8') as f:  # 载入订阅链接
    urls = f.read()
    f.close()

url_list = urls.split("|")
# 打乱顺序
random.shuffle(url_list)
step = 20
index = 0
length = len(url_list)

thread_num = length // step + 1


# lock = threading.Lock()


def has_emoji(text):
    return emoji.emoji_count(text) != 0


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
        logging.error(f"{e}")


def test_connection(ip, port):
    # 创建 socket 对象
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # 设置超时时间为 2 秒
        sock.settimeout(2)
        # 尝试连接到指定的 IP 地址和端口
        result = sock.connect_ex((socket.gethostbyname(ip), port))
        if result == 0:
            return True
        else:
            return False
    except socket.error as e:
        logging.error(f"{ip}, {e}")
    finally:
        sock.close()


def run(index, shared_list):
    # print(threading.current_thread().getName(), "开始工作")
    # for i in range(0, length, step):
    yaml_file = "./sub/" + str(index) + ".yaml"
    cur = index * step
    i = (index + 1) * step
    if i >= length:
        url_lists = url_list[cur:length]
    else:
        url_lists = url_list[cur:i]
    not_proxies = set()
    new_proxies = []
    servers = set()
    node_list = {}
    node_name = set()
    for url in url_lists:
        url_quote = urllib.parse.quote(url, safe='')
        # config_quote = urllib.parse.quote(config_url, safe='')
        # include_quote = urllib.parse.quote(include, safe='')
        exclude_quote = urllib.parse.quote(exclude, safe='')
        # 转换并获取订阅链接数据
        converted_url = server_host + '/sub?target=clash&url=' + url_quote + \
                        '&emoji=true&list=true&tfo=true&scv=true&fdn=true&sort=false&new_name=true'
        try:
            # lock.acquire()
            s = requests.Session()
            s.mount('http://', HTTPAdapter(max_retries=5))
            s.mount('https://', HTTPAdapter(max_retries=5))
            resp = s.get(converted_url, timeout=30)
            # 如果解析出错，将原始链接内容拷贝下来
            text = resp.text
            try:
                text.encode('utf-8')
                yaml_text = yaml.safe_load(text)
            except Exception as err:
                logging.error(f"{url} {err.args[0]}")
                continue
            if 'No nodes were found!' in text:
                logging.error("%s No nodes were found!", url)
                continue
            if 'The following link' in text:
                logging.error("%s The following link!", url)
                continue
            if '414 Request-URI Too Large' in text:
                logging.error("%s 414 Request-URI Too Large!", url)
                continue
            if yaml_text is None:
                logging.error("%s is None!", url)
                continue
            if yaml_text is not None and 'proxies' in yaml_text.keys():
                proxies = yaml_text['proxies']
                logging.info(f"{url}    {len(proxies)}")
                random.shuffle(proxies)
                for proxie in proxies:
                    try:
                        server = proxie['server']
                        port = proxie['port']
                        sp = str(server) + ":" + str(port)
                        if not test_connection(server, port):
                            servers.add(sp)
                            not_proxies.add(proxie['server'])
                            continue
                        if sp in servers:
                            not_proxies.add(proxie['server'])
                            continue
                        else:
                            servers.add(sp)
                        name = proxie['name']
                        if name not in node_name:
                            node_name.add(name)
                        else:
                            name = name + str(len(node_name))
                            proxie['name'] = name
                        # TLS must be true with h2/ grpc network
                        if "network" in proxie.keys() and "tls" in proxie.keys():
                            network = proxie['network']
                            tls = proxie['tls']
                            if network == "h2" or network == "grpc":
                                if tls is False:
                                    not_proxies.add(proxie['server'])
                                    continue
                        if "cipher" in proxie.keys() and proxie['cipher'] == "chacha20-poly1305":
                            not_proxies.add(proxie['server'])
                            continue
                        if server in exce_url:
                            not_proxies.add(proxie['server'])
                            continue
                        if server.startswith("127") or server.startswith("192") or server.startswith("10."):
                            not_proxies.add(proxie['server'])
                            continue
                        if "uuid" in proxie.keys() and len(proxie['uuid']) != 36:
                            not_proxies.add(proxie['server'])
                            continue
                        # add name emoji
                        if not has_emoji(name):
                            c_emoji = get_country_emoji(server)
                            if c_emoji is not None:
                                proxie['name'] = name + str(c_emoji)
                            else:
                                not_proxies.add(proxie['server'])
                                continue
                        new_proxies.append(proxie)
                    except Exception as e:
                        not_proxies.add(proxie['server'])
                        logging.error(f"proxie:{proxie} error:{e.args[0]}")
                        continue
        except Exception as err:
            # 链接有问题，直接返回原始错误
            logging.error(f"url:{url}  error:{err.args[0]}")
            continue
        # finally:
        # lock.release()
        continue
    try:
        # lock.acquire()
        if new_proxies is not None:
            shared_list.extend(new_proxies)
            logging.info("%d Number of nodes after filtering:%d", index, len(new_proxies))
            logging.info("%d Number of discarded nodes:%d", index, len(not_proxies))
            # with open(yaml_file, "w", encoding="utf-8") as f:
            #     node_list['proxies'] = new_proxies
            #     # f.write(yaml.dump(node_list))
            #     yaml.safe_dump(node_list, f, allow_unicode=True)
        else:
            logging.error("%d is empty", index)
    except Exception as e:
        # 链接有问题，直接返回原始错误
        logging.error("%d ERROR %s", index, e.args[0])
    # finally:
    #     lock.release()


def split_node(n, shared_list):
    node_list = {}
    yaml_file = "./sub/" + str(n) + ".yaml"
    name_list = []
    for list in shared_list:
        name = list['name']
        if name not in name_list:
            name_list.append(name)
        else:
            name = name + str(len(name_list))
            list['name'] = name
    with open(yaml_file, "w", encoding="utf-8") as f:
        node_list['proxies'] = shared_list
        yaml.safe_dump(node_list, f, allow_unicode=True)


if __name__ == '__main__':
    # 创建多个进程
    processes = []
    manager = multiprocessing.Manager()
    shared_list = manager.list()
    for i in range(thread_num):
        p = multiprocessing.Process(target=run, args=(i, shared_list,))
        processes.append(p)
        p.start()
    logging.info("多进程已启动")
    # 等待所有进程结束
    for p in processes:
        p.join()
    random.shuffle(shared_list)
    each_num = 512
    thread_list = []
    t_num = len(shared_list) // each_num + 1
    for i in range(t_num):
        if (i + 1) * each_num <= len(shared_list):
            t = threading.Thread(target=split_node, args=(i, shared_list[i * each_num:i * each_num + each_num]))
        else:
            t = threading.Thread(target=split_node, args=(i, shared_list[i * each_num:]))
        thread_list.append(t)
        t.start()
    logging.info("%d threads actived", threading.active_count() - 1)
    for thread in thread_list:
        thread.join()
    logging.info("All processes have finished.")