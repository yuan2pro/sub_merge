#!/usr/bin/env python3

import json
import logging
import urllib.parse
from datetime import datetime, timedelta

import requests
import yaml
from requests.adapters import HTTPAdapter

# 配置日志记录器
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 文件路径定义
sub_list_json = './sub_list.json'
url_file = "./sub/url.txt"

with open(sub_list_json, 'r', encoding='utf-8') as f:  # 载入订阅链接
    raw_list = json.load(f)
    f.close()


def check_url(url):  # 判断远程远程链接是否已经更新
    s = requests.Session()
    s.mount('http://', HTTPAdapter(max_retries=2))
    s.mount('https://', HTTPAdapter(max_retries=2))
    # url = url.replace("githubusercontent.com", "fastgit.org")
    try:
        resp = s.get(url, timeout=2)
        status = resp.status_code
    except Exception:
        status = 404
    if status == 200 and len(get_node_from_sub(url)) > 0:
        isAccessable = True
    else:
        isAccessable = False
    return isAccessable


def write_url():
    enabled_list = []
    false_list = []
    for index in range(len(raw_list)):
        urls = raw_list[index]['url']
        url_list = get_node_from_sub(url_raw=urls)
        if len(url_list) > 0:
            raw_list[index]['enabled'] = True
            enabled_list.extend(url_list)
        else:
            raw_list[index]['enabled'] = False
        if not raw_list[index]['enabled']:
            false_list.append(str(raw_list[index]['id']))
    all_url = "|".join(list(set(enabled_list)))
    file = open(url_file, 'w', encoding='utf-8')
    file.write(all_url)
    file.close()

    updated_list = json.dumps(raw_list,
                              sort_keys=False,
                              indent=2,
                              ensure_ascii=False)
    file = open(sub_list_json, 'w', encoding='utf-8')
    file.write(updated_list)
    file.close()


def get_node_from_sub(url_raw='', server_host='http://127.0.0.1:25500'):
    # 使用远程订阅转换服务
    # server_host = 'https://sub.xeton.dev'
    # 使用本地订阅转换服务
    # 分割订阅链接
    urls = url_raw.split('|')
    avaliable_url = []
    for url in urls:
        if not url.startswith("http"):
            continue
        # 对url进行ASCII编码
        # # 切换代理
        # if "github" in url:
        #     url = url.replace("githubusercontent.com","fastgit.org")
        url_quote = urllib.parse.quote(url.strip(), safe='')
        # 转换并获取订阅链接数据
        converted_url = server_host + '/sub?target=clash&url=' + \
                        url_quote + '&emoji=true&list=true&tfo=false&scv=true&fdn=true&sort=false&new_name=true'
        try:
            s = requests.Session()
            s.mount('http://', HTTPAdapter(max_retries=5))
            s.mount('https://', HTTPAdapter(max_retries=5))
            resp = s.get(converted_url, timeout=30)
            # 如果解析出错，将原始链接内容拷贝下来
            text = resp.text
            if 'No nodes were found!' in text:
                logging.info(f"{url} No nodes were found!")
                continue
            # 如果是包含chacha20-poly1305跳过
            # if 'chacha20-poly1305' in text:
            #     logging.info(url + " chacha20-poly1305!")
            #     continue
            # if '#' in text:
            #     logging.info(url + " #")
            #     continue
            if 'The following link' in text:
                logging.error(f"{url} The following link")
                continue
            # 检测节点乱码
            # try:
            #     text.encode('utf-8')
            #     yaml.safe_load(text)
            # except Exception as e:
            #     logging.error(f"url:{url} error:{e.args[0]}")
            #     continue
            avaliable_url.append(url)
        except Exception as err:
            # 链接有问题，直接返回原始错误
            logging.error(f"{url} error:{err.args[0]}")
            continue
    return avaliable_url


class update_url():

    def update_main(update_enable_list=[0, 25, 43]):
        if len(update_enable_list) > 0:
            for id in update_enable_list:
                status = update_url.update(id)
                update_url.update_write(id, status[1], status[1])
        else:
            logging.info('Don\'t need to be updated.')

    def update_write(id, status, updated_url):
        nid = id
        for index in range(len(raw_list)):
            if raw_list[index]['id'] == id:
                nid = index
                break
        if status == 404:
            raw_list[nid]['enabled'] = False
            logging.info(f'Id {id} URL NOT FOUND')
        else:
            raw_list[nid]['enabled'] = True
            if updated_url != raw_list[nid]['url']:
                raw_list[nid]['url'] = updated_url
                logging.info(f'Id {id} URL 更新至 : {updated_url}')
            else:
                logging.info(f'Id {id} URL 无可用更新')

    def update(id):
        if id == 0:
            url_raw = ["https://raw.githubusercontent.com/snakem982/proxypool/main/source/nodelist.txt",
                       "https://raw.githubusercontent.com/snakem982/proxypool/main/source/proxies.txt",
                       "https://raw.githubusercontent.com/LalatinaHub/Mineral/master/result/subs"]
            url_array = []
            try:
                for url in url_raw:
                    response = requests.get(url, timeout=2)
                    response.raise_for_status()  # 检查是否下载成功
                    # 将每行的URL以|分割，并连接起来
                    url_lines = response.text.split('\n')
                    url_array.extend(url_lines)
                url_update = '|'.join(url_array)
                return [id, url_update]
            except Exception as err:
                logging.error(f"{err.args[0]}")
                return [id, 404]


if __name__ == '__main__':
    update_url.update_main()
    write_url()
