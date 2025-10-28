# stock_market/simulation.py

import asyncio
import random
import math
from datetime import datetime, date
from typing import TYPE_CHECKING, Optional

from astrbot.api import logger
from astrbot.api.event import MessageChain

# ▼▼▼【兼容性修改】重新导入 Trend 枚举 ▼▼▼
from .models import VirtualStock, DailyScript, MarketCycle, DailyBias, Trend
# ▲▲▲【修改结束】▲▲▲

from .config import (NATIVE_EVENT_PROBABILITY_PER_TICK, NATIVE_STOCK_RANDOM_EVENTS,
                     INTRINSIC_VALUE_PRESSURE_FACTOR, PRESSURE_DECAY_RATE)

if TYPE_CHECKING:
    from .main import StockMarketRefactored

class MarketMaker:
    """市场庄家 - 提供流动性、平抑价格波动、主动做局"""

    def __init__(self, config_dict: dict):
        self.position_tracker = {}  # 跟踪庄家在每只股票上的持仓
        self.budget_per_stock = config_dict.get('MARKET_MAKER_BUDGET', 2000000)
        self.max_position = config_dict.get('MARKET_MAKER_MAX_POSITION', 20000000)
        self.base_impact = config_dict.get('MARKET_MAKER_BASE_IMPACT', 0.00002)
        self.deviation_threshold = config_dict.get('DEVIATION_THRESHOLD', 0.15)
        self.counter_trade_intensity = config_dict.get('COUNTER_TRADE_INTENSITY', 0.3)
        self.pressure_threshold = config_dict.get('MARKET_PRESSURE_THRESHOLD', 50000)

        # ====== 【修复】做局系统配置 ======
        self.rig_probability = 0.02  # 每次tick做局概率2%（从10%降至2%）
        self.rig_cooldown = 60  # 做局后冷却60个tick (约5小时，从20提升到60)
        self.max_rig_pressure = 50  # 做局时最大压力值（从200降至50，减少75%）
        self.trap_duration = 5  # 做局持续5个tick（从10降至5，减少50%）

        # 做局状态跟踪
        self.rig_state = {}  # 状态: None, 'trapping_up', 'trapping_down', 'harvesting', 'cooling'
        self.rig_progress = {}  # 做局进度 (tick计数)
        self.rig_cooldown_tracker = {}  # 做局冷却计时器

        # ======【新增】打击抄底系统配置 ======
        self.dip_attack_cooldown = {}  # 打击抄底冷却计时器
        self.dip_attack_threshold = 0.25  # 打击抄底阈值：25%（从10%提升到25%）
        self.dip_attack_min_interval = 30  # 最小触发间隔：30个tick（约2.5小时）

        logger.info("[庄家系统] 增强版已启用：主动做局 + 打击抄底")

    async def analyze_and_trade(self, stock, current_price: float,
                               fundamental_value: float, market_pressure: float) -> float:
        """
        分析市场并执行反向交易或主动做局
        返回：庄家交易对价格的影响（正为上涨，负为下跌）
        """
        if fundamental_value <= 0:
            return 0.0

        stock_id = stock.stock_id

        # 计算价格偏离度
        deviation_ratio = current_price / fundamental_value

        # 获取当前持仓
        current_position = self.position_tracker.get(stock_id, 0.0)

        # ====== 【核心】庄家做局与防御逻辑 ======
        price_impact = 0.0
        rig_state = self.rig_state.get(stock_id, None)
        rig_progress = self.rig_progress.get(stock_id, 0)
        cooldown = self.rig_cooldown_tracker.get(stock_id, 0)

        # 1. 更新做局状态和冷却
        if rig_state == 'cooling':
            cooldown -= 1
            if cooldown <= 0:
                rig_state = None
                cooldown = 0
            self.rig_cooldown_tracker[stock_id] = cooldown

        # 2. 做局逻辑：先引诱，再收割
        if rig_state:
            if rig_state == 'trapping_up':
                # 阶段1: 做局拉高，制造假突破
                trap_pressure = (self.max_rig_pressure * 0.6) + (rig_progress * 5)
                price_impact += trap_pressure * self.base_impact * 2  # 降低影响倍数（从5降至2）
                current_position += trap_pressure
                rig_progress += 1

                # 完成做局，转入收割阶段
                if rig_progress >= self.trap_duration:
                    rig_state = 'harvesting_up'
                    rig_progress = 0
                    logger.info(f"[庄家做局] {stock.name}({stock_id}) 开始收割！拉高诱多阶段完成")

            elif rig_state == 'trapping_down':
                # 阶段1: 做局打压，制造假跌破
                trap_pressure = (self.max_rig_pressure * 0.6) + (rig_progress * 5)
                price_impact -= trap_pressure * self.base_impact * 2  # 降低影响倍数（从5降至2）
                current_position -= trap_pressure
                rig_progress += 1

                if rig_progress >= self.trap_duration:
                    rig_state = 'harvesting_down'
                    rig_progress = 0
                    logger.info(f"[庄家做局] {stock.name}({stock_id}) 开始收割！打压恐慌阶段完成")

            elif rig_state == 'harvesting_up':
                # 阶段2: 收割 - 拉高后快速砸盘出货
                harvest_pressure = self.max_rig_pressure
                price_impact -= harvest_pressure * self.base_impact * 3  # 降低影响倍数（从8降至3）
                current_position -= harvest_pressure
                rig_progress += 1

                if rig_progress >= 5:
                    rig_state = 'cooling'
                    rig_progress = 0
                    cooldown = self.rig_cooldown
                    logger.info(f"[庄家做局] {stock.name}({stock_id}) 收割完成，进入冷却期")

            elif rig_state == 'harvesting_down':
                # 阶段2: 收割 - 打压后快速拉起吸筹
                harvest_pressure = self.max_rig_pressure
                price_impact += harvest_pressure * self.base_impact * 3  # 降低影响倍数（从8降至3）
                current_position += harvest_pressure
                rig_progress += 1

                if rig_progress >= 5:
                    rig_state = 'cooling'
                    rig_progress = 0
                    cooldown = self.rig_cooldown
                    logger.info(f"[庄家做局] {stock.name}({stock_id}) 收割完成，进入冷却期")

        # 3. 被动防御逻辑（原有）
        else:
            # 3a. 价格过高时卖出（保护散户）
            if deviation_ratio > (1.0 + self.deviation_threshold):
                deviation_excess = deviation_ratio - (1.0 + self.deviation_threshold)
                sell_intensity = min(0.25, deviation_excess * 2)
                sell_amount = self.budget_per_stock * sell_intensity
                price_impact -= sell_amount * self.base_impact
                current_position -= sell_amount

            # 3b. 价格过低时买入（抄底）
            elif deviation_ratio < (1.0 - self.deviation_threshold):
                deviation_deficit = (1.0 - self.deviation_threshold) - deviation_ratio
                buy_intensity = min(0.25, deviation_deficit * 2)
                buy_amount = self.budget_per_stock * buy_intensity
                price_impact += buy_amount * self.base_impact
                current_position += buy_amount

            # 3c. 市场压力过强时反向操作
            if abs(market_pressure) > self.pressure_threshold:
                counter_trade = market_pressure * self.counter_trade_intensity
                if market_pressure > 0:  # 上涨压力过大，庄家卖出
                    price_impact -= abs(counter_trade) * self.base_impact * 5
                    current_position -= abs(counter_trade)
                else:  # 下跌压力过大，庄家买入
                    price_impact += abs(counter_trade) * self.base_impact * 5
                    current_position += abs(counter_trade)

            # 3d. 【修复】打击抄底 - 当价格暴跌时继续砸盘（增加冷却机制和数据验证）
            # 计算近5个tick的价格变化率（假设可以通过stock对象获取历史价格）
            if hasattr(stock, 'price_history'):
                # 确保 price_history 是正确的类型 (deque)
                if isinstance(stock.price_history, str):
                    # 如果 price_history 变成了字符串，重新初始化为空 deque
                    from collections import deque
                    stock.price_history = deque(maxlen=60)
                    logger.warning(f"[庄家] {stock.name}({stock_id}) 的 price_history 类型异常，已重置")
                elif len(stock.price_history) >= 15:
                    recent_prices = list(stock.price_history)[-10:]
                    if len(recent_prices) >= 10:
                        # 使用最近5-10个周期的数据判断是否触底趋势
                        # 方法：比较最近5个周期 vs 前5个周期的平均价格
                        recent_5_avg = sum(recent_prices[-5:]) / 5
                        previous_5_avg = sum(recent_prices[-10:-5]) / 5

                        # ======【修复】增加数据验证和更严格的触发条件 ======
                        # 检查数据有效性：价格不能为0、负数或异常值
                        if (previous_5_avg > 0 and recent_5_avg > 0 and
                            all(p > 0 for p in recent_prices[-10:]) and  # 确保所有价格都大于0
                            abs(previous_5_avg - recent_5_avg) / previous_5_avg < 0.5):  # 排除极端异常值（跌幅>50%）

                            avg_decline_rate = (previous_5_avg - recent_5_avg) / previous_5_avg

                            # 检查冷却状态
                            dip_cooldown = self.dip_attack_cooldown.get(stock_id, 0)
                            if dip_cooldown > 0:
                                self.dip_attack_cooldown[stock_id] = dip_cooldown - 1

                            # 触发条件：跌幅超过25%且不在冷却期
                            if avg_decline_rate > self.dip_attack_threshold and dip_cooldown <= 0:
                                dip_pressure = min(80, abs(market_pressure) + 15)  # 降低做空压力（从100降至80）
                                price_impact -= dip_pressure * self.base_impact * 1.5  # 降低影响倍数（从2降至1.5）
                                current_position -= dip_pressure
                                # 设置冷却期
                                self.dip_attack_cooldown[stock_id] = self.dip_attack_min_interval
                                logger.info(f"[庄家] {stock.name}({stock_id}) 打击抄底: 继续砸盘，平均跌幅{avg_decline_rate:.1%}，冷却{self.dip_attack_min_interval}个tick")
                            elif avg_decline_rate > 0.10:  # 如果跌幅在10%-25%之间，仅记录日志不触发
                                logger.debug(f"[庄家] {stock.name}({stock_id}) 检测到底部信号但未触发（跌幅{avg_decline_rate:.1%} < 阈值{self.dip_attack_threshold:.1%}）")

            # 3e. 【新增】随机开始做局
            if not rig_state and cooldown <= 0:
                # 做局概率检查
                if random.random() < self.rig_probability:
                    # 随机选择做局方向
                    # 70%概率做局拉高(假突破)，30%概率做局打压(假跌破)
                    if random.random() < 0.7:
                        rig_state = 'trapping_up'
                        logger.info(f"[庄家做局] {stock.name}({stock_id}) 开始做局：假突破！将拉高诱多")
                    else:
                        rig_state = 'trapping_down'
                        logger.info(f"[庄家做局] {stock.name}({stock_id}) 开始做局：假跌破！将打压恐慌")
                    rig_progress = 0

        # 更新做局状态
        self.rig_state[stock_id] = rig_state
        self.rig_progress[stock_id] = rig_progress

        # 限制最大持仓
        self.position_tracker[stock_id] = max(-self.max_position,
                                             min(self.max_position, current_position))

        return price_impact

