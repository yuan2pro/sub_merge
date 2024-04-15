#!/usr/bin/env python3

import logging
import random
import re
import threading
import urllib.parse

import requests
import yaml
from ping3 import ping
from requests.adapters import HTTPAdapter

# 配置日志记录器
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

url_file = "./sub/url.txt"
server_host = 'http://127.0.0.1:25500'
# server_host = 'http://192.168.100.1:25500'
# config_url = 'https://raw.githubusercontent.com/zzcabc/Rules/master/MyConvert/MyRules.ini'

include = ".*香港.*|.*HK.*|.*Hong Kong.*|.*🇭🇰.*"
exclude = ".*测速.*|.*禁止.*|.*过期.*|.*剩余.*|.*CN.*|.*备用.*|:"

reg = 'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'

exce_url = ['1.1.1.1', '8.8.8.8', '0.0.0.0',
            '127.0.0.1', '127.0.0.2', 'google.com', 'localhost', 'github.com']

with open(url_file, 'r', encoding='utf-8') as f:  # 载入订阅链接
    urls = f.read()
    f.close()

url_list = urls.split("|")
# 打乱顺序
# random.shuffle(url_list)
step = 20
index = 0
length = len(url_list)

thread_num = length // step + 1
lock = threading.Lock()


# lock1 = threading.Lock()


def run(index):
    # print(threading.current_thread().getName(), "开始工作")
    # for i in range(0, length, step):
    yaml_file = "./sub/" + str(index) + ".yaml"
    cur = index * step
    i = (index + 1) * step
    url_lists = []
    if i >= length:
        url_lists = url_list[cur:length]
    else:
        url_lists = url_list[cur:i]
    not_proxies = []
    new_proxies = []
    node_list = {}
    for url in url_lists:
        # print(url)
        url_quote = urllib.parse.quote(url, safe='')
        # config_quote = urllib.parse.quote(config_url, safe='')
        # include_quote = urllib.parse.quote(include, safe='')
        exclude_quote = urllib.parse.quote(exclude, safe='')
        # 转换并获取订阅链接数据
        converted_url = server_host + '/sub?target=clash&url=' + url_quote + \
                        '&emoji=true&sort=true&fdn=true&list=true&exclude=' + \
                        exclude_quote
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
                logging.error("%s error:%s", url, str(err))
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
                try:
                    proxies = yaml_text['proxies']
                    logging.info("%s Number of nodes:%d", url, len(proxies))
                    for proxie in proxies:
                        server = proxie['server']
                        # TLS must be true with h2/ grpc network
                        if "network" in proxie.keys() and "tls" in proxie.keys():
                            network = proxie['network']
                            tls = proxie['tls']
                            if network == "h2" or network == "grpc":
                                if tls is False:
                                    # proxies.remove(proxie)
                                    not_proxies.append(proxie)
                                    continue
                        if "cipher" in proxie.keys() and proxie['cipher'] == "chacha20-poly1305":
                            not_proxies.append(proxie)
                            continue
                        if server in exce_url:
                            # proxies.remove(proxie)
                            not_proxies.append(proxie)
                            continue
                        if server.startswith("127") or server.startswith("192") or server.startswith("10."):
                            not_proxies.append(proxie)
                            continue
                        if "uuid" in proxie.keys() and len(proxie['uuid']) != 36:
                            not_proxies.append(proxie)
                            continue
                        # try:
                        #     # verbose_ping(server, count=1)
                        #     ping_res = ping(server, unit='ms')
                        #     # exce_url.append(server)
                        #     if not ping_res:
                        #         # proxies.remove(proxie)
                        #         not_proxies.append(proxie)
                        #         continue
                        # except Exception as e:
                        #     logging.error("error: {}", str(e))
                        #     # proxies.remove(proxie)
                        #     not_proxies.append(proxie)
                        #     continue
                        # finally:
                        #     lock1.release()
                        new_proxies.append(proxie)
                    # lock1.acquire()

                    # lock1.release()
                except Exception:
                    logging.error("%s proxie error", url)
                    continue
        except Exception:
            # 链接有问题，直接返回原始错误
            logging.error("%s url error", url)
            continue
        # finally:
        # lock.release()
        continue
    try:
        lock.acquire()
        if new_proxies is not None:
            with open(yaml_file, "w", encoding="utf-8") as f:
                logging.info("%d Number of nodes after filtering:%d", index, len(new_proxies))
                logging.info("%d Number of discarded nodes:%d", index, len(not_proxies))
                node_list['proxies'] = new_proxies
                f.write(yaml.dump(node_list))
        else:
            logging.error("%d is empty", index)
    except Exception as e:
        # 链接有问题，直接返回原始错误
        logging.error("%d ERROR %s", index, e.args[0])
    finally:
        lock.release()


thread_list = []
for i in range(thread_num):
    t = threading.Thread(target=run, args=(i,))
    thread_list.append(t)
    # t.setDaemon(True)   # 把子线程设置为守护线程，必须在start()之前设置
    t.start()
logging.info("%d个线程已启动", threading.active_count() - 1)
for thread in thread_list:
    thread.join()
logging.info("all thread finished")
