# stock_market/config.py

import os
from datetime import time

# --- 目录与路径 ---
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "plugins_db", "stock_market")
os.makedirs(DATA_DIR, exist_ok=True)
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# --- Web服务配置 ---
# !!! 重要：请将这里的 IP 地址换成您服务器IP !!!
SERVER_PUBLIC_IP = "127.0.0.1"
SERVER_PORT = 30005
SERVER_BASE_URL = f"http://{SERVER_PUBLIC_IP}:{SERVER_PORT}"
# 是否使用域名
IS_SERVER_DOMAIN = False
SERVER_DOMAIN = "https://example.domain"

# webAPI速率白名单
RATE_LIMIT_WHITELIST = [
    "127.0.0.1",       # 本地回环地址
    "192.168.1.0/24",  # 局域网192.168.1.0 到 192.168.1.255 范围内的地址
    "10.8.0.0/24"    # wireguard VPN 默认地址范围
]
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
# 分级动能波
BIG_WAVE_PROBABILITY = 0.03  # 每次尝试生成新波段时，是“大波段”的概率 (例如3%)

# “小波段”参数 (常规波动)
SMALL_WAVE_PEAK_MIN = 0.4    # 峰值范围
SMALL_WAVE_PEAK_MAX = 0.8
SMALL_WAVE_TICKS_MIN = 5     # 持续tick范围 (25-60分钟)
SMALL_WAVE_TICKS_MAX = 12

# “大波段”参数 (主升/主跌)
BIG_WAVE_PEAK_MIN = 1.0      # 峰值范围 (强度显著更高)
BIG_WAVE_PEAK_MAX = 1.6
BIG_WAVE_TICKS_MIN = 12     # 持续tick范围 (1-2小时)
BIG_WAVE_TICKS_MAX = 24

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

# ================== V6.0 平衡机制配置 ==================

# --- 流动性池系统 ---
DAILY_LIQUIDITY_LIMIT = 1000000  # 单只股票每日系统接盘上限（金币）
EXTREME_SLIPPAGE_THRESHOLD = 500000  # 开始极端滑点的阈值（金币）
MAX_DAILY_TRADES_PER_USER = 50  # 单用户日交易次数限制
LIQUIDITY_SHORTAGE_PENALTY = 0.1  # 流动性不足时的额外滑点

# --- 渐进式交易费用系统 ---
# 交易额阶梯（元金币，最大值） -> 费率倍数
# 例如：10万以下使用1倍费率，10万到50万使用3倍费率，等等
LARGE_TRADE_FEE_TIERS = {
    99999: 1.0,      # 10万以下：1倍费率（实际区间：0 - 99,999）
    499999: 3.0,     # 10-50万：3倍费率（实际区间：100,000 - 499,999）
    799999: 8.0,     # 50-80万：8倍费率（实际区间：500,000 - 799,999）
    999999: 15.0,    # 80-100万：15倍费率（实际区间：800,000 - 999,999）
    999999999: 50.0  # 100万以上：50倍费率（实际区间：1,000,000+）
}

# 频繁交易惩罚
FREQUENT_TRADE_THRESHOLD = 5  # 1小时内交易次数阈值
FREQUENT_TRADE_PENALTY = 2.0  # 频繁交易费率倍数
MAX_TRADES_FOR_PENALTY = 20  # 超过此交易次数强制惩罚

# --- 市场庄家系统 ---
MARKET_MAKER_ENABLED = True  # 是否启用庄家系统
MARKET_MAKER_BUDGET = 2000000  # 每只股票庄家初始资金（金币）
MARKET_MAKER_MAX_POSITION = 20000000  # 庄家最大持仓（金币）
MARKET_MAKER_BASE_IMPACT = 0.00002  # 庄家交易对价格的基础影响系数
DEVIATION_THRESHOLD = 0.15  # 价格偏离内在价值的干预阈值（15%）
COUNTER_TRADE_INTENSITY = 0.3  # 反向交易力度（30%）
MARKET_PRESSURE_THRESHOLD = 50000  # 市场压力干预阈值

# --- 对称性市场压力系统 ---
COST_PRESSURE_FACTOR = 0.0000002  # 买入压力系数（降低，原0.0000005）
PROFIT_SELL_PRESSURE_MULTIPLIER = 2.0  # 盈利卖出压力倍数
SELL_PRESSURE_FACTOR = 0.0000003  # 卖出压力系数
PRESSURE_DECAY_RATE = 0.85  # 市场压力每tick衰减率（15%衰减）
PENDING_SELL_PRESSURE_RATIO = 0.8  # 买入时预埋卖压的比例

# ====== 【动态增发机制配置】======
# 增发系数：每次有人买入股票时，会增发一定比例的新股
IPO_DILUTION_RATIO = 0.8  # 增发系数（0-1之间，越大稀释越快）

# 单次增发上限（防止极端情况）
MAX_DILUTION_PER_TRADE = 1000000  # 单次最多增发100万股