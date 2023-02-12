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
# server_host = 'https://sub.xeton.dev'
config_url = 'https://raw.githubusercontent.com/zzcabc/Rules/master/MyConvert/MyRules.ini'

include = ".*香港.*|.*HK.*|.*Hong Kong.*|.*🇭🇰.*"
exclude = ".*测速.*|.*禁止.*|.*过期.*|.*剩余.*"

reg = 'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'

with open(url_file, 'r', encoding='utf-8') as f:  # 载入订阅链接
    urls = f.read()
    f.close()

url_list = urls.split("|")
# 打乱顺序
random.shuffle(url_list)
step = 15
index = 0
length = len(url_list)
error_text = []

thread_num = length // step + 1


def pings(ips):
    # ips为可迭代对象,每个元素为一个IP地址或域名
    # 返回值为一个字典,key保存ip,value保存是否能ping通
    ips_status = dict()
    # 多线程执行ping函数
    with ThreadPoolExecutor(max_workers=500) as pool:
        results = pool.map(ping, ips)
    for index, result in enumerate(results):
        ip = ips[index]
        if type(result) == float:
            ips_status[ip] = True
        else:
            ips_status[ip] = False
    return ips_status


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
        config_quote = urllib.parse.quote(config_url, safe='')
        include_quote = urllib.parse.quote(include, safe='')
        exclude_quote = urllib.parse.quote(exclude, safe='')
        # 转换并获取订阅链接数据
        converted_url = server_host + '/sub?target=clash&url=' + url_quote + \
            '&emoji=true&sort=true&fdn=true&exclude=' + \
            exclude_quote
        # print(converted_url)
        try:
            s = requests.Session()
            s.mount('http://', HTTPAdapter(max_retries=5))
            s.mount('https://', HTTPAdapter(max_retries=5))
            resp = s.get(converted_url, timeout=30)
            # 如果解析出错，将原始链接内容拷贝下来
            text = resp.text
            try:
                text.encode('utf-8')
                pingtext = yaml.full_load(text)
            except UnicodeEncodeError:
                print(str(index)+"字符error")
                break
            if 'No nodes were found!' in text:
                print(url + " No nodes were found!")
                break
            if 'The following link' in text:
                # 通过with语句使用线程锁
                with error_text:
                    error_text.append(text)
                err_urls = re.findall(reg, text)
                for err in err_urls:
                    url = url.replace(err, "")
                continue
            if '414 Request-URI Too Large' in text:
                print(url, '414 Request-URI Too Large')
                break
            if pingtext is None:
                proxies = pingtext['proxies']
                servers = []
                for proxie in proxies:
                    server = proxie['server']
                    servers.append(server)
                ping_res = pings(servers)
                with error_text:
                    error_text.append(ping_res+'\n')
            clash_file = open(yaml_file, 'w', encoding='utf-8')
            clash_file.write(text)
            clash_file.close()
            # index = index+1
            break
        except Exception as err:
            # 链接有问题，直接返回原始错误
            print('网络错误，检查订阅转换服务器是否失效:' + '\n' +
                  converted_url)
            break
    # print(threading.current_thread().getName(), "✅")


thread_list = []
for i in range(thread_num):
    t = threading.Thread(target=run, args=(i,))
    thread_list.append(t)
    # t.setDaemon(True)   # 把子线程设置为守护线程，必须在start()之前设置
    t.start()
for thread in thread_list:
    thread.join()
print("all thread finished")
print(threading.active_count(), "个线程已启动")


error = open("./sub/error.txt", 'w', encoding='utf-8')
error.write("\n".join(error_text))
error.close()
