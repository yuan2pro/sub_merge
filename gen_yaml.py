#!/usr/bin/env python3

import random
import re
import urllib.parse

import requests
from requests.adapters import HTTPAdapter

url_file = "./sub/url.txt"
server_host = 'http://127.0.0.1:25500'
# server_host = 'https://sub.xeton.dev'
config_url = 'https://raw.githubusercontent.com/cutethotw/ClashRule/main/GeneralClashRule.ini'

include = ".*香港.*|.*HK.*|.*Hong Kong.*|.*🇭🇰.*"
exclude = ".*测速.*"

reg = 'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'

with open(url_file, 'r', encoding='utf-8') as f:  # 载入订阅链接
    urls = f.read()
    f.close()

url_list = urls.split("|")
# 打乱顺序
random.shuffle(url_list)
step = 30
index = 0
length = len(url_list)
error_text = []
for i in range(0, length, step):
    yaml_file = "./sub/"+str(index)+".yaml"
    if i+step >= length:
        url = "|".join(url_list[i:length])
    else:
        url = "|".join(url_list[i:i+step])
    while True:
        # print(url)
        url_quote = urllib.parse.quote(url, safe='')
        config_quote = urllib.parse.quote(config_url, safe='')
        include_quote = urllib.parse.quote(include, safe='')
        exclude_quote = urllib.parse.quote(exclude, safe='')
        # 转换并获取订阅链接数据
        converted_url = server_host + '/sub?target=clash&url=' + url_quote + \
            '&emoji=true&sort=true&list=true&exclude='+exclude_quote
        try:
            s = requests.Session()
            s.mount('http://', HTTPAdapter(max_retries=5))
            s.mount('https://', HTTPAdapter(max_retries=5))
            resp = s.get(converted_url, timeout=30)
            # 如果解析出错，将原始链接内容拷贝下来
            text = resp.text
            # print(text)
            try:
                text.encode('utf-8')
            except UnicodeEncodeError:
                print(str(index)+"字符error")
                break
            if 'No nodes were found!' in text:
                print(url + " No nodes were found!")
                error_text.append(text)
                break
            if 'The following link' in text:
                error_text.append(text)
                err_urls = re.findall(reg, text)
                for err in err_urls:
                    url = url.replace(err, "")
                continue
            if '414 Request-URI Too Large' in text:
                error_text.append(text)
                break
            clash_file = open(yaml_file, 'w', encoding='utf-8')
            clash_file.write(text)
            clash_file.close()
            index = index+1
            break
        except Exception as err:
            # 链接有问题，直接返回原始错误
            print('网络错误，检查订阅转换服务器是否失效:' + '\n' +
                  converted_url)
            break
error = open("./sub/error.txt", 'w', encoding='utf-8')
error.write("\n".join(error_text))
error.close()
