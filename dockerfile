FROM python:3.11
WORKDIR /app
ADD . .
RUN pip  install -r requirements.txt --index-url http://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
RUN wget -O subconverter.tar.gz https://github.com/tindy2013/subconverter/releases/latest/download/subconverter_linux64.tar.gz
RUN tar -zxvf subconverter.tar.gz -C ./
RUN chmod +x ./subconverter/subconverter && nohup ./subconverter/subconverter >./subconverter.log 2>&1 &
EXPOSE 25500
