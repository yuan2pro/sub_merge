import os
import yaml

def merge_proxies(directory, output_file):
    all_proxies = []

    # 遍历目录下的所有 .yaml 文件
    for filename in os.listdir(directory):
        if filename.endswith('.yaml'):
            filepath = os.path.join(directory, filename)
            with open(filepath, 'r', encoding='utf-8') as file:
                data = yaml.safe_load(file)
                if 'proxies' in data:
                    all_proxies.extend(data['proxies'])
            os.remove(filepath)

    # 生成新的 .yaml 文件
    with open(output_file, 'w', encoding='utf-8') as file:
        yaml.safe_dump({'proxies': all_proxies}, file, allow_unicode=True)

# 使用示例
merge_proxies('sub', 'sub/merged_proxies.yaml')