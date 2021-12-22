FROM ubuntu:16.04 AS build

WORKDIR /build

RUN echo 'deb http://ppa.launchpad.net/deadsnakes/ppa/ubuntu xenial main' >> /etc/apt/sources.list
RUN apt-key adv --keyserver keyserver.ubuntu.com --recv-keys BA6932366A755776
RUN apt-get -y update && apt-get -y install python3.7 python3.7-dev binutils curl git gcc
RUN curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
RUN python3.7 get-pip.py
RUN python3.7 -m pip install pyinstaller

COPY requirements.txt requirements.txt
RUN python3.7 -m pip install --requirement requirements.txt

COPY zrpc zrpc

RUN mkdir /usr/lib64
RUN pyinstaller --onefile zrpc/__main__.py
