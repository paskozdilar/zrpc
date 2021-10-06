import setuptools

VERSION = '0.5.4'

setuptools.setup(
    name='zrpc',
    version=VERSION,
    author='paskozdilar',
    author_email='paskozdilar@gmail.com',
    description='Fast and reliable single-machine RPC',
    url='https://github.com/paskozdilar/zrpc.git',
    packages=setuptools.find_packages(),
    install_requires=[
        'pyzmq>=22.0.0',
        'msgpack>=1.0.0',
    ],
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'zrpc = zrpc.__main__:main',
        ],
    },
)
