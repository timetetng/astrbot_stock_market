# stock_simulation_tester.py
# V5.3 (混沌微调版) - 增强日内走势的随机性和不可预测性

import asyncio
import os
import random
import math
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from collections import deque
from enum import Enum
from dataclasses import dataclass, field
import jinja2
from playwright.async_api import async_playwright, Browser, Error as PlaywrightError

# --- 1. 宏观环境与剧本定义 (与V5.2相同) ---
class MarketCycle(Enum):
    BULL_MARKET = "牛市"; BEAR_MARKET = "熊市"; NEUTRAL_MARKET = "盘整市"
class VolatilityRegime(Enum):
    LOW = "低波动期"; HIGH = "高波动期"
class DailyBias(Enum):
    UP = "上涨日"; DOWN = "下跌日"; SIDEWAYS = "盘整日"
@dataclass
class DailyScript:
    date: datetime; bias: DailyBias; expected_range_factor: float; target_close: float
@dataclass
class MarketSimulator:
    cycle: MarketCycle = MarketCycle.NEUTRAL_MARKET; volatility_regime: VolatilityRegime = VolatilityRegime.LOW
    steps_in_current_cycle: int = 0; steps_in_current_vol_regime: int = 0
    min_cycle_duration: int = 90; min_vol_duration: int = 20
    def update(self):
        self.steps_in_current_cycle += 1
        if self.steps_in_current_cycle > self.min_cycle_duration and random.random() < 1 / 90:
            old_cycle_name = self.cycle.value; self.cycle = random.choice([c for c in MarketCycle if c != self.cycle]); self.steps_in_current_cycle = 0
            print(f"\n{'='*20}\n[宏观周期转换] 市场从【{old_cycle_name}】进入【{self.cycle.value}】!\n{'='*20}\n")
        self.steps_in_current_vol_regime += 1
        if self.steps_in_current_vol_regime > self.min_vol_duration and random.random() < 1 / 45:
            old_vol_name = self.volatility_regime.value; self.volatility_regime = VolatilityRegime.HIGH if self.volatility_regime == VolatilityRegime.LOW else VolatilityRegime.LOW; self.steps_in_current_vol_regime = 0
            print(f"\n{'~'*20}\n[市场情绪转换] 市场进入【{self.volatility_regime.value}】!\n{'~'*20}\n")

# --- 2. 主体对象定义 (与V5.2相同) ---
class Trend(Enum):
    BULLISH = 1; BEARISH = -1; NEUTRAL = 0
@dataclass
class VirtualStock:
    stock_id: str; name: str
    price_history: deque = field(default_factory=lambda: deque(maxlen=20))
    daily_k_data: List[Dict] = field(default_factory=list)
    initial_price: float = 200.0; fundamental_value: float = 200.0
    current_price: float = 200.0
    intraday_price_history: deque = field(default_factory=lambda: deque(maxlen=60))
    intraday_trend: Trend = Trend.NEUTRAL; intraday_trend_duration: int = 0
    
    def get_last_close(self) -> float: return self.daily_k_data[-1]['close'] if self.daily_k_data else self.initial_price
    def get_momentum(self) -> float:
        if len(self.price_history) < 5: return 0.0
        changes = [1 if self.price_history[i] > self.price_history[i-1] else -1 for i in range(1, len(self.price_history))]
        weights = list(range(1, len(changes) + 1)); return sum(c * w for c, w in zip(changes, weights)) / sum(weights)
    def update_fundamental_value(self): self.fundamental_value *= random.uniform(0.999, 1.001)
    def reset_for_new_day(self):
        self.current_price = self.get_last_close()
        self.intraday_price_history.clear(); self.intraday_price_history.append(self.current_price)
        self.intraday_trend = Trend.NEUTRAL; self.intraday_trend_duration = 0

# --- 渲染环境配置 ---
DATA_DIR = os.path.join(os.path.dirname(__file__), "test_output"); TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates"); os.makedirs(DATA_DIR, exist_ok=True)
jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES_DIR), autoescape=True, enable_async=True)

# ----------------------------
# 核心算法 V5.3
# ----------------------------

