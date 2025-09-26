# main.py
# 模拟炒股插件 - 重构版 (应用 V5.3 模拟算法 + A股交易规则)
import jwt
from passlib.context import CryptContext
from functools import wraps
import json
from aiohttp import web
import aiohttp_jinja2
import hashlib
import asyncio
import os
import random
import math
from datetime import datetime, date, time, timedelta
from typing import Optional, List, Dict, Any, Tuple
from collections import deque
from enum import Enum
from dataclasses import dataclass, field
import aiosqlite
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright, Browser, Error as PlaywrightError
from ..common.forwarder import Forwarder
from astrbot.api.event import MessageChain
# --- AstrBot API 导入 ---
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import LLMResponse
from ..common.forwarder import Forwarder
from astrbot.api import logger
try:
    from ..common.services import shared_services
except (ImportError, AttributeError):
    # 创建一个伪造的 shared_services 以防止在未找到时启动失败
    class MockSharedServices:
        def get(self, key): return None
        def register(self, key, value): pass
        def unregister(self, key): pass
    shared_services = MockSharedServices()
    logger.warning("未能从 common.services 导入共享API服务，插件功能将受限。")

from astrbot.api.event import MessageEventResult
import astrbot.api.message_components as Comp

# +++ NEW: 原生股票随机事件配置 +++
# 每个原生股票在每个5分钟周期内，触发随机事件的基础概率
# (0.15 / 192 ≈ 0.0008)，这样保证每天的期望概率不变
NATIVE_EVENT_PROBABILITY_PER_TICK = 0.0008 # 每5分钟有 0.08% 的概率

# 原生股票随机事件池
# effect_type: 'price_change_percent' -> 按百分比改变股价
# value_range: [最小值, 最大值] 的百分比
# industry (可选): 如果指定，则该事件只会发生在对应行业的股票上
NATIVE_STOCK_RANDOM_EVENTS = [
    # --- 正面事件 ---
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
    # --- 负面事件 ---
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



def generate_user_hash(user_id: str) -> str:
    """
    根据用户ID (QQ号) 生成一个唯一的哈希字符串。
    为了 URL 友好和简洁，这里使用 MD5 并取前10位。
    """
    if not isinstance(user_id, str):
        user_id = str(user_id) # 确保是字符串
    
    # 使用 MD5 哈希
    hash_object = hashlib.md5(user_id.encode('utf-8'))
    
    # 取前10位作为用户哈希，确保 URL 路径简洁
    user_hash = hash_object.hexdigest()[:10] 
    return user_hash

# ----------------------------
# 全局设置与常量
# ----------------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "plugins_db", "stock_market_v2")
os.makedirs(DATA_DIR, exist_ok=True)
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True, enable_async=True)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static") 
# --- 新增Web服务配置 ---
# !!! 重要：请将这里的 IP 地址换成您服务器的公网IP !!!
# SERVER_PUBLIC_IP = "192.168.0.147" 
SERVER_PUBLIC_IP = "175.178.112.105" 
SERVER_PORT = 30005 # 您可以选用其他未被占用的端口
SERVER_BASE_URL = f"http://{SERVER_PUBLIC_IP}:{SERVER_PORT}"
# !!! 新增：API 安全设置 !!!
API_SECRET_KEY = "lsb//332211" 

# --- 新增：JWT认证配置 ---
# 用于JWT签名的密钥，极其重要，务必保密且复杂
JWT_SECRET_KEY = "4d+/vzSlO9EsdI0/4oEtpS7wkfORC9JJd5fBvGJXEgYkym3jpPmozvvqTIVnXYC1cqdWpfMxfN7G+t1nJWau+g=="
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 60 * 24 * 14 # Token有效期14天



# 用于密码哈希
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
def jwt_required(handler):
    """JWT Token 验证装饰器"""
    @wraps(handler)
    async def wrapper(self, request: web.Request):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return web.json_response({'error': '未提供认证Token'}, status=401)
        
        token = auth_header.split(' ')[1]
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            # 将解码后的用户信息附加到请求对象上，方便后续处理
            request['jwt_payload'] = payload
        except jwt.ExpiredSignatureError:
            return web.json_response({'error': 'Token已过期'}, status=401)
        except jwt.InvalidTokenError:
            return web.json_response({'error': '无效的Token'}, status=401)
            
        return await handler(self, request)
    return wrapper

def format_large_number(num: float) -> str:
    """
    将一个较大的数字格式化为带有 K, M, B, T, Q, QU, S 后缀的易读字符串。
    示例:
    1230 -> "1.23 K"
    13409 -> "13.41 K"
    4377888 -> "4.38 M"
    1234000000 -> "1.23 B"
    258604470000000 -> "258.60 T"
    1234000000000000 -> "1.23 Q"
    1234000000000000000 -> "1.23 QU"
    1234000000000000000000 -> "1.23 S"
    """
    if num is None:
        return "0.00"
    # 定义数值阈值和对应的后缀
    suffixes = {
        # 1_000_000_000_000_000_000_000_000_000: 'D',
        # 1_000_000_000_000_000_000_000_000: 'O',
        # 1_000_000_000_000_000_000_000: 'Sp',
        # 1_000_000_000_000_000_000: 'Sx',  # 千万亿的千倍 (Sextillion) # 千万亿 (Quintillion)
        1_000_000_000_000_000: 'Q',  # 千万亿 (Quadrillion)
        1_000_000_000_000: 'T',      # 万亿 (Trillion)
        1_000_000_000: 'B',          # 十亿 (Billion)
        1_000_000: 'M',              # 百万 (Million)
        1_000: 'K'                  # 千 (Thousand)
    }
    # 从大到小检查数字所属的区间
    for magnitude, suffix in suffixes.items():
        if abs(num) >= magnitude:
            value = num / magnitude
            return f"{value:.2f} {suffix}"
    # 如果数字小于1000，则直接格式化并返回
    return f"{num:,.2f}"
# -------------------------------------
# A股交易规则与市场状态
# -------------------------------------
# --- 新交易规则 ---
class MarketStatus(Enum):
    CLOSED = "已休市"
    OPEN = "交易中"

T_OPEN = time(8, 0)
#T_CLOSE = time(23, 59, 59)
T_CLOSE = time(23, 59, 59)
SELL_LOCK_MINUTES = 60 # 买入后锁定60分钟
SELL_FEE_RATE = 0.01 # 卖出手续费率 1%
# --- 新增：交易滑点配置 ---
# 用于计算大额订单对价格的冲击。数值越小，冲击越小。
# 示例: 0.0000002 * 100万股 = 0.2, 即20%的滑点
SLIPPAGE_FACTOR = 0.0000005 #10万 滑
# 为防止极端情况，设置一个滑点的最大惩罚上限
MAX_SLIPPAGE_DISCOUNT = 0.3 # 即最大滑点为30%

### 变化点 1：在这里定义新的全局常量 ###
# 用于将玩家交易的“金额”转换为“市场压力点数”的系数
COST_PRESSURE_FACTOR = 0.0000005 #100万交易额=5点压力=0.05价格/5分钟

# +++ NEW: 为上市公司API新增的配置 +++
# 业绩报告对股价影响的敏感度系数
EARNINGS_SENSITIVITY_FACTOR = 0.5 
# 上市公司默认波动率
DEFAULT_LISTED_COMPANY_VOLATILITY = 0.025
# +++ 新增：内在价值更新对市场压力的转换系数 +++
# 这个值决定了当股价低于内在价值时，系统助推的力度有多大
INTRINSIC_VALUE_PRESSURE_FACTOR = 5



# -------------------------------------
# 数据模型 (Data Models) - 基于 V5.3 算法
# -------------------------------------
class MarketCycle(Enum):
    BULL_MARKET = "牛市"; BEAR_MARKET = "熊市"; NEUTRAL_MARKET = "盘整市"

class VolatilityRegime(Enum):
    LOW = "低波动期"; HIGH = "高波动期"

class DailyBias(Enum):
    UP = "上涨日"; DOWN = "下跌日"; SIDEWAYS = "盘整日"

@dataclass
class DailyScript:
    date: date; bias: DailyBias; expected_range_factor: float; target_close: float

@dataclass
class MarketSimulator:
    """宏观市场模拟器"""
    cycle: MarketCycle = MarketCycle.NEUTRAL_MARKET
    volatility_regime: VolatilityRegime = VolatilityRegime.LOW
    steps_in_current_cycle: int = 0
    steps_in_current_vol_regime: int = 0
    min_cycle_duration: int = 7  # 周期最短持续天数
    min_vol_duration: int = 7    # 波动状态最短持续天数

    def update(self):
        """每日更新一次宏观状态"""
        self.steps_in_current_cycle += 1
        if self.steps_in_current_cycle > self.min_cycle_duration and random.random() < 1 / 7:
            old_cycle_name = self.cycle.value
            self.cycle = random.choice([c for c in MarketCycle if c != self.cycle])
            self.steps_in_current_cycle = 0
            logger.info(f"[宏观周期转换] 市场从【{old_cycle_name}】进入【{self.cycle.value}】!")

        self.steps_in_current_vol_regime += 1
        if self.steps_in_current_vol_regime > self.min_vol_duration and random.random() < 1 / 5:
            old_vol_name = self.volatility_regime.value
            self.volatility_regime = VolatilityRegime.HIGH if self.volatility_regime == VolatilityRegime.LOW else VolatilityRegime.LOW
            self.steps_in_current_vol_regime = 0
            logger.info(f"[市场情绪转换] 市场进入【{self.volatility_regime.value}】!")

# --- 股票与API定义 ---
class Trend(Enum):
    """日内微观趋势枚举"""
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0

