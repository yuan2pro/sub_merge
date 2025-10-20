import os
import yaml

def merge_proxies(directory, output_file):
    all_proxies = []
    seen_names = set()  # 用于存储已经遇到的代理名称

    # 遍历目录下的所有 .yaml 文件
    for filename in os.listdir(directory):
        if filename.endswith('.yaml'):
            filepath = os.path.join(directory, filename)
            with open(filepath, 'r', encoding='utf-8') as file:
                data = yaml.safe_load(file)
                if 'proxies' in data:
                    for proxy in data['proxies']:
                        if proxy['name'] not in seen_names:  # 检查代理名称是否已经存在
                            all_proxies.append(proxy)
                            seen_names.add(proxy['name'])  # 将新的代理名称添加到集合中
            os.remove(filepath)

    # 每1000条生成一个yaml文件
    for i in range(0, len(all_proxies), 100):
        chunk = all_proxies[i:i+100]
        with open(f'sub/merged_proxies_{i//100+1}.yaml', 'w', encoding='utf-8') as file:
            yaml.safe_dump({'proxies': chunk}, file, allow_unicode=True)

# 使用示例
merge_proxies('sub', 'sub/merged_proxies.yaml')