def generate_daily_script(stock: VirtualStock, market: MarketSimulator, date: datetime) -> DailyScript:
    # ... 此函数逻辑与 V5.2 相同 ...
    momentum = stock.get_momentum(); last_close = stock.get_last_close(); valuation_ratio = last_close / stock.fundamental_value
    mean_reversion_pressure = 1.0
    if valuation_ratio < 0.7: mean_reversion_pressure = 1 / max(valuation_ratio, 0.1)
    elif valuation_ratio > 1.5: mean_reversion_pressure = valuation_ratio
    bias_weights = [1.0, 1.0, 1.0]
    if market.cycle == MarketCycle.BULL_MARKET: bias_weights[0] *= 2.0
    elif market.cycle == MarketCycle.BEAR_MARKET: bias_weights[1] *= 2.0
    if momentum > 0: bias_weights[0] *= (1 + momentum * 1.5)
    elif momentum < 0: bias_weights[1] *= (1 - momentum * 1.5)
    if valuation_ratio < 0.7: bias_weights[0] *= mean_reversion_pressure
    elif valuation_ratio > 1.5: bias_weights[1] *= mean_reversion_pressure
    bias = random.choices([DailyBias.UP, DailyBias.DOWN, DailyBias.SIDEWAYS], weights=bias_weights, k=1)[0]
    base_range = random.uniform(0.015, 0.04)
    if market.volatility_regime == VolatilityRegime.HIGH: base_range *= 1.8
    if bias != DailyBias.SIDEWAYS: base_range *= 1.3
    price_change = last_close * base_range * random.uniform(0.4, 1.0)
    if bias == DailyBias.UP: target_close = last_close + price_change
    elif bias == DailyBias.DOWN: target_close = last_close - price_change
    else: target_close = last_close + (price_change / 2 * random.choice([-1, 1]))
    return DailyScript(date=date, bias=bias, expected_range_factor=base_range, target_close=max(0.01, target_close))

def simulate_intraday_session(stock: VirtualStock, script: DailyScript, date: datetime) -> Tuple[Dict, List[Dict]]:
    intraday_5min_k_data = []
    
    for i in range(288):
        # 1. 更新微观趋势
        if stock.intraday_trend_duration <= 0:
            # --- V5.3 核心改动 1: 降低剧本对微观趋势的控制力 ---
            if script.bias == DailyBias.UP: weights = [0.5, 0.3, 0.2]     # 原 [0.6, 0.3, 0.1]
            elif script.bias == DailyBias.DOWN: weights = [0.2, 0.3, 0.5] # 原 [0.1, 0.3, 0.6]
            else: weights = [0.3, 0.4, 0.3]
            stock.intraday_trend = random.choices([Trend.BULLISH, Trend.NEUTRAL, Trend.BEARISH], weights=weights, k=1)[0]
            stock.intraday_trend_duration = random.randint(6, 18) # 趋势持续时间略微缩短，增加变化
        else:
            stock.intraday_trend_duration -= 1
            
        # 2. 计算5分钟价格变动
        open_price = stock.current_price
        
        # --- V5.3 核心改动 2: 增强随机漫步的强度 ---
        # 将日波幅转换为5分钟波幅, 并提高随机乘数
        effective_volatility = script.expected_range_factor / math.sqrt(288) * 2.0 # 原 1.5
        
        trend_influence = stock.intraday_trend.value * (open_price * effective_volatility) * random.uniform(0.5, 1.5)
        random_walk = open_price * effective_volatility * random.normalvariate(0, 1)
        
        short_term_reversion_force = 0
        if len(stock.intraday_price_history) >= 5:
            sma5 = sum(list(stock.intraday_price_history)[-5:]) / 5
            short_term_reversion_force = -(open_price - sma5) * 0.1
        
        # --- V5.3 核心改动 3: 进一步削弱日内锚点的引力 ---
        intraday_anchor_force = (script.target_close - open_price) / 288 * 0.05 # 原 0.2
        
        total_change = trend_influence + random_walk + short_term_reversion_force + intraday_anchor_force
        close_price = round(max(0.01, open_price + total_change), 2)
        
        # 3. 记录5分钟K线和状态
        stock.current_price = close_price; stock.intraday_price_history.append(close_price)
        timestamp = date + timedelta(minutes=i * 5)
        intraday_5min_k_data.append({"date": timestamp.isoformat(), "open": open_price, "high": max(open_price, close_price), "low": min(open_price, close_price), "close": close_price})

    # 聚合日K
    day_open = intraday_5min_k_data[0]['open']; day_high = max(k['high'] for k in intraday_5min_k_data); day_low = min(k['low'] for k in intraday_5min_k_data); day_close = intraday_5min_k_data[-1]['close']
    daily_aggregate = {"date": script.date.isoformat(), "open": day_open, "high": day_high, "low": day_low, "close": day_close}
    return daily_aggregate, intraday_5min_k_data