class StockMarketAPI:
    """
    模拟炒股插件对外暴露的API。
    新版API，用于和虚拟产业插件等其他系统进行交互。
    """
    def __init__(self, plugin_instance: "StockMarketRefactored"):
        self._plugin = plugin_instance

    # --- 以下是为产业插件实现的方法 ---
    async def register_stock(self, ticker: str, company_name: str, initial_price: float, total_shares: int, owner_id: str) -> bool:
        """注册一支新的股票到市场 (通常由公司IPO时调用)。"""
        return await self._plugin.api_register_stock(ticker, company_name, initial_price, total_shares, owner_id)

    async def get_stock_price(self, ticker: str) -> Optional[float]:
        """获取指定股票的当前价格。"""
        return await self._plugin.api_get_stock_price(ticker)
    async def is_ticker_available(self, ticker: str) -> bool:
        """检查一个股票代码是否可用（未被注册）。"""
        return await self._plugin.api_is_ticker_available(ticker)
    async def report_earnings(self, ticker: str, performance_modifier: float):
        """上报公司的业绩表现，用于驱动股价大幅波动。"""
        await self._plugin.api_report_earnings(ticker, performance_modifier)

    async def report_event(self, ticker: str, price_impact_percentage: float):
        """上报一个即时影响股价的事件（如被攻击）。"""
        await self._plugin.api_report_event(ticker, price_impact_percentage)

    async def delist_stock(self, ticker: str) -> bool:
        """当公司破产时，将其从市场退市。"""
        return await self._plugin.api_delist_stock(ticker)
    # +++ 新增：设置内在价值的API接口 +++
    async def set_intrinsic_value(self, ticker: str, value: float):
        """
        【关键接口】设置或更新一只股票的内在价值（基本面价值）。
        此接口由公司插件在公司升级后调用，用于锚定股价。
        """
        await self._plugin.api_set_intrinsic_value(ticker, value)
    async def get_market_cap(self, ticker: str) -> Optional[float]:
        """【关键接口】获取指定股票的总市值。"""
        return await self._plugin.api_get_market_cap(ticker)
    # --- 以下是为经济系统等保留的旧版API方法 ---
    async def get_user_total_asset(self, user_id: str) -> Dict[str, Any]:
        """获取单个用户的详细总资产信息。"""
        return await self._plugin.get_user_total_asset(user_id)

    async def get_total_asset_ranking(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取总资产排行榜。"""
        return await self._plugin.get_total_asset_ranking(limit)
@dataclass
class VirtualStock:
    """虚拟股票的内存数据结构 (适配V5.3 + A股规则)"""
    stock_id: str
    name: str
    current_price: float
    volatility: float = 0.05
    industry: str = "综合"
    # A股规则属性
    previous_close: float = 0.0
    # V5.3 属性
    fundamental_value: float = 200.0
    daily_script: Optional[DailyScript] = None
    intraday_trend: Trend = Trend.NEUTRAL
    intraday_trend_duration: int = 0
    # 历史数据
    price_history: deque = field(default_factory=lambda: deque(maxlen=60))
    daily_close_history: deque = field(default_factory=lambda: deque(maxlen=20))
    kline_history: deque = field(default_factory=lambda: deque(maxlen=9000))
    # +++ NEW: 为上市公司新增的字段 +++
    market_pressure: float = 0.0
    is_listed_company: bool = False
    owner_id: Optional[str] = None
    total_shares: int = 0

    def get_last_day_close(self) -> float: return self.previous_close if self.previous_close > 0 else self.current_price
    
    def get_momentum(self) -> float:
        if len(self.daily_close_history) < 5: return 0.0
        changes = [1 if self.daily_close_history[i] > self.daily_close_history[i-1] else -1 for i in range(1, len(self.daily_close_history))]
        weights = list(range(1, len(changes) + 1)); return sum(c * w for c, w in zip(changes, weights)) / sum(weights)

    def update_fundamental_value(self): self.fundamental_value *= random.uniform(0.999, 1.001)

# ----------------------------
# 主插件类
# ----------------------------
@register("stock_market_v2", "timetetng", "一个功能重构的模拟炒股插件", "3.0.0")
class StockMarketRefactored(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.stocks: Dict[str, VirtualStock] = {}
        self.playwright_browser: Optional[Browser] = None
        self.db_path = os.path.join(DATA_DIR, "stock_market_v2.db")
        # V5.3 算法状态
        self.market_simulator = MarketSimulator()
        self.last_update_date: Optional[date] = None
        # A股交易状态
        self.market_status: MarketStatus = MarketStatus.CLOSED
        # 外部API
        self.bank_api = None
        self.economy_api = None; self.nickname_api = None; self.forwarder = Forwarder()
        # 添加一个集合来存储所有订阅者的 UMO +++
        self.broadcast_subscribers = set()
        # 任务
        self.init_task = asyncio.create_task(self.plugin_init())
        self.price_update_task: Optional[asyncio.Task] = None
        self.api = StockMarketAPI(self)
        self.web_app = None
        self.web_runner = None
        # 验证码
        self.pending_verifications: Dict[str, Dict[str, Any]] = {}

    async def terminate(self):
        logger.info("开始关闭模拟炒股插件...")
        # 使用字典的 pop 方法安全地移除服务
        shared_services.pop("stock_market_api", None)
        logger.info("已注销 StockMarketAPI。")
        if self.init_task and not self.init_task.done():
            self.init_task.cancel()
        if self.price_update_task and not self.price_update_task.done():
            self.price_update_task.cancel()
        
        if self.web_runner:
            await self.web_runner.cleanup()
            logger.info("Web服务已关闭。")
        await self._close_playwright_browser()
        logger.info("模拟炒股插件已成功关闭。")

    async def plugin_init(self):
        """插件的异步初始化流程，带有依赖等待"""
        
        # 这一部分保持不变
        try:
            from ..common.services import shared_services
        except (ImportError, AttributeError):
            logger.warning("未能从 common.services 导入共享API，插件功能将受限。")
            # 无法导入则直接返回，避免后续错误
            return
        
        # --- 新增的依赖等待逻辑 ---
        logger.info("正在等待经济系统API加载...")
        self.economy_api = None
        # 设置一个超时时间，避免无限等待
        timeout_seconds = 30 
        start_time = asyncio.get_event_loop().time()
        
        while self.economy_api is None:
            self.economy_api = shared_services.get("economy_api")
            if self.economy_api is None:
                if asyncio.get_event_loop().time() - start_time > timeout_seconds:
                    logger.warning("等待经济系统API超时，插件功能将受限！")
                    break
                await asyncio.sleep(1) # 每隔1秒重试一次
        
        # 检查是否成功获取
        if self.economy_api:
            logger.info("经济系统API已找到并成功加载。")
        else:
            logger.warning("经济系统API (economy_api) 未找到，插件功能将受限！")

        logger.info("正在等待昵称服务API加载...")
        # 昵称服务不是核心功能，等待时间可以短一些
        timeout_seconds_nickname = 10
        start_time = asyncio.get_event_loop().time()
        while self.nickname_api is None:
            self.nickname_api = shared_services.get("nickname_api")
            if self.nickname_api is None:
                if asyncio.get_event_loop().time() - start_time > timeout_seconds_nickname:
                    logger.warning("等待昵称服务API超时，将无法显示自定义昵称。")
                    break
                await asyncio.sleep(1)

        if self.nickname_api:
            logger.info("昵称服务API (nickname_api) 已成功加载。")


        # +++ 新增：等待银行系统API加载 +++
        logger.info("正在等待银行系统API加载...")
        # 银行服务不是核心功能，等待时间可以短一些
        timeout_seconds_bank = 15 
        start_time = asyncio.get_event_loop().time()
        while self.bank_api is None:
            self.bank_api = shared_services.get("bank_api")
            if self.bank_api is None:
                if asyncio.get_event_loop().time() - start_time > timeout_seconds_bank:
                    logger.warning("等待银行系统API超时，资产计算将不包含银行存贷款。")
                    break
                await asyncio.sleep(1)

        if self.bank_api:
            logger.info("银行系统API (bank_api) 已成功加载。")

        # 这一部分保持不变，继续你的初始化流程
        await self._initialize_database()
        await self._load_stocks_from_db()
        await self._start_playwright_browser()
        await self._load_subscriptions_from_db()
        self.price_update_task = asyncio.create_task(self._update_stock_prices_loop())         
        self.api = StockMarketAPI(self)         
        shared_services["stock_market_api"] = self.api
        await self._start_web_server()
        logger.info(f"模拟炒股插件已加载。数据库: {self.db_path}")

    async def _handle_native_stock_random_event(self, stock: VirtualStock) -> Optional[str]:
        """
        处理原生虚拟股票的每日随机事件。
        返回一个事件消息字符串，如果没有事件发生则返回 None。
        """
        # 1. 检查是否应该触发事件
        if random.random() > NATIVE_EVENT_PROBABILITY_PER_TICK:
            return None

        # 2. 筛选符合条件的事件（通用事件 + 行业特定事件）
        eligible_events = [
            event for event in NATIVE_STOCK_RANDOM_EVENTS
            if event.get("industry") is None or event.get("industry") == stock.industry
        ]
        if not eligible_events:
            return None

        # 3. 根据权重随机选择一个事件
        event_weights = [event.get('weight', 1) for event in eligible_events]
        chosen_event = random.choices(eligible_events, weights=event_weights, k=1)[0]

        # 4. 根据事件类型，计算并应用效果
        effect_type = chosen_event.get("effect_type")
        if effect_type == 'price_change_percent':
            value_min, value_max = chosen_event['value_range']
            percent_change = round(random.uniform(value_min, value_max), 4)
            
            old_price = stock.current_price
            new_price = round(old_price * (1 + percent_change), 2)
            
            # 确保价格不低于0.01
            stock.current_price = max(0.01, new_price)
            
            # 5. 构建并返回事件消息
            return chosen_event['message'].format(
                stock_name=stock.name,
                stock_id=stock.stock_id,
                value=percent_change
            )
        
        return None

    # +++ 为产业插件实现的API方法 +++
    async def api_register_stock(self, ticker: str, company_name: str, initial_price: float, total_shares: int, owner_id: str) -> bool:
        """API实现：注册一支新股票 (V4 - 彻底修复版)""" # <-- 版本号可以更新一下
        ticker = ticker.upper()
        if ticker in self.stocks:
            logger.error(f"[API.register_stock] 失败：股票代码 {ticker} 已存在。")
            return False

        # 1. 创建新的 VirtualStock 实例 (此部分不变)
        new_stock = VirtualStock(
            stock_id=ticker,
            name=company_name,
            current_price=initial_price,
            volatility=DEFAULT_LISTED_COMPANY_VOLATILITY,
            industry="上市公司",
            fundamental_value=initial_price,
            previous_close=initial_price,
            is_listed_company=True,
            owner_id=owner_id,
            total_shares=total_shares
        )
        
        # +++ 新增的修复逻辑 +++
        # 步骤 a: 模拟从数据库加载时的历史初始化，确保 price_history 不为空。
        # 这对于依赖历史数据的算法（哪怕只是前一刻的数据）至关重要。
        new_stock.price_history.append(initial_price)

        # 步骤 b: 调用每日基本面更新函数，与主循环中的每日初始化流程对齐。
        new_stock.update_fundamental_value()
        # +++ 修复逻辑结束 +++
        
        # 2. 【关键修复】立即为新股票生成当日剧本，确保它能被价格更新循环捕获
        today = datetime.now().date()
        # 检查宏观市场是否已为当天初始化，如果没有则进行初始化
        if self.last_update_date != today:
            logger.info(f"新交易日 ({today}) 因新股上市而提前初始化宏观市场...")
            self.market_simulator.update()
            self.last_update_date = today
            
        # 为新股票生成当日剧本并赋值
        new_stock.daily_script = self._generate_daily_script(new_stock, today)
        
        # 3. 添加到内存 (此部分及之后不变)
        self.stocks[ticker] = new_stock
        logger.info(f"已为新上市公司 {ticker} 生成当日交易剧本，并加入到内存中。当前总股票数: {len(self.stocks)}")

        # 4. 持久化到数据库
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT INTO stocks (stock_id, name, current_price, volatility, industry, is_listed_company, owner_id, total_shares, fundamental_value) 
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ticker, company_name, initial_price, new_stock.volatility, new_stock.industry, 
                     True, owner_id, total_shares, initial_price)
                )
                await db.commit()
            logger.info(f"[API.register_stock] 成功：新公司 {company_name} ({ticker}) 已上市并存入数据库。")
            return True
        except Exception as e:
            # 如果数据库写入失败，则回滚内存中的操作
            del self.stocks[ticker]
            logger.error(f"[API.register_stock] 数据库操作失败: {e}", exc_info=True)
            return False

    async def api_get_stock_price(self, ticker: str) -> Optional[float]:
        """API实现：获取股价"""
        stock = self.stocks.get(ticker.upper())
        return stock.current_price if stock else None

    async def api_report_earnings(self, ticker: str, performance_modifier: float):
        """API实现：根据业绩报告调整股价 (V4 - 修改内在价值，彻底修复)"""
        ticker = ticker.upper()
        stock = self.stocks.get(ticker)
        if not stock:
            logger.warning(f"[API.report_earnings] 找不到股票 {ticker}。")
            return

        old_price = stock.current_price
        old_fundamental_value = stock.fundamental_value

        # --- 核心修改 ---
        # 1. 计算价格变动因子
        price_change_factor = 1.0 + (performance_modifier - 1.0) * EARNINGS_SENSITIVITY_FACTOR
        
        # 2. 计算新价格和新的内在价值
        new_price = round(old_price * price_change_factor, 2)
        new_price = max(0.01, new_price)
        new_fundamental_value = round(old_fundamental_value * price_change_factor, 2)
        new_fundamental_value = max(0.01, new_fundamental_value)

        # 3. 更新内存数据
        stock.current_price = new_price
        stock.fundamental_value = new_fundamental_value
        stock.price_history.append(new_price)

        # 4. 持久化到数据库
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE stocks SET current_price = ?, fundamental_value = ? WHERE stock_id = ?",
                    (new_price, new_fundamental_value, ticker)
                )
                await db.commit()
            logger.info(
                f"[API.report_earnings] {stock.name}({ticker}) 财报更新 (修正: {performance_modifier:.2f}). "
                f"股价: {old_price:.2f} -> {new_price:.2f}. "
                f"内在价值: {old_fundamental_value:.2f} -> {new_fundamental_value:.2f}. (已持久化)"
            )
        except Exception as e:
            logger.error(f"[API.report_earnings] 持久化股票 {ticker} 新数据时失败: {e}", exc_info=True)

    async def api_report_event(self, ticker: str, price_impact_percentage: float):
        """API实现：根据即时事件调整股价 (V4 - 修改内在价值，彻底修复)"""
        ticker = ticker.upper()
        stock = self.stocks.get(ticker)
        if not stock:
            logger.warning(f"[API.report_event] 找不到股票 {ticker}。")
            return
            
        old_price = stock.current_price
        old_fundamental_value = stock.fundamental_value
        
        # --- 核心修改 ---
        # 1. 计算新价格
        new_price = round(old_price * (1.0 + price_impact_percentage), 2)
        new_price = max(0.01, new_price)

        # 2. (关键) 按相同比例计算新的内在价值，这才是持久影响的关键
        new_fundamental_value = round(old_fundamental_value * (1.0 + price_impact_percentage), 2)
        new_fundamental_value = max(0.01, new_fundamental_value)

        # 3. 更新内存中的核心数据
        stock.current_price = new_price
        stock.fundamental_value = new_fundamental_value
        stock.price_history.append(new_price) 

        # 4. (关键) 将新价格 和 新的内在价值 同时持久化到数据库
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE stocks SET current_price = ?, fundamental_value = ? WHERE stock_id = ?",
                    (new_price, new_fundamental_value, ticker)
                )
                await db.commit()
            logger.info(
                f"[API.report_event] {stock.name}({ticker}) 事件冲击: {price_impact_percentage:+.2%}. "
                f"股价: {old_price:.2f} -> {new_price:.2f}. "
                f"内在价值: {old_fundamental_value:.2f} -> {new_fundamental_value:.2f}. (已持久化)"
            )
        except Exception as e:
            logger.error(f"[API.report_event] 持久化股票 {ticker} 新数据时失败: {e}", exc_info=True)


    async def api_get_market_cap(self, ticker: str) -> Optional[float]:
        """API实现：计算并返回总市值"""
        stock = self.stocks.get(ticker.upper())
        if not stock or not stock.is_listed_company:
            return None
        return stock.current_price * stock.total_shares

    async def api_delist_stock(self, ticker: str) -> bool:
        """API实现：股票退市"""
        ticker = ticker.upper()
        if ticker not in self.stocks:
            logger.warning(f"[API.delist_stock] 尝试退市不存在的股票 {ticker}。")
            return False

        # 从内存中移除
        del self.stocks[ticker]
        
        # 从数据库中移除
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM stocks WHERE stock_id = ?", (ticker,))
                # (可选) 清理相关的所有持仓记录
                await db.execute("DELETE FROM holdings WHERE stock_id = ?", (ticker,))
                await db.commit()
            logger.info(f"[API.delist_stock] 股票 {ticker} 已成功退市。")
            return True
        except Exception as e:
            logger.error(f"[API.delist_stock] 数据库操作失败: {e}", exc_info=True)
            return False

    async def api_set_intrinsic_value(self, ticker: str, value: float):
        """
        API实现：更新股票的内在价值。
        - 如果当前价格低于新价值，则注入正向市场压力以助推股价。
        - 如果当前价格高于新价值，则只更新锚点，不干预市场。
        """
        ticker = ticker.upper()
        stock = self.stocks.get(ticker)
        if not stock:
            logger.warning(f"[API.set_intrinsic_value] 找不到股票 {ticker}。")
            return
        
        if value <= 0:
            logger.warning(f"[API.set_intrinsic_value] 尝试为 {ticker} 设置无效的内在价值: {value}。")
            return

        old_value = stock.fundamental_value
        current_price = stock.current_price
        
        # 1. 核心逻辑：根据当前价格与新内在价值的关系，决定是否注入市场压力
        if current_price < value:
            # 市场反应不足，需要助推
            price_gap = value - current_price
            # 将价格差距转换为巨大的正向市场压力
            pressure_injection = price_gap * INTRINSIC_VALUE_PRESSURE_FACTOR
            stock.market_pressure += pressure_injection
            logger.info(f"[API.set_intrinsic_value] {ticker} 股价低于新内在价值，注入 {pressure_injection:,.2f} 点市场压力。")
        else:
            # 市场已经过热，只更新锚点，不干预
            logger.info(f"[API.set_intrinsic_value] {ticker} 股价已高于新内在价值，尊重市场泡沫，只更新价值锚点。")

        # 2. 无论如何，都必须更新内在价值作为新的锚点
        stock.fundamental_value = value
        
        # 3. 更新数据库
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 只更新 fundamental_value 和 market_pressure，不再强制修改 current_price
                await db.execute("UPDATE stocks SET fundamental_value = ?, market_pressure = ? WHERE stock_id = ?", 
                                 (stock.fundamental_value, stock.market_pressure, ticker))
                await db.commit()
            logger.info(f"[API.set_intrinsic_value] 股票 {stock.name}({ticker}) 的内在价值已从 {old_value:.2f} 更新为 {value:.2f}。")
        except Exception as e:
            # 回滚内存中的修改
            stock.fundamental_value = old_value
            # 注意：这里回滚 market_pressure 较为复杂，暂简化处理
            logger.error(f"[API.set_intrinsic_value] 更新股票 {ticker} 的内在价值时数据库操作失败: {e}", exc_info=True) 
   
    async def api_is_ticker_available(self, ticker: str) -> bool:
        """API实现：检查股票代码是否可用"""
        # .upper() 确保不区分大小写
        is_available = ticker.upper() not in self.stocks
        logger.info(f"[API.is_ticker_available] 查询代码 '{ticker.upper()}': {'可用' if is_available else '已被占用'}")
        return is_available
    # ------------------------------------
    # A股规则核心方法
    # ------------------------------------
    def _get_market_status(self) -> Tuple[MarketStatus, int]:
        """获取当前市场状态及到下一状态的秒数 (新规则)"""
        now = datetime.now()
        current_time = now.time()

        if T_OPEN <= current_time <= T_CLOSE:
            # 交易中
            return MarketStatus.OPEN, 1
        else:
            # 休市
            if current_time < T_OPEN: # 凌晨
                next_open_dt = datetime.combine(now.date(), T_OPEN)
            else: # 晚上
                next_open_dt = datetime.combine(now.date() + timedelta(days=1), T_OPEN)
            
            wait_seconds = int((next_open_dt - now).total_seconds())
            return MarketStatus.CLOSED, max(1, wait_seconds)

# ------------webk线 aiohttp 服务器--------------
    async def _start_web_server(self):
        """初始化并启动 aiohttp Web 服务器"""
        self.web_app = web.Application(logger=logger)
        aiohttp_jinja2.setup(self.web_app, loader=jinja_env.loader, enable_async=True)
        
        # --- 原有路由 ---
        self.web_app.router.add_get('/api/kline/{stock_id}', self._handle_kline_api) 
        self.web_app.router.add_static('/static/', path=STATIC_DIR, name='static')
        self.web_app.router.add_get('/charts/{user_hash}', self._handle_user_charts_page)
        self.web_app.router.add_get('/api/get_user_hash', self._handle_get_user_hash)
        
        # --- !!! 新增的 API 路由 !!! ---
        api_v1 = web.Application()
        api_v1.router.add_get('/stock/{stock_id}', self._api_get_stock_info)
        api_v1.router.add_get('/stocks', self._api_get_all_stocks)
        api_v1.router.add_get('/portfolio', self._api_get_user_portfolio)
        api_v1.router.add_post('/trade/buy', self._api_trade_buy)
        api_v1.router.add_post('/trade/sell', self._api_trade_sell)
        api_v1.router.add_get('/ranking', self._api_get_ranking)
        self.web_app.add_subapp('/api/v1', api_v1)
        # --------------------------------
        # --- 新增：认证 API 路由 ---
        auth_app = web.Application()
        auth_app.router.add_post('/register', self._api_auth_register)
        auth_app.router.add_post('/login', self._api_auth_login)
        self.web_app.add_subapp('/api/auth', auth_app)

        self.web_runner = web.AppRunner(self.web_app)
        await self.web_runner.setup()
        site = web.TCPSite(self.web_runner, '0.0.0.0', SERVER_PORT)
        await site.start()
        logger.info(f"Web服务及API已在 {SERVER_BASE_URL} 上启动。")


    async def _handle_chart_page(self, request: web.Request):
        """处理K线图页面的HTTP请求"""
        try:
            stock_id = request.match_info.get('stock_id', "").upper()
            stock = await self._find_stock(stock_id)

            if not stock or len(stock.kline_history) < 2:
                return web.HTTPNotFound(text=f"找不到股票 {stock_id} 或其数据不足")

            total_minutes = len(stock.kline_history) * 5
            context = {
                'stock_name': stock.name,
                'stock_id': stock.stock_id,
                'data_period': f"最近 {total_minutes} 分钟",
                'stock_data': list(stock.kline_history)
            }
            
            # 手动调用 aiohttp_jinja2 的渲染函数
            response = await aiohttp_jinja2.render_template_async(
                'kline_chart.html', # 模板文件名
                request,             # aiohttp 的请求对象
                context              # 包含所有数据的字典
            )
            return response

        except Exception as e:
            # 如果渲染过程中出现任何错误，现在我们能捕获它并记录日志
            logger.error(f"处理web请求 /chart/{request.match_info.get('stock_id')} 时发生严重错误:", exc_info=True)
            return web.HTTPInternalServerError(text="服务器渲染页面时发生内部错误，请查看后台日志。")

    # ----------------------------
# Web 服务核心 (多图表版)
    # ----------------------------
    async def _handle_kline_api(self, request: web.Request):
        """为前端提供K线数据的API接口 (同时支持JWT和user_hash)"""
        try:
            # --- 1. 获取请求参数 ---
            stock_id = request.match_info.get('stock_id', "").upper()
            user_hash = request.query.get('user_hash')
            period = request.query.get('period', '1d')

            stock = await self._find_stock(stock_id)
            if not stock or len(stock.kline_history) < 2:
                return web.json_response({'error': 'not found'}, status=404)

            # --- 2. K线历史数据筛选 ---
            now = datetime.now()
            days_map = {'1d': 1, '7d': 7, '30d': 30}
            cutoff_time = now - timedelta(days=days_map.get(period, 1))
            
            filtered_kline_history = [
                candle for candle in stock.kline_history if candle.get('date') and candle['date'] >= cutoff_time.isoformat()
            ]

            # --- 3. 智能识别用户身份 ---
            target_user_id = None
            
            # 3.1 优先尝试从JWT Token中获取用户ID (适用于已登录用户)
            auth_header = request.headers.get('Authorization')
            if auth_header and auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
                try:
                    payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
                    target_user_id = payload.get('sub')
                    logger.info(f"[K线API] 通过JWT识别到已登录用户: {target_user_id}")
                except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                    logger.warning("[K线API] 提供的JWT无效或已过期，将忽略")

            # 3.2 如果没有有效的JWT，再回退到使用 user_hash (兼容游客模式)
            if not target_user_id and user_hash:
                logger.info(f"[K线API] 未检测到有效登录，尝试通过user_hash '{user_hash}' 识别游客")
                async with aiosqlite.connect(self.db_path) as db:
                    # 注意: 此查询在用户量大时效率较低，但对于当前场景可用
                    cursor = await db.execute("SELECT DISTINCT user_id FROM holdings")
                    all_user_ids = [row[0] for row in await cursor.fetchall()]
                    for uid in all_user_ids:
                        if generate_user_hash(uid) == user_hash:
                            target_user_id = uid
                            logger.info(f"[K线API] 通过user_hash成功匹配到用户: {target_user_id}")
                            break
            
            # --- 4. 根据识别到的用户ID，获取其持仓信息 ---
            user_holdings = []
            if target_user_id:
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute(
                        "SELECT quantity, purchase_price FROM holdings WHERE user_id=? AND stock_id=?", 
                        (target_user_id, stock_id)
                    )
                    raw_holdings = await cursor.fetchall()
                    if raw_holdings:
                        total_qty = sum(row[0] for row in raw_holdings)
                        total_cost = sum(row[0] * row[1] for row in raw_holdings)
                        avg_cost = total_cost / total_qty if total_qty > 0 else 0
                        user_holdings.append({
                            "stock_id": stock_id,
                            "quantity": total_qty,
                            "avg_cost": avg_cost,
                        })
            
            # --- 5. 构造并返回响应 ---
            response_data = {
                "kline_history": filtered_kline_history,
                "user_holdings": user_holdings
            }
            return web.json_response(response_data)
            
        except Exception as e:
            logger.error(f"处理K线API请求时发生未知错误: {e}", exc_info=True)
            return web.json_response({'error': '服务器内部错误'}, status=500)
    
    @aiohttp_jinja2.template('charts_page.html')
    async def _handle_user_charts_page(self, request: web.Request):
        """处理用户专属多图表页面的HTTP请求 (已修正缩进)"""
        user_hash = request.match_info.get('user_hash')
        logger.info(f"[Web看板] 收到对 user_hash '{user_hash}' 的访问请求。")

        stocks_list = sorted(
            [{'stock_id': s.stock_id, 'name': s.name} for s in self.stocks.values()],
            key=lambda x: x['stock_id']
        )
        
        user_id = None
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT DISTINCT user_id FROM holdings")
            all_user_ids = [row[0] for row in await cursor.fetchall()]
        for uid in all_user_ids:
            if generate_user_hash(uid) == user_hash:
                user_id = uid
                break

        user_portfolio_data = None
        if user_id:
            # --- 从这里开始，直到 return 之前的所有逻辑，都应在这个 if 内部 ---
            logger.info(f"[Web看板] 成功匹配到 user_id: {user_id}。")           
            display_name = await self._get_display_name(user_id)
            
            # 获取并处理持仓数据
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT stock_id, quantity, purchase_price FROM holdings WHERE user_id=?", (user_id,))
                raw_holdings = await cursor.fetchall()
            
            if raw_holdings:
                aggregated_holdings = {}
                for stock_id, qty, price in raw_holdings:
                    if stock_id not in aggregated_holdings:
                        aggregated_holdings[stock_id] = {'quantity': 0, 'cost_basis': 0}
                    aggregated_holdings[stock_id]['quantity'] += qty
                    aggregated_holdings[stock_id]['cost_basis'] += qty * price

                holdings_list_for_template = []
                total_market_value = 0
                total_cost_basis = 0
                
                for stock_id, data in aggregated_holdings.items():
                    stock = self.stocks.get(stock_id)
                    if not stock: continue
                    
                    qty = data['quantity']
                    cost_basis = data['cost_basis']
                    avg_cost = cost_basis / qty if qty > 0 else 0
                    market_value = qty * stock.current_price
                    pnl = market_value - cost_basis
                    pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0
                    
                    holdings_list_for_template.append({
                        "name": stock.name, "stock_id": stock.stock_id, "quantity": qty, 
                        "avg_cost": avg_cost, "market_value": market_value, "pnl": pnl,
                        "pnl_percent": pnl_percent, "is_positive": pnl >= 0,
                    })
                    total_market_value += market_value
                    total_cost_basis += cost_basis
                
                total_pnl = total_market_value - total_cost_basis
                total_pnl_percent = (total_pnl / total_cost_basis) * 100 if total_cost_basis > 0 else 0

                user_portfolio_data = {
                    "user_name": display_name,
                    "holdings": holdings_list_for_template,
                    "total": {
                        "market_value": total_market_value, "pnl": total_pnl,
                        "pnl_percent": total_pnl_percent, "is_positive": total_pnl >= 0,
                    }
                }
            else:
                # 处理用户存在但没有持仓的情况
                user_portfolio_data = {
                    "user_name": display_name,
                    "holdings": [],
                    "total": { "market_value": 0, "pnl": 0, "pnl_percent": 0, "is_positive": True }
                }

        # 这个 return 语句在 if user_id 块之外，是正确的
        return {
            'stocks': stocks_list,
            'user_hash': user_hash,
            'user_portfolio_data': user_portfolio_data
        }

    async def _get_display_name(self, user_id: str) -> str:
        """根据用户ID，按优先级获取最佳显示名称"""
        # 1. 默认使用用户ID (QQ号)
        display_name = user_id
        
        # 2. 尝试获取注册时使用的登录名 (如果有的话)
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT login_id FROM users WHERE user_id = ?", (user_id,))
                user_record = await cursor.fetchone()
                if user_record and user_record[0]:
                    display_name = user_record[0]
        except Exception as e:
            logger.error(f"查询用户 {user_id} 的登录名时出错: {e}")

        # 3. 尝试获取QQ昵称 (更高优先级)
        if self.economy_api:
            try:
                profile = await self.economy_api.get_user_profile(user_id)
                if profile and profile.get('nickname'):
                    display_name = profile['nickname']
            except Exception as e:
                logger.error(f"调用 EconomyAPI.get_user_profile 时出错: {e}", exc_info=True)
        
        # 4. 尝试获取自定义昵称 (最高优先级)
        if not self.nickname_api:
            self.nickname_api = shared_services.get("nickname_api")
        if self.nickname_api:
            try:
                custom_name = await self.nickname_api.get_nickname(user_id)
                if custom_name:
                    display_name = custom_name
            except Exception as e:
                logger.error(f"调用 NicknameAPI 时出错: {e}", exc_info=True)

        logger.info(f"[昵称查询] 用户 {user_id} 的最终显示名称为: {display_name}")
        return display_name

    async def _handle_get_user_hash(self, request: web.Request):
        """根据QQ号查询并返回对应的user_hash"""
        qq_id = request.query.get('qq_id')
        if not qq_id or not qq_id.isdigit():
            return web.json_response({'error': '无效的QQ号'}, status=400)
        
        user_hash = generate_user_hash(qq_id)
        return web.json_response({'user_hash': user_hash})

# --- Web API 实现 ---
    async def _api_get_stock_info(self, request: web.Request):
        """API: 获取单支股票的详细信息"""
        stock_id = request.match_info.get('stock_id', "").upper()
        stock = await self._find_stock(stock_id)
        if not stock:
            return web.json_response({'error': 'Stock not found'}, status=404)
        return web.json_response({
            'stock_id': stock.stock_id,
            'name': stock.name,
            'current_price': stock.current_price,
            'previous_close': stock.previous_close,
            'industry': stock.industry,
            'volatility': stock.volatility
        })

    async def _api_get_all_stocks(self, request: web.Request):
        """API: 获取所有股票的列表"""
        stock_list = [
            {
                'stock_id': s.stock_id, 'name': s.name, 'current_price': s.current_price
            } for s in sorted(self.stocks.values(), key=lambda x: x.stock_id)
        ]
        return web.json_response(stock_list)

    @jwt_required
    async def _api_get_user_portfolio(self, request: web.Request):
        """API: 获取当前登录用户的资产和持仓信息 (JWT认证)"""
        try:
            # --- 从这里开始的所有逻辑，都应该在 try 块内部 ---
            
            # 从装饰器附加的payload中获取用户ID，这绝对安全
            user_id = request['jwt_payload']['sub']
            display_name = await self._get_display_name(user_id)
            # 复用您现有的 get_user_total_asset 逻辑获取总览
            asset_summary = await self.get_user_total_asset(user_id)

            # 补充详细的持仓列表
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT stock_id, quantity, purchase_price FROM holdings WHERE user_id=?", (user_id,))
                raw_holdings = await cursor.fetchall()

            aggregated_holdings = {}
            for stock_id, qty, price in raw_holdings:
                if stock_id not in aggregated_holdings:
                    aggregated_holdings[stock_id] = {'quantity': 0, 'cost_basis': 0}
                aggregated_holdings[stock_id]['quantity'] += qty
                aggregated_holdings[stock_id]['cost_basis'] += qty * price
            
            detailed_stocks = []
            for stock_id, data in aggregated_holdings.items():
                stock = self.stocks.get(stock_id)
                if not stock: continue
                
                qty = data['quantity']
                cost_basis = data['cost_basis']
                avg_cost = cost_basis / qty if qty > 0 else 0
                market_value = qty * stock.current_price
                pnl = market_value - cost_basis
                pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

                detailed_stocks.append({
                    'stock_id': stock_id,
                    'name': stock.name,
                    'quantity': qty,
                    'avg_cost': round(avg_cost, 2),
                    'market_value': round(market_value, 2),
                    'pnl': round(pnl, 2),
                    'pnl_percent': round(pnl_percent, 2)
                })
            asset_summary['user_name'] = display_name
            asset_summary['holdings_detailed'] = detailed_stocks
            return web.json_response(asset_summary)

        # --- except 块现在可以正确地匹配到 try 块了 ---
        except Exception as e:
            user_id_for_log = request.get('jwt_payload', {}).get('sub', '未知用户')
            logger.error(f"获取用户 {user_id_for_log} 持仓时出错: {e}", exc_info=True)
            return web.json_response({'error': '获取持仓信息时发生内部错误'}, status=500)

    async def _api_get_ranking(self, request: web.Request):
        """API: 获取总资产排行榜"""
        limit = int(request.query.get('limit', 10))
        # 直接复用现有方法
        ranking_data = await self.get_total_asset_ranking(limit)
        return web.json_response(ranking_data)

    @jwt_required # <-- 应用装饰器
    async def _api_trade_buy(self, request: web.Request):
        """API: 执行买入交易 (JWT认证)"""
        try:
            data = await request.json()
            user_id = request['jwt_payload']['sub'] # <-- 从Token获取用户ID
            stock_id = data['stock_id'].upper()
            quantity = int(data['quantity'])
            
            success, message = await self._internal_perform_buy(user_id, stock_id, quantity)

            if success:
                return web.json_response({'success': True, 'message': message})
            else:
                return web.json_response({'success': False, 'message': message}, status=400)
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return web.json_response({'error': f'无效的请求体: {e}'}, status=400)

    @jwt_required # <-- 应用装饰器
    async def _api_trade_sell(self, request: web.Request):
        """API: 执行卖出交易 (JWT认证)"""
        try:
            data = await request.json()
            user_id = request['jwt_payload']['sub'] # <-- 从Token获取用户ID
            stock_id = data['stock_id'].upper()
            quantity = int(data['quantity'])
            
            success, message, _ = await self._internal_perform_sell(user_id, stock_id, quantity)

            if success:
                return web.json_response({'success': True, 'message': message})
            else:
                return web.json_response({'success': False, 'message': message}, status=400)
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return web.json_response({'error': f'无效的请求体: {e}'}, status=400)


    async def _api_auth_login(self, request: web.Request):
        """API: 用户登录并获取JWT Token (支持自定义登录名)"""
        try:
            data = await request.json()
            login_id = data.get('user_id') # 用户在网页上输入的是登录名
            password = data.get('password')

            async with aiosqlite.connect(self.db_path) as db:
                # 使用 login_id 查询
                cursor = await db.execute("SELECT password_hash, user_id FROM users WHERE login_id = ?", (login_id,))
                user_record = await cursor.fetchone()

                if not user_record or not pwd_context.verify(password, user_record[0]):
                    return web.json_response({'error': '登录名或密码错误'}, status=401)
            
            # 验证成功，从记录中获取真实的 user_id (QQ号)
            qq_user_id = user_record[1]
            
            # 在Token中存入真实的QQ号，因为所有游戏数据都与它关联
            expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRATION_MINUTES)
            payload = {'sub': qq_user_id, 'login_id': login_id, 'exp': expire}
            token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
            
            return web.json_response({'access_token': token, 'token_type': 'bearer', 'user_id': qq_user_id, 'login_id': login_id})
        except Exception as e:
            logger.error(f"登录时发生错误: {e}", exc_info=True)
            return web.json_response({'error': '服务器内部错误'}, status=500)

    async def _api_auth_register(self, request: web.Request):
        """API: 发起用户注册，生成并返回验证码 (支持自定义登录名)"""
        try:
            data = await request.json()
                        
            # --- !!! 核心诊断日志 !!! ---
            logger.info(f"[注册诊断] 收到的原始请求数据: {data}")
            # ---
            login_id = data.get('user_id') # 网页上输入的现在是登录名
            password = data.get('password')

            if not login_id or not password:
                return web.json_response({'error': '登录名和密码不能为空'}, status=400)

            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT login_id FROM users WHERE login_id = ?", (login_id,))
                if await cursor.fetchone():
                    return web.json_response({'error': '该登录名已被使用'}, status=409)

            verification_code = f"{random.randint(100000, 999999)}"
            while verification_code in self.pending_verifications:
                verification_code = f"{random.randint(100000, 999999)}"

            password_hash = pwd_context.hash(password)
            self.pending_verifications[verification_code] = {
                'login_id': login_id, # <--- 暂存登录名
                'password_hash': password_hash,
                'timestamp': datetime.now()
            }
            
            logger.info(f"为登录名 '{login_id}' 生成了一个新的注册验证码: {verification_code}")
            return web.json_response({'success': True, 'verification_code': verification_code})

        except Exception as e:
            logger.error(f"发起注册时发生错误: {e}", exc_info=True)
            return web.json_response({'error': '服务器内部错误'}, status=500)
    # ------------------------------------
    # 内部核心方法 (Core Methods) - V5.3
    # ------------------------------------
    def _generate_daily_script(self, stock: VirtualStock, current_date: date) -> DailyScript:
        """为单支股票生成每日剧本 (V5.3 算法 - 已修复波动率问题)"""
        momentum = stock.get_momentum()
        last_close = stock.get_last_day_close()
        valuation_ratio = last_close / stock.fundamental_value

        mean_reversion_pressure = 1.0
        if valuation_ratio < 0.7: mean_reversion_pressure = 1 / max(valuation_ratio, 0.1)
        elif valuation_ratio > 1.5: mean_reversion_pressure = valuation_ratio

        bias_weights = [1.0, 1.0, 1.0]
        if self.market_simulator.cycle == MarketCycle.BULL_MARKET: bias_weights[0] *= 2.0
        elif self.market_simulator.cycle == MarketCycle.BEAR_MARKET: bias_weights[2] *= 2.0
        if momentum > 0: bias_weights[0] *= (1 + momentum * 1.5)
        elif momentum < 0: bias_weights[2] *= (1 - abs(momentum) * 1.5)
        if valuation_ratio < 0.7: bias_weights[0] *= mean_reversion_pressure
        elif valuation_ratio > 1.5: bias_weights[2] *= mean_reversion_pressure
        bias = random.choices([DailyBias.UP, DailyBias.SIDEWAYS, DailyBias.DOWN], weights=bias_weights, k=1)[0]

        # --- 核心修正点 ---
        # 不再使用硬编码的范围，而是基于股票自身的 volatility 属性
        # 这意味着每日的基础波幅会在股票自身波动率的 70% 到 150% 之间随机
        base_range = stock.volatility * random.uniform(0.7, 1.5)
        # --- 修正结束 ---

        # 后续的宏观环境影响逻辑保持不变
        if self.market_simulator.volatility_regime == VolatilityRegime.HIGH: base_range *= 1.7
        if bias != DailyBias.SIDEWAYS: base_range *= 1.3

        price_change = last_close * base_range * random.uniform(0.4, 1.0)
        if bias == DailyBias.UP:
            target_close = last_close + price_change
        elif bias == DailyBias.DOWN:
            target_close = last_close - price_change
        else:
            target_close = last_close + (price_change / 2 * random.choice([-1, 1]))

        return DailyScript(date=current_date, bias=bias, expected_range_factor=base_range, target_close=max(0.01, target_close))

    async def _update_stock_prices_loop(self):
            """后台任务循环，根据新规则更新股票价格 (V5.4 微观波动增强版)"""
            # await asyncio.sleep(1) # 如果您改成了1秒，可以保留
            while True:
                try:
                    new_status, wait_seconds = self._get_market_status()

                    if new_status != self.market_status:
                        logger.info(f"市场状态变更: {self.market_status.value} -> {new_status.value}")
                        self.market_status = new_status
                    
                    if self.market_status != MarketStatus.OPEN:
                        if wait_seconds > 0:
                            await asyncio.sleep(wait_seconds)
                        continue

                    now = datetime.now()
                    today = now.date()

                    if self.last_update_date != today:
                        logger.info(f"新交易日 ({today}) 开盘，正在初始化市场...")
                        self.market_simulator.update()

                        for stock in self.stocks.values():
                            # 此处的随机事件逻辑已被移至下方的5分钟循环中，以实现盘中随机触发
                            
                            if self.last_update_date:
                                stock.previous_close = stock.current_price
                                stock.daily_close_history.append(stock.current_price)
                            else:
                                stock.previous_close = stock.current_price
                            
                            stock.update_fundamental_value()
                            stock.daily_script = self._generate_daily_script(stock, today)
                            logger.debug(f"{stock.stock_id} 前收: {stock.previous_close}")
                        
                        self.last_update_date = today

                    db_updates = []
                    current_interval_minute = (now.minute // 5) * 5
                    five_minute_start = now.replace(minute=current_interval_minute, second=0, microsecond=0)

                    for stock in self.stocks.values():
                        script = stock.daily_script
                        if not script: continue

                        # 在循环开始时，记录下当前周期的开盘价
                        open_price = stock.current_price
                        event_message = None # 初始化事件消息

                        # 1. 检查是否触发原生股票的随机事件
                        if not stock.is_listed_company:
                            # 此函数会直接修改 stock.current_price
                            event_message = await self._handle_native_stock_random_event(stock)

                        # 2. 根据是否发生事件，决定走哪条逻辑路径
                        if event_message:
                            # --- 事件发生路径 ---
                            # 此时，stock.current_price 已经是事件造成的新价格了
                            # 这就是您想要的“直接修改股价”的效果

                            # a. 广播事件消息
                            logger.info(f"[随机市场事件] {event_message}")
                            message_chain = MessageChain().message(f"【市场快讯】\n{event_message}")
                            subscribers_copy = list(self.broadcast_subscribers)
                            for umo in subscribers_copy:
                                try:
                                    await self.context.send_message(umo, message_chain)
                                except Exception as e:
                                    logger.error(f"向订阅者 {umo} 推送消息失败: {e}")
                                    if umo in self.broadcast_subscribers:
                                        self.broadcast_subscribers.remove(umo)

                            # b. 事件的价格就是最终价格，K线的高低点就是开盘和收盘
                            close_price = stock.current_price
                            high_price = max(open_price, close_price)
                            low_price = min(open_price, close_price)

                        else:
                            # --- 常规波动路径 (仅在没有事件发生时执行) ---
                            # ======================= 算法核心 V5.4 (微观波动增强) =======================
                            TREND_BREAK_CHANCE = 0.20
                            if stock.intraday_trend_duration <= 0 or random.random() < TREND_BREAK_CHANCE:
                                if script.bias == DailyBias.UP: weights = [0.5, 0.3, 0.2]
                                elif script.bias == DailyBias.DOWN: weights = [0.2, 0.3, 0.5]
                                else: weights = [0.3, 0.4, 0.3]
                                stock.intraday_trend = random.choices([Trend.BULLISH, Trend.NEUTRAL, Trend.BEARISH], weights=weights, k=1)[0]
                                stock.intraday_trend_duration = random.randint(4, 12)
                            else:
                                stock.intraday_trend_duration -= 1

                            effective_volatility = script.expected_range_factor / math.sqrt(288) * 2.2
                            trend_influence = stock.intraday_trend.value * (open_price * effective_volatility) * random.uniform(0.5, 1.5)
                            random_walk = open_price * effective_volatility * random.normalvariate(0, 1)

                            short_term_reversion_force = 0
                            if len(stock.price_history) >= 5:
                                sma5 = sum(list(stock.price_history)[-5:]) / 5
                                short_term_reversion_force = -(open_price - sma5) * 0.15

                            intraday_anchor_force = (script.target_close - open_price) / 288 * 0.05
                            pressure_influence = stock.market_pressure * 0.01
                            stock.market_pressure *= 0.95 #衰减系数
                            total_change = trend_influence + random_walk + short_term_reversion_force + intraday_anchor_force + pressure_influence

                            close_price_raw = open_price + total_change
                            close_price = round(max(0.01, close_price_raw), 2)
                            absolute_volatility_base = open_price * (script.expected_range_factor / math.sqrt(288))
                            high_price_raw = max(open_price, close_price) + random.uniform(0, absolute_volatility_base * 0.8)
                            low_price_raw = min(open_price, close_price) - random.uniform(0, absolute_volatility_base * 0.8)
                            high_price = round(high_price_raw, 2)
                            low_price = round(max(0.01, low_price_raw), 2)

                            stock.current_price = close_price
                            # ======================= 算法核心结束 =======================

                        # 3. 公共处理逻辑：无论哪种路径，都统一更新历史和数据库
                        stock.price_history.append(stock.current_price)
                        kline_entry = {"date": five_minute_start.isoformat(), "open": open_price, "high": high_price, "low": low_price, "close": stock.current_price}
                        stock.kline_history.append(kline_entry)
                        db_updates.append({"stock_id": stock.stock_id, "current_price": stock.current_price, "kline": kline_entry, "market_pressure": stock.market_pressure})

                    # 4. 批量写入数据库
                    if db_updates:
                        async with aiosqlite.connect(self.db_path) as db:
                            for data in db_updates:
                                await db.execute(
                                    "UPDATE stocks SET current_price = ?, market_pressure = ? WHERE stock_id = ?", 
                                    (data['current_price'], data['market_pressure'], data['stock_id']) # <--- 增加对应的值
                                )
                                k = data['kline']
                                await db.execute(
                                    "INSERT INTO kline_history (stock_id, timestamp, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?) "
                                    "ON CONFLICT(stock_id, timestamp) DO UPDATE SET open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close",
                                    (data['stock_id'], k['date'], k['open'], k['high'], k['low'], k['close'])
                                )
                            await db.commit()

                    # 5. 精准等待到下一个5分钟周期
                    now_after_update = datetime.now()
                    seconds_to_wait = (5 - (now_after_update.minute % 5)) * 60 - now_after_update.second
                    await asyncio.sleep(max(1, seconds_to_wait))

                except asyncio.CancelledError:
                    logger.info("股票价格更新任务被取消。")
                    break
                except Exception as e:
                    logger.error(f"股票价格更新任务出现严重错误: {e}", exc_info=True)
                    await asyncio.sleep(60)

    async def _execute_sell_order(self, user_id: str, stock_id: str, quantity_to_sell: int, current_price: float, return_data: bool = False) -> Any:
        """
        执行卖出操作的核心函数 (FIFO, T+60min, Fee, Slippage)
        """
        if quantity_to_sell <= 0:
            message = "❌ 卖出数量必须大于0。"
            if return_data: return False, message, None
            return False, message

        unlock_time = (datetime.now() - timedelta(minutes=SELL_LOCK_MINUTES)).isoformat()
        
        async with aiosqlite.connect(self.db_path) as db:
            # 1. 查询所有可卖出的持仓记录，按时间升序 (FIFO)
            cursor = await db.execute(
                "SELECT holding_id, quantity, purchase_price FROM holdings WHERE user_id=? AND stock_id=? AND purchase_timestamp <= ? ORDER BY purchase_timestamp ASC",
                (user_id, stock_id, unlock_time)
            )
            sellable_holdings = await cursor.fetchall()
            
            total_sellable_qty = sum(h[1] for h in sellable_holdings)
            if total_sellable_qty < quantity_to_sell:
                message = f"❌ 可卖数量不足！您只有 {total_sellable_qty} 股可卖，无法出售 {quantity_to_sell} 股。"
                if return_data: return False, message, None
                return False, message
            
            # 2. 计算并执行卖出
            remaining_to_sell = quantity_to_sell
            total_cost_basis = 0
            
            for holding_id, qty, price in sellable_holdings:
                if remaining_to_sell == 0: break
                
                sell_from_this_holding = min(remaining_to_sell, qty)
                total_cost_basis += sell_from_this_holding * price
                
                if sell_from_this_holding == qty: # 全部卖出此笔持仓
                    await db.execute("DELETE FROM holdings WHERE holding_id=?", (holding_id,))
                else: # 部分卖出
                    new_qty = qty - sell_from_this_holding
                    await db.execute("UPDATE holdings SET quantity=? WHERE holding_id=?", (new_qty, holding_id))
                
                remaining_to_sell -= sell_from_this_holding
            
            # 3. +++ 核心修改：计算滑点并应用 +++
            price_discount_percent = min(quantity_to_sell * SLIPPAGE_FACTOR, MAX_SLIPPAGE_DISCOUNT)
            actual_sell_price = current_price * (1 - price_discount_percent)
            gross_income = round(actual_sell_price * quantity_to_sell, 2)
            # +++ 修改结束 +++

            fee = round(gross_income * SELL_FEE_RATE, 2)
            net_income = gross_income - fee
            profit_loss = gross_income - total_cost_basis
            
            # 4. 更新用户余额
            await self.economy_api.add_coins(user_id, int(net_income), f"出售 {quantity_to_sell} 股 {self.stocks[stock_id].name}")
            
            ### 变化点：删除错误代码，使用正确逻辑计算压力 ###
            # 使用"混合模型"的正确代码 (基于交易总额 gross_income):
            pressure_generated = (gross_income ** 0.98) * COST_PRESSURE_FACTOR
            self.stocks[stock_id].market_pressure -= pressure_generated # 市场推动力
            ### ======================================= ###
            
            await db.commit()

        pnl_emoji = "🎉" if profit_loss > 0 else "😭" if profit_loss < 0 else "😐"
        
        slippage_info = f"(因大单抛售产生 {price_discount_percent:.2%} 滑点)\n" if price_discount_percent >= 0.001 else ""

        message = (f"✅ 卖出成功！{slippage_info}"
                   f"成交数量: {quantity_to_sell} 股\n"
                   f"当前市价: ${current_price:.2f}\n"
                   f"您的成交均价: ${actual_sell_price:.2f}\n"
                   f"成交总额: {gross_income:.2f} 金币\n"
                   f"手续费(1%): -{fee:.2f} 金币\n"
                   f"实际收入: {net_income:.2f} 金币\n"
                   f"{pnl_emoji} 本次交易盈亏: {profit_loss:+.2f} 金币")

        if return_data:
            return True, message, {
                "net_income": net_income, 
                "fee": fee, 
                "profit_loss": profit_loss,
                "slippage_percent": price_discount_percent
            }
        else:
            return True, message
            
    async def _internal_perform_buy(self, user_id: str, identifier: str, quantity: int) -> Tuple[bool, str]:
        """执行买入操作的核心内部函数，返回(是否成功, 消息)"""
        if self.market_status != MarketStatus.OPEN:
            return False, f"⏱️ 当前市场状态为【{self.market_status.value}】，无法交易。"

        if not self.economy_api:
            return False, "经济系统未启用，无法进行交易！"
            
        if quantity <= 0:
            return False, "❌ 购买数量必须是一个正整数。"

        stock = await self._find_stock(str(identifier))
        if not stock:
            return False, f"❌ 找不到标识符为 '{identifier}' 的股票。"
            
        cost = round(stock.current_price * quantity, 2)
        balance = await self.economy_api.get_coins(user_id)
        
        if balance < cost:
            return False, f"💰 金币不足！需要 {cost:.2f}，你只有 {balance:.2f}。"
        
        # 扣款
        success = await self.economy_api.add_coins(user_id, -int(cost), f"购买 {quantity} 股 {stock.name}")
        if not success:
            return False, "❗ 扣款失败，购买操作已取消。"

        # 写入数据库
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO holdings (user_id, stock_id, quantity, purchase_price, purchase_timestamp) VALUES (?, ?, ?, ?, ?)",
                (user_id, stock.stock_id, quantity, stock.current_price, datetime.now().isoformat())
            )
            await db.commit()
        
        # 核心修改：压力的基础不再是 quantity，而是 cost (交易总额)
        cost = quantity * stock.current_price 
        # 新的压力常数，因为 cost 的量级远大于 quantity，所以常数要变得更小
         
        pressure_generated = (cost ** 0.98) * COST_PRESSURE_FACTOR
        stock.market_pressure += pressure_generated

        message = (
            f"✅ 买入成功！\n"
            f"以 ${stock.current_price:.2f}/股 的价格买入 {quantity} 股 {stock.name}，花费 {cost:.2f} 金币。\n"
            f"⚠️ 注意：买入的股票将在 {SELL_LOCK_MINUTES} 分钟后解锁，方可卖出。"
        )
        return True, message

    async def _internal_perform_sell(self, user_id: str, identifier: str, quantity_to_sell: int) -> Tuple[bool, str, Optional[Dict]]:
        """执行卖出操作的核心内部函数，返回(是否成功, 消息, 附加数据)"""
        if self.market_status != MarketStatus.OPEN:
            return False, f"⏱️ 当前市场状态为【{self.market_status.value}】，无法交易。", None
        
        if not self.economy_api:
            return False, "经济系统未启用，无法进行交易！", None
        
        if quantity_to_sell <= 0:
            return False, "❌ 出售数量必须是一个正整数。", None

        stock = await self._find_stock(identifier)
        if not stock:
            return False, f"❌ 找不到标识符为 '{identifier}' 的股票。", None

        unlock_time_str = (datetime.now() - timedelta(minutes=SELL_LOCK_MINUTES)).isoformat()
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT SUM(quantity) FROM holdings WHERE user_id=? AND stock_id=? AND purchase_timestamp <= ?", 
                                      (user_id, stock.stock_id, unlock_time_str))
            result = await cursor.fetchone()
            total_sellable = result[0] if result and result[0] else 0
            
            if total_sellable < quantity_to_sell:
                cursor = await db.execute(
                    "SELECT MIN(purchase_timestamp) FROM holdings WHERE user_id=? AND stock_id=? AND purchase_timestamp > ?",
                    (user_id, stock.stock_id, unlock_time_str))
                next_purchase = await cursor.fetchone()
                hint = ""
                if next_purchase and next_purchase[0]:
                    unlock_dt = datetime.fromisoformat(next_purchase[0]) + timedelta(minutes=SELL_LOCK_MINUTES)
                    time_left = unlock_dt - datetime.now()
                    if time_left.total_seconds() > 0:
                        minutes, seconds = divmod(int(time_left.total_seconds()), 60)
                        hint = f"\n提示：下一批持仓大约在 {minutes}分{seconds}秒 后解锁。"
                return False, f"❌ 可卖数量不足！\n您想卖 {quantity_to_sell} 股，但只有 {total_sellable} 股可卖。{hint}", None

        # 调用简化后的卖出执行函数
        success, message, data = await self._execute_sell_order(
            user_id, 
            stock.stock_id, 
            quantity_to_sell, 
            stock.current_price, 
            return_data=True
        )
        return success, message, data
    # ----------------------------
    # 数据库与初始化方法 (已修改)
    # ----------------------------
    async def _initialize_database(self):
        """
        检查并初始化数据库。如果表或列不存在，则创建它们。
        """
        logger.info("正在检查并初始化数据库结构...")
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 1. 创建所有基础表结构 (使用 CREATE TABLE IF NOT EXISTS)
                
                # --- 用户表 (修正：增加了 login_id 用于登录) ---
                await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY NOT NULL,
                    login_id TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """)
                # 为 login_id 添加索引以加速登录查询
                await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_login_id ON users (login_id);")

                # --- 股票信息表 ---
                await db.execute("""
                CREATE TABLE IF NOT EXISTS stocks (
                    stock_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    current_price REAL NOT NULL,
                    volatility REAL NOT NULL DEFAULT 0.05,
                    industry TEXT NOT NULL DEFAULT '综合'
                );
                """)

                # --- K线历史数据表 ---
                await db.execute("""
                CREATE TABLE IF NOT EXISTS kline_history (
                    stock_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    PRIMARY KEY (stock_id, timestamp),
                    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
                );
                """)

                # --- 持仓记录表 (记录每一笔买入) ---
                await db.execute("""
                CREATE TABLE IF NOT EXISTS holdings (
                    holding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    stock_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    purchase_price REAL NOT NULL,
                    purchase_timestamp TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                """)
                # 为持仓查询添加索引
                await db.execute("CREATE INDEX IF NOT EXISTS idx_holdings_user_stock ON holdings (user_id, stock_id);")
                
                # --- 订阅表 ---
                await db.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    umo TEXT PRIMARY KEY NOT NULL
                );
                """)

                # 2. 安全地为 `stocks` 表添加所有需要的额外列
                # (这个集中的方法比多个if语句更清晰)
                await self._safe_add_columns(db, 'stocks', {
                    'is_listed_company': 'BOOLEAN NOT NULL DEFAULT 0',
                    'owner_id': 'TEXT',
                    'total_shares': 'INTEGER',
                    'market_pressure': 'REAL NOT NULL DEFAULT 0.0',
                    'fundamental_value': 'REAL'
                })

                await db.commit()
            logger.info("数据库初始化完成。")
        except Exception as e:
            logger.error(f"数据库初始化过程中发生严重错误: {e}", exc_info=True)
            raise

    # ++ 新增一个辅助函数来处理添加列的逻辑 ++
    async def _safe_add_columns(self, db, table_name, columns_to_add):
        """
        一个通用的辅助函数，安全地为指定表添加多个列。
        :param db: aiosqlite 数据库连接对象
        :param table_name: 要修改的表名
        :param columns_to_add: 一个字典 { '列名': '列的定义和约束' }
        """
        cursor = await db.execute(f"PRAGMA table_info({table_name})")
        existing_columns = {row[1] for row in await cursor.fetchall()}
        
        for col_name, col_definition in columns_to_add.items():
            if col_name not in existing_columns:
                logger.info(f"为表 `{table_name}` 添加新列: `{col_name}`")
                await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_definition}")

    async def _load_subscriptions_from_db(self):
        """从数据库加载所有订阅者到内存"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT umo FROM subscriptions")
                rows = await cursor.fetchall()
                self.broadcast_subscribers = {row[0] for row in rows}
                logger.info(f"成功从数据库加载 {len(self.broadcast_subscribers)} 个订阅者。")
        except Exception as e:
            logger.error(f"从数据库加载订阅者列表失败: {e}", exc_info=True)

    async def _load_stocks_from_db(self):
            """从数据库加载所有股票信息到内存 (包含初始数据和K线历史加载)"""
            async with aiosqlite.connect(self.db_path) as db:
                # +++ 修改 2：定义一个统一的、包含所有新字段的查询语句 +++
                query = "SELECT stock_id, name, current_price, volatility, industry, is_listed_company, owner_id, total_shares, market_pressure, fundamental_value FROM stocks"
                
                cursor = await db.execute(query)
                rows = await cursor.fetchall()

                if not rows:
                    logger.info("数据库为空，正在插入初始股票数据...")
                    initial_data = [
                        ('CY', '晨宇科技', 57, 0.020, '科技'), 
                        ('HL', '今州航空', 49, 0.0250, '航空'),
                        ('JD', '金盾安防', 44, 0.0300, '安防'), 
                        ('DL', '大立农业', 54, 0.0200, '农业'),
                        ('HK', '虎口矿业', 45, 0.0300, '矿业'), 
                        ('GH', '光合生物', 26, 0.0550, '生物'),
                    ]
                    # 为原生股票设置初始内在价值等于其价格
                    await db.executemany("INSERT INTO stocks (stock_id, name, current_price, volatility, industry, fundamental_value) VALUES (?, ?, ?, ?, ?, ?)", 
                                         [(d[0], d[1], d[2], d[3], d[4], d[2]) for d in initial_data])
                    await db.commit()
                    cursor = await db.execute(query)
                    rows = await cursor.fetchall()

                for row in rows:
                    # +++ 修改 3：解包所有10个字段 +++
                    stock_id, name, price, volatility, industry, is_listed, owner_id, total_shares, market_pressure, fundamental_value = row
                    
                    # 如果内在价值在数据库中为NULL（老数据），则默认为当前价格
                    if fundamental_value is None:
                        fundamental_value = price

                    stock = VirtualStock(
                        stock_id=stock_id, name=name, current_price=price,
                        volatility=volatility, industry=industry, 
                        fundamental_value=fundamental_value, # <--- 使用加载的值
                        previous_close=price,
                        is_listed_company=is_listed or False,
                        owner_id=owner_id,
                        total_shares=total_shares or 0,
                        market_pressure=market_pressure or 0.0
                    )
                    
                    # --- 后续加载K线历史的代码不变 ---
                    k_cursor = await db.execute(
                        "SELECT timestamp, open, high, low, close FROM kline_history WHERE stock_id = ? ORDER BY timestamp DESC LIMIT ?",
                        (stock_id, stock.kline_history.maxlen)
                    )
                    k_rows = await k_cursor.fetchall()
                    kline_data = [
                        {"date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]} 
                        for r in reversed(k_rows)
                    ]
                    stock.kline_history.extend(kline_data)
                    stock.price_history.extend([k['close'] for k in kline_data])
                    if not stock.price_history:
                        stock.price_history.append(price)
                    stock.daily_close_history.extend(list(stock.price_history)[-stock.daily_close_history.maxlen:])
                    
                    self.stocks[stock_id] = stock
                    
            logger.info(f"成功从数据库加载 {len(self.stocks)} 支股票。")

    # ----------------------------
    # Playwright 和 API 方法 (不变)
    # ----------------------------
    async def _start_playwright_browser(self):
        """启动并初始化 Playwright 浏览器实例"""
        try:
            p = await async_playwright().start()
            self.playwright_browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox"]
            )
            logger.info("Playwright 浏览器实例已成功启动。")
        except Exception as e:
            logger.error(f"启动 Playwright 浏览器失败: {e}. K线图功能将不可用。")
            self.playwright_browser = None

    async def _close_playwright_browser(self):
        """安全地关闭 Playwright 浏览器实例"""
        if self.playwright_browser and self.playwright_browser.is_connected():
            await self.playwright_browser.close()
            logger.info("Playwright 浏览器实例已关闭。")
          

    async def _find_stock(self, identifier: str) -> Optional[VirtualStock]:
        """统一的股票查找器，支持编号、代码、名称"""
        # 1. 按编号查找 (基于固定的代码排序)
        if identifier.isdigit():
            try:
                index = int(identifier) - 1
                sorted_stocks = sorted(self.stocks.values(), key=lambda s: s.stock_id)
                if 0 <= index < len(sorted_stocks):
                    return sorted_stocks[index]
            except (ValueError, IndexError):
                pass

        # 2. 按代码查找 (不区分大小写)
        stock = self.stocks.get(identifier.upper())
        if stock:
            return stock

        # 3. 按名称查找 (完全匹配)
        for s in self.stocks.values():
            if s.name == identifier:
                return s
        
        return None


    async def get_user_total_asset(self, user_id: str) -> Dict[str, Any]:
            """计算单个用户的总资产详情，供API调用 (已集成银行资产)"""
            stock_market_value = 0.0

            # 1. 计算股票市值
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute(
                        "SELECT stock_id, SUM(quantity) FROM holdings WHERE user_id = ? GROUP BY stock_id",
                        (user_id,)
                    )
                    holdings = await cursor.fetchall()

                    for stock_id, total_quantity in holdings:
                        stock = self.stocks.get(stock_id)
                        current_price = 0.0
                        if stock:
                            current_price = stock.current_price
                            stock_market_value += current_price * total_quantity
                        else:
                            # [保留] 这是一个重要警告，提示数据可能不一致
                            logger.warning(f"  -> 警告: 在数据库中找到持仓 {stock_id}，但在内存(self.stocks)中找不到该股票对象！")
            except Exception as e:
                # [保留] 数据库错误是关键问题
                logger.error(f"查询或计算持仓市值时发生数据库错误: {e}", exc_info=True)

            # 2. 获取现金余额
            coins = 0
            if self.economy_api:
                try:
                    coins = await self.economy_api.get_coins(user_id)
                except Exception as e:
                    # [保留] API调用错误是关键问题
                    logger.error(f"调用 economy_api.get_coins 时出错: {e}", exc_info=True)
            else:
                # [保留] 核心依赖缺失是重要警告
                logger.warning("economy_api 未加载，金币强制计为 0。")

            # 3. 获取公司资产
            company_assets = 0
            industry_api = shared_services.get("industry_api")
            if industry_api:
                try:
                    is_public_company_owner = False
                    public_company_market_cap = 0
                    for stock in self.stocks.values():
                        if stock.is_listed_company and stock.owner_id == user_id:
                            is_public_company_owner = True
                            public_company_market_cap = stock.current_price * stock.total_shares
                            break
                    
                    if is_public_company_owner:
                        company_assets = public_company_market_cap
                    else:
                        company_assets = await industry_api.get_company_asset_value(user_id)
                except Exception as e:
                    # [保留] API调用错误是关键问题
                    logger.error(f"调用 industry_api 时出错: {e}", exc_info=True)
            
            # 4. 获取银行资产和负债
            bank_deposits = 0.0
            bank_loans = 0.0
            # 注意：这里我们假设您已经采纳了之前的建议，在 plugin_init 中加载 self.bank_api
            # 如果没有，可以继续使用 bank_api = shared_services.get("bank_api")
            if self.bank_api: 
                try:
                    # 获取银行存款 (正资产)
                    bank_deposits = await self.bank_api.get_bank_asset_value(user_id)
                    # 获取银行贷款 (负债)
                    loan_info = await self.bank_api.get_loan_info(user_id)
                    if loan_info:
                        bank_loans = loan_info.get("amount_due", 0)
                except Exception as e:
                    # [保留] API调用错误是关键问题
                    logger.error(f"调用 bank_api 时出错: {e}", exc_info=True)

            # 5. 计算最终总资产
            final_total_assets = round(coins + stock_market_value + company_assets + bank_deposits - bank_loans, 2)
            
            # 6. 返回包含所有资产成分的字典
            return {
                "user_id": user_id,
                "total_assets": final_total_assets,
                "coins": coins,
                "stock_value": round(stock_market_value, 2),
                "company_assets": company_assets,
                "bank_deposits": bank_deposits,
                "bank_loans": bank_loans,
                "holdings_count": len(holdings) if 'holdings' in locals() else 0
            }

    async def get_total_asset_ranking(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        计算并获取总资产排行榜 (V2.1 - 精简日志版)。
        该版本集成了股票、现金、银行存款和公司资产，以提供最全面的排名。
        """
        if not self.economy_api:
            logger.error("无法计算总资产排行，因为经济系统API不可用。")
            return []
        candidate_user_ids = set()

        # 1. 获取所有持有股票的用户
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT DISTINCT user_id FROM holdings")
                for row in await cursor.fetchall():
                    candidate_user_ids.add(row[0])
        except Exception as e:
            logger.error(f"从 holdings 表获取用户时出错: {e}", exc_info=True)

        # 2. 获取现金排名前列的用户
        try:
            top_coin_users = await self.economy_api.get_ranking(limit=50)
            for user in top_coin_users:
                candidate_user_ids.add(user['user_id'])
        except Exception as e:
            logger.error(f"调用 economy_api.get_ranking 时出错: {e}", exc_info=True)

        # 3. 获取银行存款排名前列的用户
        bank_api = shared_services.get("bank_api")
        if bank_api:
            try:
                top_bank_users = await bank_api.get_top_accounts(limit=50)
                for user in top_bank_users:
                    candidate_user_ids.add(user['user_id'])
            except Exception as e:
                logger.error(f"调用 bank_api.get_top_accounts 时出错: {e}", exc_info=True)
        else:
            logger.warning("bank_api 未加载，总资产排行将不包含银行存款排行数据。")

        # 4. 获取公司资产价值排名前列的用户
        industry_api = shared_services.get("industry_api")
        if industry_api:
            try:
                top_companies = await industry_api.get_top_companies_by_value(limit=50)
                for company in top_companies:
                    candidate_user_ids.add(company['user_id'])
            except Exception as e:
                logger.error(f"调用 industry_api.get_top_companies_by_value 时出错: {e}", exc_info=True)
        else:
            logger.warning("industry_api 未加载，总资产排行将不包含公司资产排行数据。")
        
        candidate_user_ids.discard('1902929802')
        # 为候选池中的每一位用户计算总资产
        asset_tasks = [self.get_user_total_asset(uid) for uid in candidate_user_ids]
        all_asset_data = await asyncio.gather(*asset_tasks)

        # 过滤掉总资产为0或负数的用户
        valid_asset_data = [data for data in all_asset_data if data and data.get('total_assets', 0) > 0]
        
        # 按总资产排序并返回结果
        sorted_assets = sorted(valid_asset_data, key=lambda x: x['total_assets'], reverse=True)
        return sorted_assets[:limit]


    # ----------------------------
    # 用户指令 (User Commands)
    # ----------------------------
    @filter.command("股票列表", alias={"所有股票", "查询股票", "查看股票", "股票"})
    async def list_stocks(self, event: AstrMessageEvent):
        """查看当前市场所有可交易的股票"""
        if not self.stocks:
            yield event.plain_result("当前市场没有可交易的股票。")
            return
        
        reply = "--- 虚拟股票市场列表 ---\n"
        sorted_stocks = sorted(self.stocks.values(), key=lambda s: s.stock_id)
        
        for i, stock in enumerate(sorted_stocks, 1):
            price_change = 0.0
            price_change_percent = 0.0
            
            # 确保有足够的历史数据来计算涨跌幅
            if len(stock.price_history) > 1:
                # price_history[-1] 是当前价格的记录, price_history[-2] 是上一个周期的价格
                last_price = stock.price_history[-2]
                price_change = stock.current_price - last_price
                
                # 防止除以零的错误
                if last_price > 0:
                    price_change_percent = (price_change / last_price) * 100
            
            emoji = "📈" if price_change > 0 else "📉" if price_change < 0 else "➖"
            
            # 在价格后面添加格式化的涨跌幅百分比
            # :+.2f 会强制显示正负号，并保留两位小数
            reply += f"[{i}]{stock.stock_id.ljust(5)}{stock.name.ljust(6)}{emoji}${stock.current_price:<8.2f}({price_change_percent:+.2f}%)\n"
        
        reply += "----------------------\n"
        reply += "使用 /行情 <编号/代码/名称> 查看详细信息"
        yield event.plain_result(reply)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("股东列表", alias={"持股查询"})
    async def stock_holders(self, event: AstrMessageEvent, stock_identifier: str):
        """
        查询指定股票的持股用户列表及其详细盈亏信息。
        用法: /股东列表 [股票代码/名称]
        """
        # 1. 验证输入并查找股票
        if not stock_identifier:
            # 直接返回纯文本错误信息
            yield event.plain_result("❌ 请输入要查询的股票代码或名称。\n用法: `/股东列表 [股票代码/名称]`")
            return

        stock = await self._find_stock(stock_identifier)
        if not stock:
            yield event.plain_result(f"❌ 找不到股票 `'{stock_identifier}'`。请检查代码或名称是否正确。")
            return

        # 2. 从数据库查询该股票的所有持仓记录
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, quantity, purchase_price FROM holdings WHERE stock_id=?",
                (stock.stock_id,)
            )
            raw_holdings = await cursor.fetchall()

        if not raw_holdings:
            yield event.plain_result(f"ℹ️ 当前无人持有 **【{stock.name}】**。")
            return

        # 3. 按 user_id 聚合数据
        holders_data = {}
        for user_id, qty, price in raw_holdings:
            if user_id not in holders_data:
                holders_data[user_id] = {'quantity': 0, 'cost_basis': 0.0}
            holders_data[user_id]['quantity'] += qty
            holders_data[user_id]['cost_basis'] += qty * price

        # 4. 【核心修正V2：确保自定义昵称的最高优先级】
        user_ids = list(holders_data.keys())
        final_names = {}

        if self.economy_api:
            profile_tasks = [self.economy_api.get_user_profile(uid) for uid in user_ids]
            profiles = await asyncio.gather(*profile_tasks)
            for profile in profiles:
                if profile and profile.get('nickname'):
                    final_names[profile['user_id']] = profile['nickname']

        if self.nickname_api:
            custom_nicknames = await self.nickname_api.get_nicknames_batch(user_ids)
            final_names.update(custom_nicknames)

        # 5. 计算每个用户的盈亏详情
        holder_details_list = []
        for user_id, data in holders_data.items():
            display_name = final_names.get(user_id) or f"用户({user_id[:6]}...)"

            quantity = data['quantity']
            cost_basis = data['cost_basis']
            market_value = quantity * stock.current_price
            pnl = market_value - cost_basis
            pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

            holder_details_list.append({
                'name': display_name,
                'quantity': quantity,
                'market_value': market_value,
                'pnl': pnl,
                'pnl_percent': pnl_percent
            })

        # 6. 按持股数量从高到低排序
        sorted_holders = sorted(holder_details_list, key=lambda x: x['quantity'], reverse=True)

        # 7. 构建包含 Markdown 表格语法的字符串
        response_lines = [
            f"### 📊 【**{stock.name}** ({stock.stock_id})】股东盈亏榜",
            f"**当前价格:** `${stock.current_price:.2f}`",
            "| 排名 | 股东 | 持仓(股) | 市值 | 盈亏 | 盈亏比例 |",
            "| :--: | :--- | :---: | :---: | :---: | :---: |"
        ]

        rank = 1
        for holder in sorted_holders:
            pnl_emoji = "📈" if holder['pnl'] > 0 else "📉" if holder['pnl'] < 0 else "➖"
            pnl_str = f"{holder['pnl']:+.2f}"
            pnl_percent_str = f"{holder['pnl_percent']:+.2f}%"

            line = (
                f"| {rank} | **{holder['name']}** | {holder['quantity']} | `${holder['market_value']:.2f}` | {pnl_emoji} **{pnl_str}** | ({pnl_percent_str}) |"
            )
            response_lines.append(line)
            rank += 1
        
        markdown_text = "\n".join(response_lines)
        
        # 8. 【核心修改】将 Markdown 文本转换为图片并发送
        url = await self.text_to_image(markdown_text)
        yield event.image_result(url)


    @filter.command("行情", alias={"查看行情"})
    async def get_stock_price(self, event: AstrMessageEvent, identifier: Optional[str] = None):
        """查询指定股票的实时行情"""
        if identifier is None:
            yield event.plain_result("🤔 请输入需要查询的股票。\n正确格式: /行情 <编号/代码/名称>")
            return
        stock = await self._find_stock(str(identifier))
        if not stock:
            yield event.plain_result(f"❌ 找不到标识符为 '{identifier}' 的股票。")
            return
        
        k_history = stock.kline_history
        if len(k_history) < 2:
            yield event.plain_result(f"【{stock.name} ({stock.stock_id})】\n价格: ${stock.current_price:.2f}\n行情数据不足...")
            return

        # --- 基础价格计算 ---
        last_price = k_history[-2]['close']
        change = stock.current_price - last_price
        change_percent = (change / last_price) * 100 if last_price > 0 else 0
        emoji = "📈" if change > 0 else "📉" if change < 0 else "➖"
        
        # --- 增强信息计算 ---
        relevant_history = list(k_history)[-288:]
        day_high = max(k['high'] for k in relevant_history)
        day_low = min(k['low'] for k in relevant_history)
        day_open = relevant_history[0]['open']

        sma5_text = "数据不足"
        if len(k_history) >= 5:
            recent_closes = [k['close'] for k in list(k_history)[-5:]]
            sma5 = sum(recent_closes) / 5
            sma5_text = f"${sma5:.2f}"
            
        # --- 获取内部趋势状态 ---
        trend_map = {
            "BULLISH": "看涨",
            "BEARISH": "看跌",
            "NEUTRAL": "盘整"
        }
        # **核心修改点**: 将 stock.trend.name 改为 stock.intraday_trend.name
        current_trend_text = trend_map.get(stock.intraday_trend.name, "未知")

        # --- 重新组织回复信息 ---
        reply = (
            f"{emoji}【{stock.name} ({stock.stock_id})】行情\n"
            f"--------------------\n"
            f"现价: ${stock.current_price:.2f}\n"
            f"涨跌: ${change:+.2f} ({change_percent:+.2f}%) (较5min前)\n"
            f"--------------------\n"
            f"24h开盘: ${day_open:.2f}\n"
            f"24h最高: ${day_high:.2f}\n"
            f"24h最低: ${day_low:.2f}\n"
            f"5周期均线: {sma5_text}\n"
            f"--------------------\n"
            f"短期趋势: {current_trend_text}\n"
            f"所属行业: {stock.industry}"
        )
        yield event.plain_result(reply)

    @filter.command("K线", alias={"k线图", "k线", "K线图"})
    async def show_kline(self, event: AstrMessageEvent, identifier: Optional[str] = None):
        """显示指定股票的K线图 (仅最近24小时)"""
        if identifier == None:
            # 如果用户只输入了 "/行情" 而没有带参数，则返回帮助信息
            yield event.plain_result("🤔 请输入需要查询的股票。\n正确格式: /k线 <编号/代码/名称>")
            return
        stock = await self._find_stock(str(identifier))
        if not stock:
            yield event.plain_result(f"❌ 找不到标识符为 '{identifier}' 的股票。")
            return
            
        if not self.playwright_browser:
            yield event.plain_result("❌ 图表渲染服务当前不可用，请联系管理员。")
            return

        if len(stock.kline_history) < 2:
            yield event.plain_result(f"📈 {stock.name} 的K线数据不足，无法生成图表。")
            return

        yield event.plain_result(f"正在为 {stock.name} 生成最近24小时K线图，请稍候...")
        
        # 使用 jinja2 渲染 HTML 模板
        try:
            # === 新增：定义24小时的数据点数量 (按5分钟一次计算) ===
            POINTS_FOR_24H = 288 
            
            # === 新增：从完整的历史记录中只切片出最近24小时的数据 ===
            # Python的切片[-288:]即使总数不足288也会安全地返回所有可用数据
            kline_data_for_image = list(stock.kline_history)[-POINTS_FOR_24H:]

            template = jinja_env.get_template("kline_chart.html")
            
            # === 修改：使用切片后的数据和固定的时间周期描述 ===
            html_content = await template.render_async(
                stock_name=stock.name, stock_id=stock.stock_id,
                data_period=f"最近 24 小时", # 副标题固定为24小时
                stock_data=kline_data_for_image # 传递切片后的数据
            )
        except Exception as e:
            logger.error(f"渲染K线图模板失败: {e}")
            yield event.plain_result("❌ 渲染K线图模板失败。")
            return

        # 使用 playwright 将 HTML 转为图片 (后续部分保持不变)
        temp_html_path = os.path.join(DATA_DIR, f"temp_kline_{stock.stock_id}_{random.randint(1000,9999)}.html")
        screenshot_path = os.path.join(DATA_DIR, f"kline_{stock.stock_id}_{random.randint(1000,9999)}.png")
        
        try:
            with open(temp_html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            
            page = await self.playwright_browser.new_page(viewport={"width": 800, "height": 600})
            await page.goto(f"file://{os.path.abspath(temp_html_path)}")
            await page.wait_for_selector("#kline-chart", state="visible", timeout=20000)
            chart_element = await page.query_selector('#kline-chart')
            await chart_element.screenshot(path=screenshot_path)
            await page.close()
            
            yield event.image_result(screenshot_path)
        
        except PlaywrightError as e:
            logger.error(f"Playwright 生成K线图失败: {e}")
            yield event.plain_result("❌ 生成K线图时发生浏览器错误。")
        except Exception as e:
            logger.error(f"生成K线图过程中发生未知错误: {e}")
            yield event.plain_result("❌ 生成K线图失败，请稍后重试。")
        finally:
            # 清理临时文件
            if os.path.exists(temp_html_path): os.remove(temp_html_path)
            # 在 yield 之后，截图文件也应被清理
            if os.path.exists(screenshot_path): os.remove(screenshot_path)

    @filter.command("购买股票", alias={"买入","加仓"})
    async def buy_stock(self, event: AstrMessageEvent, identifier: Optional[str] = None, quantity_str: Optional[str] = None):
        """购买指定数量的股票 (T+60min)"""
        # --- 参数检查 ---
        if identifier is None or quantity_str is None:
            yield event.plain_result("🤔 指令格式错误。\n正确格式: /买入 <标识符> <数量>")
            return

        # --- 数量有效性检查 ---
        try:
            quantity = int(quantity_str)
            if quantity <= 0:
                yield event.plain_result("❌ 购买数量必须是一个正整数。")
                return
        except ValueError:
            yield event.plain_result("❌ 购买数量必须是一个有效的数字。")
            return

        # --- 原有逻辑：执行买入操作 ---
        user_id = event.get_sender_id()
        success, message = await self._internal_perform_buy(user_id, identifier, quantity)
        yield event.plain_result(message)


    @filter.command("出售", alias={"卖出","减仓","抛出"})
    async def sell_stock(self, event: AstrMessageEvent, identifier: Optional[str] = None, quantity_str: Optional[str] = None):
        """出售指定数量的股票 (T+60min & Fee)"""
        # --- 参数检查 ---
        if identifier is None or quantity_str is None:
            yield event.plain_result("🤔 指令格式错误。\n正确格式: /卖出 <标识符> <数量>")
            return

        # --- 数量有效性检查 ---
        try:
            quantity_to_sell = int(quantity_str)
            if quantity_to_sell <= 0:
                yield event.plain_result("❌ 出售数量必须是一个正整数。")
                return
        except ValueError:
            yield event.plain_result("❌ 出售数量必须是一个有效的数字。")
            return
            
        # --- 原有逻辑：执行卖出操作 ---
        user_id = event.get_sender_id()
        success, message, _ = await self._internal_perform_sell(user_id, identifier, quantity_to_sell)
        yield event.plain_result(message)

    @filter.command("全抛", alias={"全部抛出"})
    async def sell_all_stock(self, event: AstrMessageEvent, identifier: Optional[str] = None):
        """快捷指令：卖出单支股票的所有可卖持仓 (T+60min & Fee)"""
        # --- 参数检查 ---
        if identifier is None:
            yield event.plain_result("🤔 请输入需要抛售的股票。\n正确格式: /全抛 <编号/代码/名称>")
            return
        if self.market_status != MarketStatus.OPEN:
            yield event.plain_result(f"⏱️ 当前市场状态为【{self.market_status.value}】，无法交易。")
            return
            
        if not self.economy_api:
            yield event.plain_result("经济系统未启用，无法进行交易！")
            return

        stock = await self._find_stock(str(identifier))
        if not stock:
            yield event.plain_result(f"❌ 找不到标识符为 '{identifier}' 的股票。")
            return
            
        user_id = event.get_sender_id()
        unlock_time_str = (datetime.now() - timedelta(minutes=SELL_LOCK_MINUTES)).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            # 1. 查询该股票总的可卖数量
            cursor = await db.execute("SELECT SUM(quantity) FROM holdings WHERE user_id=? AND stock_id=? AND purchase_timestamp <= ?", 
                                      (user_id, stock.stock_id, unlock_time_str))
            result = await cursor.fetchone()
            quantity_to_sell = result[0] if result and result[0] else 0

            # 2. 如果可卖数量为0，进入新的提示逻辑
            if quantity_to_sell == 0:
                # 2a. 检查是否持有任何该股票（包括锁定的）
                cursor = await db.execute("SELECT 1 FROM holdings WHERE user_id=? AND stock_id=? LIMIT 1", (user_id, stock.stock_id))
                any_holdings = await cursor.fetchone()

                if not any_holdings:
                    yield event.plain_result(f"您当前未持有 {stock.name} 的股票。")
                    return
                else:
                    # 2b. 有持仓但均被锁定，计算解锁时间
                    cursor = await db.execute(
                        "SELECT MIN(purchase_timestamp) FROM holdings WHERE user_id=? AND stock_id=? AND purchase_timestamp > ?",
                        (user_id, stock.stock_id, unlock_time_str)
                    )
                    next_purchase = await cursor.fetchone()
                    
                    hint = ""
                    if next_purchase and next_purchase[0]:
                        purchase_dt = datetime.fromisoformat(next_purchase[0])
                        unlock_dt = purchase_dt + timedelta(minutes=SELL_LOCK_MINUTES)
                        time_left = unlock_dt - datetime.now()
                        if time_left.total_seconds() > 0:
                            minutes, seconds = divmod(int(time_left.total_seconds()), 60)
                            hint = f"\n提示：下一批持仓大约在 {minutes}分{seconds}秒 后解锁。"
                    
                    yield event.plain_result(f"您当前没有可供卖出的 {stock.name} 股票。{hint}")
                    return
        
        # 3. 如果可卖数量 > 0，正常执行卖出
        success, message = await self._execute_sell_order(user_id, stock.stock_id, quantity_to_sell, stock.current_price)
        yield event.plain_result(message)

    @filter.command("梭哈股票")
    async def buy_all_in(self, event: AstrMessageEvent, identifier: str):
        """快捷指令：用全部现金买入单支股票 (T+60min)"""
        if self.market_status != MarketStatus.OPEN:
            yield event.plain_result(f"⏱️ 当前市场状态为【{self.market_status.value}】，无法交易。")
            return

        if not self.economy_api:
            yield event.plain_result("经济系统未启用，无法进行交易！")
            return

        stock = await self._find_stock(identifier)
        if not stock:
            yield event.plain_result(f"❌ 找不到标识符为 '{identifier}' 的股票。")
            return
        
        if stock.current_price <= 0:
            yield event.plain_result("❌ 股价异常，无法购买。")
            return

        user_id = event.get_sender_id()
        balance = await self.economy_api.get_coins(user_id)
        
        if balance < stock.current_price:
            yield event.plain_result(f"💰 金币不足！\n股价为 ${stock.current_price:.2f}，而您只有 {balance:.2f} 金币，连一股都买不起。")
            return
            
        quantity_to_buy = int(balance // stock.current_price)
        if quantity_to_buy == 0:
            yield event.plain_result(f"💰 金币不足！\n股价为 ${stock.current_price:.2f}，而您只有 {balance:.2f} 金币，连一股都买不起。")
            return

        cost = round(stock.current_price * quantity_to_buy, 2)
        
        success = await self.economy_api.add_coins(user_id, -int(cost), f"梭哈 {quantity_to_buy} 股 {stock.name}")
        if not success:
            yield event.plain_result("❗ 扣款失败，梭哈操作已取消。")
            return

        # 插入到 holdings 表
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT INTO holdings (user_id, stock_id, quantity, purchase_price, purchase_timestamp) VALUES (?, ?, ?, ?, ?)",
                             (user_id, stock.stock_id, quantity_to_buy, stock.current_price, datetime.now().isoformat()))
            await db.commit()
        stock.market_pressure += (quantity_to_buy**0.98) * 0.0001 # 施加市场推动力    
        yield event.plain_result(
            f"💥 梭哈成功！\n以 ${stock.current_price:.2f}/股 的价格买入 {quantity_to_buy} 股 {stock.name}，all in {cost:.2f} 金币！\n"
            f"⚠️ 注意：买入的股票将在 {SELL_LOCK_MINUTES} 分钟后解锁，方可卖出。"
        )

    @filter.command("清仓", alias={"全部卖出"})
    async def sell_all_portfolio(self, event: AstrMessageEvent):
        """快捷指令：卖出所有持仓中可卖的股票"""
        if self.market_status != MarketStatus.OPEN:
            yield event.plain_result(f"⏱️ 当前市场状态为【{self.market_status.value}】，无法交易。")
            return

        if not self.economy_api:
            yield event.plain_result("经济系统未启用，无法进行交易！")
            return
        
        user_id = event.get_sender_id()
        unlock_time_str = (datetime.now() - timedelta(minutes=SELL_LOCK_MINUTES)).isoformat()
        
        async with aiosqlite.connect(self.db_path) as db:
            # 1. 检查是否有可卖的股票
            cursor = await db.execute(
                "SELECT stock_id, SUM(quantity) FROM holdings WHERE user_id=? AND purchase_timestamp <= ? GROUP BY stock_id",
                (user_id, unlock_time_str)
            )
            sellable_stocks = await cursor.fetchall()

            # 2. 如果没有任何可卖的股票，进入新的提示逻辑
            if not sellable_stocks:
                # 2a. 检查是否持有任何股票（包括锁定的）
                cursor = await db.execute("SELECT 1 FROM holdings WHERE user_id=? LIMIT 1", (user_id,))
                any_holdings = await cursor.fetchone()

                if not any_holdings:
                    # 情况A：真的没有任何持仓
                    yield event.plain_result("您当前没有任何持仓，无需清仓。")
                    return
                else:
                    # 情况B：有持仓，但全被锁定了
                    # 2b. 查找最早一笔被锁定的交易，计算解锁时间
                    cursor = await db.execute(
                        "SELECT MIN(purchase_timestamp) FROM holdings WHERE user_id=? AND purchase_timestamp > ?",
                        (user_id, unlock_time_str)
                    )
                    next_purchase_to_unlock = await cursor.fetchone()
                    
                    hint = ""
                    if next_purchase_to_unlock and next_purchase_to_unlock[0]:
                        try:
                            purchase_dt = datetime.fromisoformat(next_purchase_to_unlock[0])
                            unlock_dt = purchase_dt + timedelta(minutes=SELL_LOCK_MINUTES)
                            time_left = unlock_dt - datetime.now()
                            
                            if time_left.total_seconds() > 0:
                                minutes, seconds = divmod(int(time_left.total_seconds()), 60)
                                hint = f"\n提示：您最早的一笔持仓大约在 {minutes}分{seconds}秒 后解锁。"
                        except ValueError:
                            # 处理时间戳格式可能存在的问题
                            pass
                            
                    yield event.plain_result(f"您持有的股票尚未解锁（需等待{SELL_LOCK_MINUTES}分钟），当前没有可供卖出的持仓。{hint}")
                    return

            # 3. 如果有可卖的股票，执行正常的清仓逻辑
            total_net_income = 0
            total_profit_loss = 0
            total_fees = 0
            sell_details = []

            for stock_id, quantity_to_sell in sellable_stocks:
                stock = self.stocks.get(stock_id)
                if not stock: continue
                
                success, message, result_data = await self._execute_sell_order(
                    user_id, stock_id, quantity_to_sell, stock.current_price, return_data=True
                )
                
                if success:
                    total_net_income += result_data["net_income"]
                    total_profit_loss += result_data["profit_loss"]
                    total_fees += result_data["fee"]
                    pnl_str = f"盈亏 {result_data['profit_loss']:+.2f}"

                    # --- 在这里新增滑点信息处理 ---
                    slippage_percent = result_data.get("slippage_percent", 0)
                    slippage_text = ""
                    if slippage_percent >= 0.0001: # 仅当滑点有意义时显示
                        slippage_text = f" (含{slippage_percent:.2%}滑点)"
                    # --- 新增结束 ---

                    sell_details.append(f" - {stock.name}: {quantity_to_sell}股, 收入 {result_data['net_income']:.2f} ({pnl_str}){slippage_text}")

            if not sell_details:
                yield event.plain_result("清仓失败，未能成功卖出任何股票。")
                return

            pnl_emoji = "🎉" if total_profit_loss > 0 else "😭" if total_profit_loss < 0 else "😐"
            details_str = "\n".join(sell_details)
            
            yield event.plain_result(
                f"🗑️ 已清仓所有可卖持股！\n{details_str}\n--------------------\n"
                f"总收入: {total_net_income:.2f} 金币\n"
                f"总手续费: -{total_fees:.2f} 金币\n"
                f"{pnl_emoji} 总盈亏: {total_profit_loss:+.2f} 金币"
            )

    @filter.command("持仓", alias={"文字持仓"})
    async def portfolio_text(self, event: AstrMessageEvent):
        """查看我的个人持仓详情（纯文字版）"""
        user_id = event.get_sender_id()
        name = event.get_sender_name()

        # 1. 从 new holdings 表获取原始持仓数据
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT stock_id, quantity, purchase_price FROM holdings WHERE user_id=?", (user_id,))
            raw_holdings = await cursor.fetchall()

        if not raw_holdings:
            yield event.plain_result(f"{name}，你当前没有持仓。使用 '/股票列表' 查看市场。")
            return

        # 2. 在代码中聚合数据，按 stock_id 分组计算总数和总成本
        aggregated_holdings = {}
        for stock_id, qty, price in raw_holdings:
            if stock_id not in aggregated_holdings:
                aggregated_holdings[stock_id] = {'quantity': 0, 'cost_basis': 0}
            aggregated_holdings[stock_id]['quantity'] += qty
            aggregated_holdings[stock_id]['cost_basis'] += qty * price

        # 3. 基于聚合后的数据计算各项指标
        holdings_list_for_template = []
        total_market_value = 0
        total_cost_basis = 0
        
        for stock_id, data in aggregated_holdings.items():
            stock = self.stocks.get(stock_id)
            if not stock: 
                continue
            
            qty = data['quantity']
            cost_basis = data['cost_basis']
            
            price_change = stock.current_price - stock.price_history[-2] if len(stock.price_history) > 1 else 0
            emoji = "📈" if price_change > 0 else "📉" if price_change < 0 else "➖"

            market_value = qty * stock.current_price
            pnl = market_value - cost_basis
            pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0
            
            holdings_list_for_template.append({
                "name": stock.name,
                "quantity": qty,
                "pnl": pnl,
                "pnl_percent": pnl_percent,
                "emoji": emoji,
            })
            
            total_market_value += market_value
            total_cost_basis += cost_basis
        
        total_pnl = total_market_value - total_cost_basis
        total_pnl_percent = (total_pnl / total_cost_basis) * 100 if total_cost_basis > 0 else 0

        # 4. 格式化并返回文字信息
        response_lines = [f"📊 {name} 的持仓：\n----------------\n"]
        for holding in holdings_list_for_template:
            pnl_str = f"{holding['pnl']:+.2f}"
            pnl_percent_str = f"({holding['pnl_percent']:+.2f}%)"
            response_lines.append(f"{holding['emoji']} {holding['name']}: {holding['quantity']} 股, 盈亏: {pnl_str} {pnl_percent_str}")
        
        total_pnl_str = f"{total_pnl:+.2f}"
        total_pnl_percent_str = f"({total_pnl_percent:+.2f}%)"
        
        response_lines.append(f"\n----------------\n总市值: {total_market_value:.2f}")
        response_lines.append(f"总成本: {total_cost_basis:.2f}")
        response_lines.append(f"总盈亏: {total_pnl_str} {total_pnl_percent_str}")
        
        yield event.plain_result("\n".join(response_lines))


    @filter.command("持仓图", alias={"我的持仓", "持仓图片"})
    async def my_portfolio(self, event: AstrMessageEvent):
        """查看我的个人持仓详情（以图片卡片形式，失败时自动切换为文字版）"""
        user_id = event.get_sender_id()
        name = event.get_sender_name()

        # 1. 从 new holdings 表获取原始持仓数据
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT stock_id, quantity, purchase_price FROM holdings WHERE user_id=?", (user_id,))
            raw_holdings = await cursor.fetchall()

        if not raw_holdings:
            yield event.plain_result(f"{name}，你当前没有持仓。使用 '/股票列表' 查看市场。")
            return

        # 2. 在代码中聚合数据
        aggregated_holdings = {}
        for stock_id, qty, price in raw_holdings:
            if stock_id not in aggregated_holdings:
                aggregated_holdings[stock_id] = {'quantity': 0, 'cost_basis': 0}
            aggregated_holdings[stock_id]['quantity'] += qty
            aggregated_holdings[stock_id]['cost_basis'] += qty * price

        # 3. 基于聚合后的数据准备模板所需数据
        holdings_list_for_template = []
        total_market_value = 0
        total_cost_basis = 0
        
        for stock_id, data in aggregated_holdings.items():
            stock = self.stocks.get(stock_id)
            if not stock: 
                continue
            
            qty = data['quantity']
            cost_basis = data['cost_basis']
            avg_cost = cost_basis / qty if qty > 0 else 0
            
            price_change = stock.current_price - stock.price_history[-2] if len(stock.price_history) > 1 else 0
            emoji = "📈" if price_change > 0 else "📉" if price_change < 0 else "➖"
            
            market_value = qty * stock.current_price
            pnl = market_value - cost_basis
            pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0
            
            holdings_list_for_template.append({
                "name": stock.name, "stock_id": stock.stock_id,
                "quantity": qty, "avg_cost": avg_cost,
                "market_value": market_value, "pnl": pnl,
                "pnl_percent": pnl_percent, "is_positive": pnl >= 0,
                "emoji": emoji,
            })
            
            total_market_value += market_value
            total_cost_basis += cost_basis
        
        total_pnl = total_market_value - total_cost_basis
        total_pnl_percent = (total_pnl / total_cost_basis) * 100 if total_cost_basis > 0 else 0

        # 4. 尝试生成图片卡片
        if self.playwright_browser:
            try:
                template_data = {
                    "user_name": name,
                    "holdings": holdings_list_for_template,
                    "total": {
                        "market_value": total_market_value, "pnl": total_pnl,
                        "pnl_percent": total_pnl_percent, "is_positive": total_pnl >= 0,
                    }
                }
                template = jinja_env.get_template("portfolio_card.html")
                html_content = await template.render_async(template_data)
                
                temp_html_path = os.path.join(DATA_DIR, f"temp_portfolio_{user_id}_{random.randint(1000,9999)}.html")
                screenshot_path = os.path.join(DATA_DIR, f"portfolio_{user_id}_{random.randint(1000,9999)}.png")

                with open(temp_html_path, "w", encoding="utf-8") as f: 
                    f.write(html_content)
                
                page = await self.playwright_browser.new_page()
                await page.goto(f"file://{os.path.abspath(temp_html_path)}")
                await page.locator('.card').screenshot(path=screenshot_path)
                await page.close()
                
                yield event.image_result(screenshot_path)
                return
            except Exception as e:
                logger.error(f"生成持仓卡片失败: {e}")
            finally:
                if 'temp_html_path' in locals() and os.path.exists(temp_html_path): 
                    os.remove(temp_html_path)
                if 'screenshot_path' in locals() and os.path.exists(screenshot_path): 
                    os.remove(screenshot_path)

        # 如果图片卡片生成失败或浏览器不可用，则返回文字版持仓信息
        response_lines = [f"📊 {name} 的持仓：\n----------------\n"]
        for holding in holdings_list_for_template:
            pnl_str = f"{holding['pnl']:+.2f}"
            pnl_percent_str = f"({holding['pnl_percent']:+.2f}%)"
            response_lines.append(f"{holding['emoji']} {holding['name']}: {holding['quantity']} 股, 盈亏: {pnl_str} {pnl_percent_str}")
        
        total_pnl_str = f"{total_pnl:+.2f}"
        total_pnl_percent_str = f"({total_pnl_percent:+.2f}%)"
        
        response_lines.append(f"\n----------------\n总市值: {total_market_value:.2f}")
        response_lines.append(f"总成本: {total_cost_basis:.2f}")
        response_lines.append(f"总盈亏: {total_pnl_str} {total_pnl_percent_str}")
        
        yield event.plain_result("\n".join(response_lines))

    # ----------------------------
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("添加股票")
    async def admin_add_stock(self, event: AstrMessageEvent, stock_id: str, name: str, initial_price: float, volatility: float = 0.05, industry: str = "综合"):
        """[管理员] 添加一支新的虚拟股票"""
        stock_id = stock_id.upper()
        if stock_id in self.stocks:
            yield event.plain_result(f"❌ 添加失败：股票代码 {stock_id} 已存在。")
            return
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO stocks (stock_id, name, current_price, volatility, industry) VALUES (?, ?, ?, ?, ?)",
                (stock_id, name, initial_price, volatility, industry)
            )
            await db.commit()

        stock = VirtualStock(stock_id=stock_id, name=name, current_price=initial_price, volatility=volatility, industry=industry)
        stock.price_history.append(initial_price)
        self.stocks[stock_id] = stock
        
        yield event.plain_result(f"✅ 成功添加股票: {name} ({stock_id})")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除股票")
    async def admin_del_stock(self, event: AstrMessageEvent, identifier: str):
        """[管理员] 删除一支股票及其所有相关数据"""
        stock = await self._find_stock(identifier)
        if not stock:
            yield event.plain_result(f"❌ 删除失败：找不到标识符为 '{identifier}' 的股票。")
            return
        
        stock_id = stock.stock_id
        async with aiosqlite.connect(self.db_path) as db:
            # 使用了外键并设置了 ON DELETE CASCADE，所以只需要删除 stocks 表中的记录
            await db.execute("DELETE FROM stocks WHERE stock_id = ?", (stock_id,))
            await db.commit()
        
        del self.stocks[stock_id]
        yield event.plain_result(f"🗑️ 已成功删除股票 {stock.name} ({stock_id}) 及其所有持仓和历史数据。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("设置股票趋势")
    async def admin_set_trend(self, event: AstrMessageEvent, identifier: str, trend_str: str, duration: int):
        """[管理员] 强制设定股票在未来一段时间的趋势"""
        stock = await self._find_stock(identifier)
        if not stock:
            yield event.plain_result(f"❌ 操作失败：找不到标识符为 '{identifier}' 的股票。")
            return
        
        try:
            # 将输入的字符串转换为 Trend 枚举成员
            trend_mapping = {
                "看涨": Trend.BULLISH,
                "看跌": Trend.BEARISH,
                "盘整": Trend.NEUTRAL,
                "BULLISH": Trend.BULLISH,  # 英文也支持
                "BEARISH": Trend.BEARISH,
                "NEUTRAL": Trend.NEUTRAL,
            }
 
            trend_enum = trend_mapping.get(trend_str)
            if trend_enum is None:
                yield event.plain_result("❌ 无效的趋势！请输入 `看涨`, `看跌`, `盘整`, `BULLISH`, `BEARISH`, 或 `NEUTRAL`。")
                return
 
            if duration <= 0:
                yield event.plain_result("❌ 持续时间（分钟）必须为正整数。")
                return
 
            # 计算延迟生效的时间
            delay_minutes = 5
            生效时间 = datetime.now() + timedelta(minutes=delay_minutes)
 
            # 将持续时间转换为 5 分钟的 tick 数
            duration_in_ticks = duration // 5  # 整数除法，向下取整
            if duration % 5 != 0:
                duration_in_ticks += 1  # 如果不能整除，则向上取整，确保至少持续指定的时间
 
            async def apply_trend():
                """延迟应用趋势的协程"""
                # 检查当前时间是否已经到达生效时间
                等待时间 = (生效时间 - datetime.now()).total_seconds()
                if 等待时间 > 0:
                    await asyncio.sleep(等待时间)
 
                # 修改与 V5.3 算法对应的日内趋势变量
                stock.intraday_trend = trend_enum
                stock.intraday_trend_duration = duration_in_ticks
                print(f"趋势已于 {datetime.now()} 生效") # 打印生效时间
 
            # 创建一个异步任务来延迟应用趋势
            asyncio.create_task(apply_trend())
            
            yield event.plain_result(f"✅ 操作成功！\n已将 {stock.name} 的趋势强制设定为 {trend_str.lower()}，将在 {delay_minutes} 分钟后生效，持续约 {duration} 分钟。")
        except KeyError:
            yield event.plain_result("❌ 无效的趋势！请输入 `看涨`, `看跌`, `盘整`, `BULLISH`, `BEARISH`, 或 `NEUTRAL`。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("修改股票")
    async def admin_modify_stock(self, event: AstrMessageEvent, identifier: str, param: str, value: str):
        """[管理员] 修改现有股票的参数。用法: /修改股票 <标识符> <参数> <新值>"""
        
        stock = await self._find_stock(identifier)
        if not stock:
            yield event.plain_result(f"❌ 操作失败：找不到标识符为 '{identifier}' 的股票。")
            return

        param = param.lower()
        old_stock_id = stock.stock_id
        
        # --- 修改股票名称 ---
        if param in ("name", "名称"):
            new_name = value
            if new_name == stock.name:
                yield event.plain_result(f"ℹ️ 新名称与旧名称相同，无需修改。")
                return
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("UPDATE stocks SET name = ? WHERE stock_id = ?", (new_name, old_stock_id))
                await db.commit()
            stock.name = new_name
            yield event.plain_result(f"✅ 成功将股票 {old_stock_id} 的名称修改为: {new_name}")

        # --- 修改股票代码 (核心操作) ---
        elif param in ("stock_id", "股票代码","代码") :
            new_stock_id = value.upper()
            if new_stock_id == old_stock_id:
                yield event.plain_result(f"ℹ️ 新代码与旧代码相同，无需修改。")
                return
            if new_stock_id in self.stocks:
                yield event.plain_result(f"❌ 操作失败：新的股票代码 {new_stock_id} 已存在！")
                return
            
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    # 暂时禁用外键约束以更新所有关联表
                    await db.execute("PRAGMA foreign_keys = OFF")
                    await db.execute("BEGIN TRANSACTION")
                    
                    await db.execute("UPDATE stocks SET stock_id = ? WHERE stock_id = ?", (new_stock_id, old_stock_id))
                    # **核心改动**: 更新 holdings 表
                    await db.execute("UPDATE holdings SET stock_id = ? WHERE stock_id = ?", (new_stock_id, old_stock_id))
                    await db.execute("UPDATE kline_history SET stock_id = ? WHERE stock_id = ?", (new_stock_id, old_stock_id))
                    
                    await db.execute("COMMIT")
                    await db.execute("PRAGMA foreign_keys = ON")
                
                # 数据库操作成功后，再更新内存
                stock.stock_id = new_stock_id
                self.stocks[new_stock_id] = self.stocks.pop(old_stock_id)
                yield event.plain_result(f"✅ 成功将股票代码 {old_stock_id} 修改为: {new_stock_id}，所有关联数据已同步更新。")

            except Exception as e:
                logger.error(f"修改股票代码时发生数据库错误: {e}")
                yield event.plain_result(f"❌ 修改股票代码时发生数据库错误，操作已取消。")

        # --- 修改其他参数 ---
        elif param in ("industry", "行业"):
            stock.industry = value
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("UPDATE stocks SET industry = ? WHERE stock_id = ?", (value, old_stock_id))
                await db.commit()
            yield event.plain_result(f"✅ 成功将股票 {old_stock_id} 的行业修改为: {value}")
            
        elif param in ("volatility", "波动率"):
            try:
                new_vol = float(value)
                if new_vol <= 0:
                    yield event.plain_result("❌ 波动率必须是大于0的数字。")
                    return
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("UPDATE stocks SET volatility = ? WHERE stock_id = ?", (new_vol, old_stock_id))
                    await db.commit()
                stock.volatility = new_vol
                yield event.plain_result(f"✅ 成功将股票 {old_stock_id} 的波动率修改为: {new_vol:.4f}")
            except ValueError:
                yield event.plain_result("❌ 波动率必须是有效的数字。")
        
        else:
            yield event.plain_result(f"❌ 未知的参数: '{param}'。\n可用参数: `name`, `stock_id`, `industry`, `volatility`")

    @filter.command("炒股帮助", alias={"股票帮助","stock_help"})
    async def show_plugin_help(self, event: AstrMessageEvent):
        """显示本插件的所有指令帮助"""
        
        help_text = """
        --- 📈 模拟炒股插件帮助 📉 ---
        
【基础指令】
/股票 - 查看所有可交易的股票
/行情 <编号/代码/名称> - 查询股票行情
/K线 <编号/代码/名称> - 显示股票K线图
/持仓（图） - 查看您的个人持仓详情（图片）
/webk - 在线网页K线图及持仓信息(推荐)

/资产 - 查看您的当前总资产
【交易指令】
/买入 <标识符> <数量> - 买入指定数量股票
/卖出 <标识符> <数量> - 卖出指定数量股票

【快捷指令】
/梭哈股票 <标识符> - 用全部现金买入该股票
/全抛 <标识符> - 卖出该股票的全部持仓
/清仓 - 卖出您持有的所有股票

【管理员指令】
/添加股票 <代码> <名称> <价格> [波动率] [行业]
/删除股票 <标识符>
"""
        msg = help_text.strip()
        msg = self.forwarder.create_from_text(msg)

        yield event.chain_result([msg])
# """
# 修改股票名称

# /修改股票 ASTR name 星尘宇宙集团

# ✅ 成功将股票 ASTR 的名称修改为: 星尘宇宙集团

# 修改股票代码 (请谨慎操作)

# /修改股票 ASTR stock_id ASTR-U

# ✅ 成功将股票代码 ASTR 修改为: ASTR-U，所有关联数据已同步更新。

# 修改其他参数 (一并提供，方便统一管理)

# /修改股票 ASTR industry 宇宙科技   #行业

# /修改股票 ASTR volatility 0.045  #波动率

# """

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("设置股价", alias={"修改股价"})
    async def admin_set_price(self, event: AstrMessageEvent, identifier: str, new_price: float):
        """[管理员] 强制修改指定股票的当前价格"""
        # 1. 基础验证
        if new_price <= 0:
            yield event.plain_result("❌ 价格必须是一个正数。")
            return

        stock = await self._find_stock(identifier)
        if not stock:
            yield event.plain_result(f"❌ 操作失败：找不到标识符为 '{identifier}' 的股票。")
            return

        old_price = stock.current_price
        stock_id = stock.stock_id

        # 2. 更新内存中的价格
        #    直接修改当前价，并追加到价格历史中，让下一次模拟以此为基准
        stock.current_price = new_price
        stock.price_history.append(new_price)

        # 3. 更新数据库中的价格，确保持久化
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE stocks SET current_price = ? WHERE stock_id = ?", (new_price, stock_id))
            await db.commit()
        # 4. 发送成功确认信息
        yield event.plain_result(
            f"✅ 操作成功！\n"
            f"已将股票 {stock.name} ({stock_id}) 的价格\n"
            f"从 ${old_price:.2f} 强制修改为 ${new_price:.2f}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("股票详情", alias={"查询股票参数"})
    async def admin_stock_details(self, event: AstrMessageEvent, identifier: str):
        """[管理员] 查看股票的所有内部详细参数"""
        stock = await self._find_stock(identifier)
        if not stock:
            yield event.plain_result(f"❌ 操作失败：找不到标识符为 '{identifier}' 的股票。")
            return

        # 收集所有需要展示的内部参数
        details = (
            f"--- 股票内部参数详情 ---\n"
            f"股票名称: {stock.name}\n"
            f"股票代码: {stock.stock_id}\n"
            f"所属行业: {stock.industry}\n"
            f"--------------------\n"
            f"当前价格: ${stock.current_price:.2f}\n"
            f"波动率 (volatility): {stock.volatility:.4f}\n"
            f"--------------------\n"
            f"日内趋势: {stock.intraday_trend.name}\n"  
            f"趋势剩余Tick: {stock.intraday_trend_duration}\n" 
            f"--------------------\n"
            f"内存记录:\n"
            f" - 价格历史点: {len(stock.price_history)} / {stock.price_history.maxlen}\n"
            f" - K线历史点: {len(stock.kline_history)} / {stock.kline_history.maxlen}"
        )

        yield event.plain_result(details)

    async def get_user_asset_rank(self, target_user_id: str) -> tuple[int | str, int]:
        """
        [新版] 获取单个用户的资产排名和总上榜人数 (利用现有的 get_total_asset_ranking API)。
        """
        # 调用您现有的方法获取一个足够长的排行榜，以确保目标用户在其中。
        # 通过设置一个超大的 limit 值，我们实际上就获取了完整的排行榜。
        try:
            full_ranking = await self.get_total_asset_ranking(limit=999999)
        except Exception as e:
            logger.error(f"调用 get_total_asset_ranking 获取完整排行时出错: {e}", exc_info=True)
            return "查询失败", 0

        total_players = len(full_ranking)
        if total_players == 0:
            return "未上榜", 0

        # 在返回的榜单中查找目标用户
        for i, user_data in enumerate(full_ranking):
            # 使用 .get() 方法以避免因缺少 'user_id' 键而引发错误
            if user_data.get("user_id") == target_user_id:
                return i + 1, total_players  # 返回排名 (索引+1) 和总人数
        
        return "未上榜", total_players  # 如果用户不在榜上（例如总资产为0）

    @filter.command("总资产", alias={'资产'})
    async def my_total_asset(self, event: AstrMessageEvent):
        """查询当前用户或@用户的个人总资产详情 (金币+股票+公司+银行)，并显示其全服排名"""
        try:
            # ID获取逻辑 (保持不变)
            target_user_id = None
            for component in event.message_obj.message:
                if isinstance(component, Comp.At):
                    target_user_id = str(component.qq)
                    break
            if not target_user_id:
                target_user_id = event.get_sender_id()

            # 并行获取资产详情和排名 (逻辑不变)
            asset_details_task = self.get_user_total_asset(target_user_id)
            asset_rank_task = self.get_user_asset_rank(target_user_id)
            
            asset_details, (rank, total_players) = await asyncio.gather(
                asset_details_task,
                asset_rank_task
            )

            if not asset_details:
                yield event.plain_result("未能查询到该用户的资产信息。")
                return

            # --- 核心修改部分 开始 ---

            # 数据提取 (新增 bank_deposits 和 bank_loans)
            total_assets = asset_details.get("total_assets", 0)
            coins = asset_details.get("coins", 0)
            stock_value = asset_details.get("stock_value", 0)
            company_assets = asset_details.get("company_assets", 0)
            bank_deposits = asset_details.get("bank_deposits", 0) # <--- 新增
            bank_loans = asset_details.get("bank_loans", 0)       # <--- 新增

            # 输出格式化 (逻辑不变)
            is_self_query = (target_user_id == event.get_sender_id())
            display_name = target_user_id
            if self.nickname_api:
                custom_nickname = await self.nickname_api.get_nickname(target_user_id)
                if custom_nickname:
                    display_name = custom_nickname
            if is_self_query and display_name == target_user_id:
                display_name = event.get_sender_name()

            title = "💰 您的个人资产报告 💰" if is_self_query else f"💰 {display_name} 的资产报告 💰"
            rank_text = f"🏆 资产排名: {rank} " if isinstance(rank, int) else f"🏆 资产排名: {rank}"

            # 结果文本 (新增“银行存款”和“银行贷款”两行)
            result_text = (
                f"{title}\n"
                f"--------------------\n"
                f"🪙 现金余额: {coins:,.2f}\n"
                f"📈 股票市值: {stock_value:,.2f}\n"
                f"🏢 公司资产: {company_assets:,.2f}\n"
                f"💳 银行存款: {bank_deposits:,.2f}\n"  # <--- 新增
                f"🚨 银行贷款: {bank_loans:,.2f}\n"     # <--- 新增
                f"--------------------\n"
                f"🏦 总计资产: {total_assets:,.2f}\n"
                f"{rank_text}"
            )
            
            # --- 核心修改部分 结束 ---

            yield event.plain_result(result_text)

        except Exception as e:
            logger.error(f"查询用户 {event.get_sender_id()} 的总资产失败: {e}", exc_info=True)
            yield event.plain_result("查询资产失败了喵~ 可能是服务出了点小问题。")

    @filter.command("总资产排行", alias={'资产榜', '资产排行'})
    async def total_asset_ranking(self, event: AstrMessageEvent):
        """查看总资产排行榜 (金币+股票)"""
        if not self.economy_api:
            yield event.plain_result("错误：经济系统未连接，无法计算总资产排行榜。")
            return

        try:
            # 直接调用公开的API实现方法
            ranking_data = await self.get_total_asset_ranking(limit=20)
            
            header = "🏆 宇宙总资产排行榜 🏆\n--------------------\n"
            if not ranking_data:
                yield event.plain_result("现在还没有人进行投资，快来成为股神第一人！")
                return

            user_ids_on_ranking = [row['user_id'] for row in ranking_data]
            custom_nicknames = {}
            if self.nickname_api:
                custom_nicknames = await self.nickname_api.get_nicknames_batch(user_ids_on_ranking)

            fallback_nicknames = {}
            profiles = await asyncio.gather(*[self.economy_api.get_user_profile(uid) for uid in user_ids_on_ranking if uid not in custom_nicknames])
            for profile in profiles:
                if profile:
                    fallback_nicknames[profile['user_id']] = profile.get('nickname')
            
            entries = []
            for i, row in enumerate(ranking_data, 1):
                user_id = row['user_id']
                display_name = custom_nicknames.get(user_id) or fallback_nicknames.get(user_id) or user_id
                
                # 【修改】使用新的格式化函数来处理总资产的显示
                formatted_assets = format_large_number(row['total_assets'])
                
                entries.append(
                    f"🏅 第 {i} 名: {display_name}   总资产: {formatted_assets}"
                )

            result_text = header + "\n".join(entries)
            yield event.plain_result(result_text)

        except Exception as e:
            logger.error(f"获取总资产排行榜失败: {e}", exc_info=True)
            yield event.plain_result("排行榜不见了喵~ 可能是服务出了点小问题。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("列出所有股票", alias={"所有股票"})
    async def admin_list_db_stocks(self, event: AstrMessageEvent):
        """[管理员] 从数据库中查询并列出所有股票的详细信息。"""
        
        # 为了获取初始价格，我们使用子查询找到每个股票最早的开盘价
        # 注意：这假设股票被添加后至少产生了一条K线数据
        query = """
            SELECT
                s.stock_id,
                s.name,
                (SELECT open FROM kline_history WHERE stock_id = s.stock_id ORDER BY timestamp ASC LIMIT 1) AS initial_price,
                s.current_price,
                s.volatility,
                s.industry
            FROM
                stocks s
            ORDER BY
                s.stock_id ASC
        """

        async with aiosqlite.connect(self.db_path) as db:
            # 使用 aiosqlite 的 execute_fetchall 方法获取所有结果
            try:
                stock_data = await db.execute_fetchall(query)
            except aiosqlite.Error as e:
                logger.error(f"查询数据库股票列表时出错: {e}")
                yield event.plain_result(f"❌ 查询数据库时出错，请检查日志。")
                return

        if not stock_data:
            yield event.plain_result("数据库中没有任何股票信息。")
            return

        # 准备格式化输出
        response_lines = []
        # 表头
        # 使用全角空格来帮助对齐中文
        header = f"{'代码':<8}{'名称':<12}{'初始价':<10}{'当前价':<10}{'波动率':<10}{'行业'}"
        response_lines.append(header)
        response_lines.append("-" * 55)

        # 表内容
        for row in stock_data:
            stock_id, name, initial_price, current_price, volatility, industry = row
            
            # 处理可能为空的初始价格
            initial_p_str = f"{initial_price:<10.2f}" if initial_price is not None else f"{'N/A':<10}"

            line = (
                f"{stock_id:<8}"
                f"{name:<12}"
                f"{initial_p_str}"
                f"{current_price:<10.2f}"
                f"{volatility:<10.4f}"
                f"{industry}"
            )
            response_lines.append(line)

        # 将整个表格包裹在代码块中以保持格式
        full_response = "```\n" + "\n".join(response_lines) + "\n```"
        yield event.plain_result(full_response)


    @filter.command("webK线", alias={"webk", "webk线", "webK线图"})
    async def show_kline_chart_web(self, event: AstrMessageEvent, identifier: Optional[str] = None):
        """显示所有股票的K线图Web版，可指定默认显示的股票，并为用户生成专属链接"""
        if not self.web_app:
            yield event.plain_result("❌ Web服务当前不可用，请联系管理员。")
            return

        user_id = event.get_sender_id()
        current_user_hash = generate_user_hash(user_id) # 生成当前用户的哈希

        # 修改 base_url，包含用户哈希
        base_url = f"https://stock.leewater.online/charts/{current_user_hash}"
        
        # 如果用户指定了股票，就通过URL hash定位
        if identifier:
            stock = await self._find_stock(identifier)
            if not stock:
                yield event.plain_result(f"❌ 找不到标识符为 '{identifier}' 的股票。")
                return
            
            chart_url = f"{base_url}#{stock.stock_id}"
            message = f"📈 已为您生成【{stock.name}】的实时K线图页面，点击链接查看，您可在此页面自由切换其他股票并查看专属持仓信息：\n{chart_url}"
        else:
            # 如果用户没指定，直接给主页链接 (仍然包含用户哈希)
            chart_url = base_url
            message = f"📈 已为您生成您的专属实时K线图页面，请点击链接查看所有股票和您的持仓信息：\n{chart_url}"
            
        yield event.plain_result(message)

    @filter.command("验证")
    async def verify_registration(self, event: AstrMessageEvent, code: str):
        """接收验证码，完成账户的注册和绑定 (支持自定义登录名)"""
        pending_data = self.pending_verifications.get(code)
        
        if not pending_data or (datetime.now() - pending_data['timestamp']) > timedelta(minutes=5):
            if code in self.pending_verifications: del self.pending_verifications[code]
            yield event.plain_result("❌ 无效或已过期的验证码。")
            return
            
        qq_user_id = event.get_sender_id()
        login_id = pending_data['login_id']
        password_hash = pending_data['password_hash']
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (qq_user_id,))
            if await cursor.fetchone():
                yield event.plain_result("✅ 您的QQ号已经绑定了网页账户，无需重复验证。")
                del self.pending_verifications[code]
                return

            await db.execute(
                "INSERT INTO users (login_id, password_hash, user_id, created_at) VALUES (?, ?, ?, ?)",
                (login_id, password_hash, qq_user_id, datetime.now().isoformat())
            )
            await db.commit()

        del self.pending_verifications[code]
        logger.info(f"用户 {qq_user_id} 成功将网页账户 '{login_id}' 与其绑定。")
        yield event.plain_result(f"🎉 恭喜！您的网页账户 '{login_id}' 已成功激活并与您的QQ绑定！现在可以返回网页登录了。")

    @filter.command("订阅股票", alias={"订阅市场"})
    async def subscribe_news(self, event: AstrMessageEvent):
        """订阅随机市场事件快讯"""
        umo = event.unified_msg_origin
        if umo in self.broadcast_subscribers:
            yield event.plain_result("✅ 您已订阅市场快讯，无需重复操作。")
        else:
            # --- 核心修改：写入数据库 ---
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("INSERT INTO subscriptions (umo) VALUES (?)", (umo,))
                    await db.commit()
                
                self.broadcast_subscribers.add(umo)
                logger.info(f"新的订阅者已添加并持久化: {umo}")
                yield event.plain_result("🎉 订阅成功！\n当有随机市场事件发生时，您将会在这里收到推送。")
            except Exception as e:
                logger.error(f"添加订阅者 {umo} 到数据库时失败: {e}", exc_info=True)
                yield event.plain_result("❌ 订阅失败，后台数据库出错。")

    @filter.command("取消订阅股票", alias={"退订市场"})
    async def unsubscribe_news(self, event: AstrMessageEvent):
        """取消订阅随机市场事件快讯"""
        umo = event.unified_msg_origin
        if umo in self.broadcast_subscribers:
            # --- 核心修改：从数据库删除 ---
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("DELETE FROM subscriptions WHERE umo = ?", (umo,))
                    await db.commit()
                
                self.broadcast_subscribers.remove(umo)
                logger.info(f"订阅者已移除并持久化: {umo}")
                yield event.plain_result("✅ 已为您取消订阅市场快讯。")
            except Exception as e:
                logger.error(f"从数据库移除订阅者 {umo} 时失败: {e}", exc_info=True)
                yield event.plain_result("❌ 取消订阅失败，后台数据库出错。")
        else:
            yield event.plain_result("您尚未订阅市场快讯。")