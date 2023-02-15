#!/usr/bin/env python3

import random
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
import yaml
from ping3 import ping, verbose_ping
from requests.adapters import HTTPAdapter

url_file = "./sub/url.txt"
server_host = 'http://127.0.0.1:25500'
# server_host = 'http://192.168.100.1:25500'
config_url = 'https://raw.githubusercontent.com/zzcabc/Rules/master/MyConvert/MyRules.ini'

include = ".*香港.*|.*HK.*|.*Hong Kong.*|.*🇭🇰.*"
exclude = ".*测速.*|.*禁止.*|.*过期.*|.*剩余.*|.*CN.*|.*备用.*"

reg = 'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'

exce_url = {'1.1.1.1', '8.8.8.8', '0.0.0.0', '127.0.0.1', 'google.com'}
use_url = set()

with open(url_file, 'r', encoding='utf-8') as f:  # 载入订阅链接
    urls = f.read()
    f.close()

url_list = urls.split("|")
# 打乱顺序
# random.shuffle(url_list)
step = 30
index = 0
length = len(url_list)
error_text = []

thread_num = length // step + 1
lock = threading.Lock()


def run(index):
    # print(threading.current_thread().getName(), "开始工作")
    # for i in range(0, length, step):
    yaml_file = "./sub/"+str(index)+".yaml"
    cur = index * step
    i = (index+1)*step
    # print(cur, i, length)
    if i >= length:
        url = "|".join(url_list[cur:length])
    else:
        url = "|".join(url_list[cur:i])
    while True:
        # print(url)
        url_quote = urllib.parse.quote(url, safe='')
        # config_quote = urllib.parse.quote(config_url, safe='')
        # include_quote = urllib.parse.quote(include, safe='')
        exclude_quote = urllib.parse.quote(exclude, safe='')
        # 转换并获取订阅链接数据
        converted_url = server_host + '/sub?target=clash&url=' + url_quote + \
            '&emoji=true&sort=true&fdn=true&list=true&exclude=' + \
            exclude_quote
        # print(converted_url)
        lock.acquire()
        try:
            s = requests.Session()
            s.mount('http://', HTTPAdapter(max_retries=5))
            s.mount('https://', HTTPAdapter(max_retries=5))
            resp = s.get(converted_url, timeout=30)
            # 如果解析出错，将原始链接内容拷贝下来
            text = resp.text
            try:
                text.encode('utf-8')
                yaml_text = yaml.full_load(text)
            except Exception as e:
                print(str(index)+" " + e)
                break
            if 'No nodes were found!' in text:
                print(url + " No nodes were found!")
                break
            if 'The following link' in text:
                error_text.append(text)
                err_urls = re.findall(reg, text)
                for err in err_urls:
                    url = url.replace(err, "")
                continue
            if '414 Request-URI Too Large' in text:
                print(url, '414 Request-URI Too Large')
                break
        except Exception as err:
            # 链接有问题，直接返回原始错误
            print(str(index)+' 错误' + '\n')
            break
        finally:
            lock.release()
        if yaml_text is not None:
            try:
                proxies = yaml_text['proxies']
                for proxie in proxies:
                    server = proxie['server']
                    if server in exce_url:
                        proxies.remove(proxie)
                        continue
                    if server in use_url:
                        continue
                    try:
                        verbose_ping(server, count=1)
                        ping_res = ping(server, unit='ms')
                        if not ping_res:
                            exce_url.add(server)
                            proxies.remove(proxie)
                        else:
                            use_url.add(server)
                    except Exception:
                        exce_url.add(server)
                        proxies.remove(proxie)
                        continue
                yaml_text['proxies'] = proxies
                with open(yaml_file, "w", encoding="utf-8") as f:
                    f.write(yaml.dump(yaml_text))
            except Exception as e:
                print(e)
        break

    # print(threading.current_thread().getName(), "✅")


thread_list = []
for i in range(thread_num):
    t = threading.Thread(target=run, args=(i,))
    thread_list.append(t)
    # t.setDaemon(True)   # 把子线程设置为守护线程，必须在start()之前设置
    t.start()
print(threading.active_count(), "个线程已启动")
for thread in thread_list:
    thread.join()
print("all thread finished")


error = open("./sub/error.txt", 'w', encoding='utf-8')
error.write("\n".join(error_text))
error.close()
# with open("./sub/exce_url.txt", 'w', encoding='utf-8') as f:
#     f.write("\n".join(exce_url))
# with open("./sub/use_url.txt", 'w', encoding='utf-8') as f:
#     f.write("\n".join(use_url))