# --- 主执行函数 ---
async def main():
    print("--- 股票模拟算法长期测试器 (V5.3 混沌微调版) ---")
    durations_to_simulate = [24, 72, 168]
    initial_price = 200.0
    for duration_hours in durations_to_simulate:
        print(f"\n{'#'*50}\n# 开始模拟 {duration_hours} 小时 ({duration_hours/24:.1f} 天) 的走势...\n{'#'*50}")
        test_stock = VirtualStock(stock_id="HYBRID", name="混合推演股", initial_price=initial_price, fundamental_value=initial_price, current_price=initial_price)
        market = MarketSimulator()
        all_5min_k_data = []
        simulation_days = math.ceil(duration_hours / 24)
        start_time = datetime.now()
        for i in range(simulation_days):
            current_date = start_time + timedelta(days=i)
            test_stock.reset_for_new_day()
            script = generate_daily_script(test_stock, market, current_date)
            print(f"  -- Day {i+1}, 宏观: {market.cycle.value}/{market.volatility_regime.value}, 今日剧本: {script.bias.value}")
            daily_aggregate, intraday_5min_k_data = simulate_intraday_session(test_stock, script, current_date)
            all_5min_k_data.extend(intraday_5min_k_data)
            test_stock.daily_k_data.append(daily_aggregate); test_stock.price_history.append(daily_aggregate['close'])
            test_stock.update_fundamental_value(); market.update()
        final_5min_data = all_5min_k_data[:duration_hours * 12]
        print(f"\n模拟完成！共生成 {len(final_5min_data)} 个5分钟数据点。")
        final_price = final_5min_data[-1]['close']; print(f"初始价格: {initial_price:.2f}, 最终价格: {final_price:.2f}")
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                output_file = os.path.join(DATA_DIR, f"simulation_v5.3_{duration_hours}h_5min.png")
                # 此处省略渲染函数，与之前版本完全相同
                await render_kline_chart(test_stock.name, test_stock.stock_id, final_5min_data, duration_hours, output_file, browser)
                await browser.close()
            except Exception as e: print(f"启动或渲染 Playwright 失败: {e}")

async def render_kline_chart(stock_name, stock_id, k_data, duration_hours, output_path, browser):
    """渲染K线图的函数 (现在接收时长参数)"""
    print(f"\n开始渲染 {duration_hours} 小时周期的5分钟K线图...")
    try:
        template = jinja_env.get_template("kline_chart.html")
        html_content = await template.render_async(stock_name=stock_name, stock_id=stock_id, data_period=f"模拟 {duration_hours} 小时 (V5.3 混沌微调版 - 5分钟K线)", stock_data=k_data)
        temp_html_path = os.path.join(DATA_DIR, "temp_chart_5min.html");
        with open(temp_html_path, "w", encoding="utf-8") as f: f.write(html_content)
        page = await browser.new_page(viewport={"width": 1200, "height": 600}); await page.goto(f"file://{os.path.abspath(temp_html_path)}")
        await page.wait_for_selector("#kline-chart", state="visible", timeout=15000); chart_element = await page.query_selector('#kline-chart'); await chart_element.screenshot(path=output_path); await page.close()
        print(f"✅ {duration_hours} 小时K线图已成功保存到: {output_path}")
    except Exception as e: print(f"❌ Playwright 截图失败: {e}")
    finally:
        if 'temp_html_path' in locals() and os.path.exists(temp_html_path): os.remove(temp_html_path)

if __name__ == "__main__":
    asyncio.run(main())