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
    all_url = "|".join(enabled_list)
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
        # 对url进行ASCII编码
        # # 切换代理
        # if "github" in url:
        #     url = url.replace("githubusercontent.com","fastgit.org")
        url_quote = urllib.parse.quote(url.strip(), safe='')
        # 转换并获取订阅链接数据
        converted_url = server_host + '/sub?target=clash&url=' + \
                        url_quote + '&list=true&fdn=true&emoji=true'
        try:
            s = requests.Session()
            s.mount('http://', HTTPAdapter(max_retries=5))
            s.mount('https://', HTTPAdapter(max_retries=5))
            resp = s.get(converted_url, timeout=30)
            # 如果解析出错，将原始链接内容拷贝下来
            text = resp.text
            if 'No nodes were found!' in text:
                logging.info(url + " No nodes were found!")
                continue
            # 如果是包含chacha20-poly1305跳过
            if 'chacha20-poly1305' in text:
                logging.info(url + " chacha20-poly1305!")
                continue
            if '#' in text:
                logging.info(url + " #")
                continue
            if 'The following link' in text:
                logging.info(url + " The following link")
                continue
            # 检测节点乱码
            try:
                text.encode('utf-8')
                yaml.safe_load(text)
            except Exception as e:
                logging.error("url:{}, error:{}", url, str(e))
                continue
            avaliable_url.append(url)
        except Exception as err:
            # 链接有问题，直接返回原始错误
            logging.error('网络错误，检查订阅转换服务器是否失效:' + '\n' + converted_url + '\n' +
                  str(err))
            continue
    return avaliable_url


class update_url():

    def update_main(update_enable_list=[0, 7, 25, 43, 54, 57]):
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
            # remarks: pojiezhiyuanjun/freev2, 将原链接更新至 https://raw.fastgit.org/pojiezhiyuanjun/freev2/master/%MM%(DD - 1).txt
            # today = datetime.today().strftime('%m%d')
            # 得到当前日期前一天 https://blog.csdn.net/wanghuafengc/article/details/42458721
            yesterday = (datetime.today() + timedelta(-1)).strftime('%m%d')
            front_url = 'https://raw.githubusercontent.com/pojiezhiyuanjun/freev2/master/'
            end_url = 'clash.yml'
            # 修改字符串中的某一位字符 https://www.zhihu.com/question/31800070/answer/53345749
            url_update = front_url + yesterday + end_url
            if check_url(url_update):
                return [0, url_update]
            else:
                return [0, 404]
            
        elif id == 7:
            # remarks: https://freenode.openrunner.net/
            # today = datetime.today().strftime('%m%d')
            # 得到当天一天 https://freenode.openrunner.net/uploads/20231123-clash.yaml
            today = datetime.today().strftime('%Y%m%d')
            front_url = 'https://freenode.openrunner.net/uploads/today-clash.yaml'
            url_update = front_url.replace("today", today)
            if check_url(url_update):
                return [0, url_update]
            else:
                return [0, 404]

        elif id == 43:
            # remarks: v2raydy/v2ray, 将原链接更新至 https://https://raw.githubusercontent.com/v2raydy/v2ray/main/%MM-%(DD - 1)%str%1.txt
            # 得到当前日期前一天 https://blog.csdn.net/wanghuafengc/article/details/42458721
            # https://nodefree.org/dy/2023/02/20230205.yaml
            today = datetime.today().strftime('%Y%m%d')
            year = datetime.today().strftime('%Y') + '/'
            month = datetime.today().strftime('%m') + '/'
            front_url = 'https://nodefree.org/dy/'
            end_url = '.yaml'
            url_update = front_url + year + month + today + end_url
            if check_url(url_update):
                return [43, url_update]
            else:
                return [43, 404]

        elif id == 25:
            today = datetime.today().strftime('%Y%m%d')
            month = datetime.today().strftime('%m') + '/'
            year = datetime.today().strftime('%Y') + '/'
            front_url = 'https://v2rayshare.com/wp-content/uploads/'
            end_url = '.yaml'
            url_update = front_url + year + month + today + end_url
            if check_url(url_update):
                return [25, url_update]
            else:
                return [25, 404]

        elif id == 54:
            url_raw = [
                "https://raw.githubusercontent.com/RenaLio/Mux2sub/main/urllist",
                "https://raw.githubusercontent.com/RenaLio/Mux2sub/main/sub_list"
            ]
            url_update_array = []
            try:
                for url in url_raw:
                    resp = requests.get(url, timeout=3)
                    resp_content = resp.content.decode('utf-8')
                    resp_content = resp_content.split('\n')
                    for line in resp_content:
                        if 'http' in line:
                            url_update_array.append(line)
                        else:
                            continue
                url_update = '|'.join(url_update_array)
                return [54, url_update]
            except Exception as err:
                logging.error(str(err))
                return [54, 404]

        elif id == 57:
            today = datetime.today().strftime('%Y%m%d')
            month = datetime.today().strftime('%m') + '/'
            year = datetime.today().strftime('%Y') + '/'
            front_url = 'https://clashnode.com/wp-content/uploads/'
            end_url = '.txt'
            url_update = front_url + year + month + today + end_url
            if check_url(url_update):
                return [57, url_update]
            else:
                return [57, 404]
            
        # elif id == 75:
        #     url_raw = 'https://raw.githubusercontent.com/RiverFlowsInUUU/collectSub/main/sub/' + \
        #               str(datetime.today().year) + '/' + str(datetime.today().month) + '/' + \
        #               str(datetime.today().month) + '-' + \
        #               str(datetime.today().day) + '.yaml'
        #     if check_url(url_raw):
        #         try:
        #             resp = requests.get(url_raw, timeout=2)
        #             resp_content = resp.content.decode('utf-8')
        #             resp_content = resp_content.split('\n')
        #             url_update_array = []
        #             for line in resp_content:
        #                 if '- ' in line:
        #                     line = line.replace("- ", "")
        #                     url_update_array.append(line)
        #             url_update = '|'.join(url_update_array)
        #             return [75, url_update]
        #         except Exception as err:
        #             print(str(err))
        #             return [75, 404]
        #     else:
        #         return [75, 404]


if __name__ == '__main__':
    update_url.update_main()
    write_url()