class MarketSimulation:
    def __init__(self, plugin: "StockMarketRefactored"):
        self.plugin = plugin
        self.task: Optional[asyncio.Task] = None
        # ======【初始化庄家系统】======
        from .config import (MARKET_MAKER_ENABLED, MARKET_MAKER_BUDGET, MARKET_MAKER_MAX_POSITION,
                            MARKET_MAKER_BASE_IMPACT, DEVIATION_THRESHOLD, COUNTER_TRADE_INTENSITY,
                            MARKET_PRESSURE_THRESHOLD)
        if MARKET_MAKER_ENABLED:
            config_dict = {
                'MARKET_MAKER_BUDGET': MARKET_MAKER_BUDGET,
                'MARKET_MAKER_MAX_POSITION': MARKET_MAKER_MAX_POSITION,
                'MARKET_MAKER_BASE_IMPACT': MARKET_MAKER_BASE_IMPACT,
                'DEVIATION_THRESHOLD': DEVIATION_THRESHOLD,
                'COUNTER_TRADE_INTENSITY': COUNTER_TRADE_INTENSITY,
                'MARKET_PRESSURE_THRESHOLD': MARKET_PRESSURE_THRESHOLD
            }
            self.market_maker = MarketMaker(config_dict)
        else:
            self.market_maker = None

    def start(self):
        """启动价格更新循环任务。"""
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self._update_stock_prices_loop())
            logger.info("股票价格更新循环已启动。")

    def stop(self):
        """停止价格更新循环任务。"""
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("股票价格更新循环已停止。")
            
    def _generate_daily_script(self, stock: VirtualStock, current_date: date) -> DailyScript:
        """为单支股票生成每日剧本 (V5.3 算法)。"""
        momentum = stock.get_momentum()
        last_close = stock.get_last_day_close()
        valuation_ratio = last_close / stock.fundamental_value if stock.fundamental_value > 0 else 1.0

        mean_reversion_pressure = 1.0
        if valuation_ratio < 0.7: mean_reversion_pressure = 1 / max(valuation_ratio, 0.1)
        elif valuation_ratio > 1.5: mean_reversion_pressure = valuation_ratio

        bias_weights = [1.0, 1.0, 1.0]
        if self.plugin.market_simulator.cycle == MarketCycle.BULL_MARKET: bias_weights[0] *= 2.0
        elif self.plugin.market_simulator.cycle == MarketCycle.BEAR_MARKET: bias_weights[2] *= 2.0
        if momentum > 0: bias_weights[0] *= (1 + momentum * 1.5)
        elif momentum < 0: bias_weights[2] *= (1 - abs(momentum) * 1.5)
        if valuation_ratio < 0.7: bias_weights[0] *= mean_reversion_pressure
        elif valuation_ratio > 1.5: bias_weights[2] *= mean_reversion_pressure
        bias = random.choices([DailyBias.UP, DailyBias.SIDEWAYS, DailyBias.DOWN], weights=bias_weights, k=1)[0]

        base_range = stock.volatility * random.uniform(0.7, 1.5)
        if self.plugin.market_simulator.volatility_regime.value == "高波动期": base_range *= 1.7
        if bias != DailyBias.SIDEWAYS: base_range *= 1.3

        price_change = last_close * base_range * random.uniform(0.4, 1.0)
        if bias == DailyBias.UP: target_close = last_close + price_change
        elif bias == DailyBias.DOWN: target_close = last_close - price_change
        else: target_close = last_close + (price_change / 2 * random.choice([-1, 1]))

        return DailyScript(date=current_date, bias=bias, expected_range_factor=base_range, target_close=max(0.01, target_close))

    async def _handle_native_stock_random_event(self, stock: VirtualStock) -> Optional[str]:
        """处理原生虚拟股票的随机事件。"""
        if random.random() > NATIVE_EVENT_PROBABILITY_PER_TICK:
            return None

        eligible_events = [e for e in NATIVE_STOCK_RANDOM_EVENTS if e.get("industry") is None or e.get("industry") == stock.industry]
        if not eligible_events:
            return None

        event_weights = [e.get('weight', 1) for e in eligible_events]
        chosen_event = random.choices(eligible_events, weights=event_weights, k=1)[0]

        if chosen_event.get("effect_type") == 'price_change_percent':
            value_min, value_max = chosen_event['value_range']
            percent_change = round(random.uniform(value_min, value_max), 4)
            new_price = round(stock.current_price * (1 + percent_change), 2)
            stock.current_price = max(0.01, new_price)
            return chosen_event['message'].format(stock_name=stock.name, stock_id=stock.stock_id, value=percent_change)
        
        return None

    async def _update_stock_prices_loop(self):
        """后台任务循环，更新股票价格 (V2.1 分级动能波)。"""
        from .config import (BIG_WAVE_PROBABILITY, SMALL_WAVE_PEAK_MIN, SMALL_WAVE_PEAK_MAX,
                             SMALL_WAVE_TICKS_MIN, SMALL_WAVE_TICKS_MAX, BIG_WAVE_PEAK_MIN,
                             BIG_WAVE_PEAK_MAX, BIG_WAVE_TICKS_MIN, BIG_WAVE_TICKS_MAX)

        while True:
            try:
                new_status, wait_seconds = self.plugin.get_market_status_and_wait()
                self.plugin.market_status = new_status
                if new_status != self.plugin.market_status:
                    logger.info(f"市场状态变更: {self.plugin.market_status.value} -> {new_status.value}")
                    self.plugin.market_status = new_status
                
                if self.plugin.market_status.value != "交易中":
                    if wait_seconds > 0: await asyncio.sleep(wait_seconds)
                    continue

                now = datetime.now()
                today = now.date()
                if self.plugin.last_update_date != today:
                    logger.info(f"新交易日 ({today}) 开盘，正在初始化市场...")
                    self.plugin.market_simulator.update(logger)
                    for stock in self.plugin.stocks.values():
                        if self.plugin.last_update_date:
                            stock.previous_close = stock.current_price
                            stock.daily_close_history.append(stock.current_price)
                        else:
                            stock.previous_close = stock.current_price
                        
                        stock.update_fundamental_value()
                        stock.daily_script = self._generate_daily_script(stock, today)
                    self.plugin.last_update_date = today

                db_updates = []
                current_interval_minute = (now.minute // 5) * 5
                five_minute_start = now.replace(minute=current_interval_minute, second=0, microsecond=0)

                for stock in self.plugin.stocks.values():
                    script = stock.daily_script
                    if not script: continue

                    open_price = stock.current_price
                    event_message = None

                    if not stock.is_listed_company:
                        event_message = await self._handle_native_stock_random_event(stock)

                    if event_message:
                        logger.info(f"[随机市场事件] {event_message}")
                        message_chain = MessageChain().message(f"【市场快讯】\n{event_message}")
                        subscribers_copy = list(self.plugin.broadcast_subscribers)
                        for umo in subscribers_copy:
                            try:
                                await self.plugin.context.send_message(umo, message_chain)
                            except Exception as e:
                                logger.error(f"向订阅者 {umo} 推送消息失败: {e}")
                                if umo in self.plugin.broadcast_subscribers:
                                    self.plugin.broadcast_subscribers.remove(umo)
                        
                        close_price = stock.current_price
                        high_price, low_price = (max(open_price, close_price), min(open_price, close_price))
                    else:
                        # --- ▼▼▼【核心算法 V2.1】▼▼▼
                        
                        if stock.momentum_current_tick >= stock.momentum_duration_ticks:
                            stock.intraday_momentum = 0.0
                            stock.momentum_current_tick = 0
                            stock.momentum_duration_ticks = 0

                        if stock.momentum_duration_ticks == 0 and random.random() < 0.3:
                            bias = script.bias
                            weights = [0.6, 0.4] if bias == DailyBias.UP else [0.4, 0.6] if bias == DailyBias.DOWN else [0.5, 0.5]
                            direction = random.choices([1, -1], weights=weights)[0]
                            
                            if random.random() < BIG_WAVE_PROBABILITY:
                                peak_magnitude = random.uniform(BIG_WAVE_PEAK_MIN, BIG_WAVE_PEAK_MAX)
                                duration_ticks = random.randint(BIG_WAVE_TICKS_MIN, BIG_WAVE_TICKS_MAX)
                            else:
                                peak_magnitude = random.uniform(SMALL_WAVE_PEAK_MIN, SMALL_WAVE_PEAK_MAX)
                                duration_ticks = random.randint(SMALL_WAVE_TICKS_MIN, SMALL_WAVE_TICKS_MAX)

                            stock.momentum_target_peak = direction * peak_magnitude
                            stock.momentum_duration_ticks = duration_ticks
                            stock.momentum_current_tick = 0
                        
                        if stock.momentum_duration_ticks > 0:
                            stock.momentum_current_tick += 1
                            progress = stock.momentum_current_tick / stock.momentum_duration_ticks
                            momentum_factor = math.sin(progress * math.pi)
                            stock.intraday_momentum = stock.momentum_target_peak * momentum_factor

                        effective_volatility = script.expected_range_factor / math.sqrt(288) * 2.2
                        trend_influence = stock.intraday_momentum * (open_price * effective_volatility) * random.uniform(0.8, 1.2)
                        random_walk = open_price * effective_volatility * random.normalvariate(0, 0.8)
                        
                        short_term_reversion_force = 0
                        if isinstance(stock.price_history, str):
                            # 如果 price_history 变成了字符串，重新初始化为空 deque
                            from collections import deque
                            stock.price_history = deque(maxlen=60)
                            logger.warning(f"[价格更新] {stock.name}({stock_id}) 的 price_history 类型异常，已重置")
                        elif len(stock.price_history) >= 5:
                            sma5 = sum(list(stock.price_history)[-5:]) / 5
                            short_term_reversion_force = -(open_price - sma5) * 0.15

                        intraday_anchor_force = (script.target_close - open_price) / 288 * 0.05
                        pressure_influence = stock.market_pressure * 0.01
                        # ======【加快压力衰减】======
                        stock.market_pressure *= PRESSURE_DECAY_RATE  # 每tick衰减15%
                        # 处理预埋卖压：缓慢转化为实际卖压
                        if hasattr(stock, 'pending_sell_pressure') and stock.pending_sell_pressure > 0:
                            stock.pending_sell_pressure *= 0.90  # 每tick衰减10%
                            # 一部分预埋卖压转化为实际市场压力
                            convert_rate = 0.05
                            converted_pressure = stock.pending_sell_pressure * convert_rate
                            stock.market_pressure -= converted_pressure
                            stock.pending_sell_pressure -= converted_pressure

                        # ======【庄家系统介入】======
                        market_maker_impact = 0.0
                        if self.market_maker:
                            market_maker_impact = await self.market_maker.analyze_and_trade(
                                stock, open_price, stock.fundamental_value, stock.market_pressure
                            )

                        total_change = (trend_influence + random_walk + short_term_reversion_force +
                                       intraday_anchor_force + pressure_influence + market_maker_impact)
                        close_price = round(max(0.01, open_price + total_change), 2)
                        
                        # --- ▲▲▲【核心算法结束】▲▲▲
                        
                        # ▼▼▼【兼容层】根据新动能更新旧趋势字段，以兼容main.py ▼▼▼
                        if stock.intraday_momentum > 0.15:
                            stock.intraday_trend = Trend.BULLISH
                        elif stock.intraday_momentum < -0.15:
                            stock.intraday_trend = Trend.BEARISH
                        else:
                            stock.intraday_trend = Trend.NEUTRAL
                        stock.intraday_trend_duration = max(0, stock.momentum_duration_ticks - stock.momentum_current_tick)
                        # ▲▲▲【兼容层结束】▲▲▲

                        absolute_volatility_base = open_price * (script.expected_range_factor / math.sqrt(288))
                        high_price = round(max(open_price, close_price) + random.uniform(0, absolute_volatility_base * 0.8), 2)
                        low_price = round(max(0.01, min(open_price, close_price) - random.uniform(0, absolute_volatility_base * 0.8)), 2)
                        stock.current_price = close_price
                    
                    stock.price_history.append(stock.current_price)
                    kline_entry = {"date": five_minute_start.isoformat(), "open": open_price, "high": high_price, "low": low_price, "close": stock.current_price}
                    stock.kline_history.append(kline_entry)
                    db_updates.append({"stock_id": stock.stock_id, "current_price": stock.current_price, "kline": kline_entry, "market_pressure": stock.market_pressure})

                if self.plugin.db_manager:
                    await self.plugin.db_manager.batch_update_stock_data(db_updates)

                now_after_update = datetime.now()
                seconds_to_wait = (5 - (now_after_update.minute % 5)) * 60 - now_after_update.second
                await asyncio.sleep(max(1, seconds_to_wait))

            except asyncio.CancelledError:
                logger.info("股票价格更新任务被取消。")
                break
            except Exception as e:
                logger.error(f"股票价格更新任务出现严重错误: {e}", exc_info=True)
                await asyncio.sleep(60)