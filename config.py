# stock_market/config.py

import os
from datetime import time

# --- 目录与路径 ---
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "plugins_db", "stock_market")
os.makedirs(DATA_DIR, exist_ok=True)
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# --- Web服务配置 ---
# !!! 重要：请将这里的 IP 地址换成您服务器的公网IP !!!
SERVER_PUBLIC_IP = "localhost"
SERVER_PORT = 30005
SERVER_BASE_URL = f"http://{SERVER_PUBLIC_IP}:{SERVER_PORT}"

# --- API 安全与JWT认证 ---
JWT_SECRET_KEY = "4d+/vzSlO9EsdI0/4oEtpS7wkfORC9JJd5fBvGJXEgYkym3jpPmozvvqTIVnXYC1cqdWpfMxfN7G+t1nJWau+g=="
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 60 * 24 * 14  # Token有效期14天

# --- A股交易规则与市场状态 ---
T_OPEN = time(8, 0)
T_CLOSE = time(23, 59, 59)
SELL_LOCK_MINUTES = 60  # 买入后锁定60分钟
SELL_FEE_RATE = 0.01  # 卖出手续费率 1%

# --- V5.4 算法常量 ---
# 交易滑点配置
SLIPPAGE_FACTOR = 0.0000005  # 用于计算大额订单对价格的冲击
MAX_SLIPPAGE_DISCOUNT = 0.3  # 最大滑点为30%

# 玩家交易对市场压力的影响
COST_PRESSURE_FACTOR = 0.0000005  # 交易额转换为市场压力点数的系数

# 上市公司API配置
EARNINGS_SENSITIVITY_FACTOR = 0.5
DEFAULT_LISTED_COMPANY_VOLATILITY = 0.025

# 内在价值更新对市场压力的影响
INTRINSIC_VALUE_PRESSURE_FACTOR = 5

# --- 原生股票随机事件 ---
NATIVE_EVENT_PROBABILITY_PER_TICK = 0.001  # 每5分钟有 0.1% 的概率

NATIVE_STOCK_RANDOM_EVENTS = [
    # 正面事件
    {
        "type": "positive", "effect_type": "price_change_percent", "value_range": [0.05, 0.12],
        "message": "📈 [行业利好] {stock_name}({stock_id})所在行业迎来政策扶持，市场前景看好，股价上涨 {value:.2%}！",
        "weight": 20, "industry": "科技"
    },
    {
        "type": "positive", "effect_type": "price_change_percent", "value_range": [0.03, 0.08],
        "message": "📈 [企业喜讯] {stock_name}({stock_id})宣布与巨头达成战略合作，股价受提振上涨 {value:.2%}！",
        "weight": 15
    },
    {
        "type": "positive", "effect_type": "price_change_percent", "value_range": [0.10, 0.20],
        "message": "📈 [重大突破] {stock_name}({stock_id})公布了革命性的新技术，市场为之疯狂，股价飙升 {value:.2%}！",
        "weight": 5
    },
    # 负面事件
    {
        "type": "negative", "effect_type": "price_change_percent", "value_range": [-0.10, -0.04],
        "message": "📉 [行业利空] 监管机构宣布对{stock_name}({stock_id})所在行业进行严格审查，股价应声下跌 {value:.2%}！",
        "weight": 20
    },
    {
        "type": "negative", "effect_type": "price_change_percent", "value_range": [-0.15, -0.08],
        "message": "📉 [企业丑闻] {stock_name}({stock_id})被爆出数据泄露丑闻，信誉受损，投资者大量抛售，股价下跌 {value:.2%}！",
        "weight": 10
    },
    {
        "type": "negative", "effect_type": "price_change_percent", "value_range": [-0.25, -0.18],
        "message": "📉 [核心产品缺陷] {stock_name}({stock_id})的核心产品被发现存在严重安全漏洞，面临大规模召回，股价暴跌 {value:.2%}！",
        "weight": 3
    }
]
