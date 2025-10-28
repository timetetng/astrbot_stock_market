from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Tuple, Optional, Dict

from .models import MarketStatus
from .config import (SELL_LOCK_MINUTES, SELL_FEE_RATE, SLIPPAGE_FACTOR, MAX_SLIPPAGE_DISCOUNT,
                     COST_PRESSURE_FACTOR, LARGE_TRADE_FEE_TIERS, FREQUENT_TRADE_THRESHOLD,
                     FREQUENT_TRADE_PENALTY, MAX_TRADES_FOR_PENALTY, DAILY_LIQUIDITY_LIMIT,
                     EXTREME_SLIPPAGE_THRESHOLD, LIQUIDITY_SHORTAGE_PENALTY, PROFIT_SELL_PRESSURE_MULTIPLIER,
                     SELL_PRESSURE_FACTOR, PENDING_SELL_PRESSURE_RATIO,
                     IPO_DILUTION_RATIO, MAX_DILUTION_PER_TRADE)

if TYPE_CHECKING:
    from .main import StockMarketRefactored

class TradingManager:
    def __init__(self, plugin: "StockMarketRefactored"):
        self.plugin = plugin

    async def get_user_daily_volume(self, user_id: str) -> float:
        """获取用户当日交易额"""
        today = datetime.now().date()
        if self.plugin.db_manager:
            return await self.plugin.db_manager.get_user_trading_volume(user_id, today)
        return 0.0

    async def get_stock_daily_volume(self, stock_id: str) -> float:
        """获取股票当日交易额"""
        today = datetime.now().date()
        if self.plugin.db_manager:
            return await self.plugin.db_manager.get_stock_trading_volume(stock_id, today)
        return 0.0

    async def get_user_recent_trades(self, user_id: str, hours: int = 1) -> int:
        """获取用户最近N小时内的交易次数"""
        if self.plugin.db_manager:
            return await self.plugin.db_manager.get_user_recent_trade_count(user_id, hours)
        return 0

    async def calculate_trading_fee(self, user_id: str, gross_amount: float, is_sell: bool) -> Tuple[float, str]:
        """计算渐进式交易费用"""
        # 基础费率
        base_fee_rate = SELL_FEE_RATE if is_sell else 0.005  # 买入费率较低（0.5%）

        # 获取用户当日交易量
        daily_volume = await self.get_user_daily_volume(user_id)
        total_volume = daily_volume + gross_amount

        # 渐进式费率增长
        fee_multiplier = 1.0
        for threshold, multiplier in sorted(LARGE_TRADE_FEE_TIERS.items()):
            if total_volume <= threshold:
                fee_multiplier = multiplier
                break
        else:
            fee_multiplier = LARGE_TRADE_FEE_TIERS[max(LARGE_TRADE_FEE_TIERS.keys())]

        # 频繁交易惩罚
        recent_trades = await self.get_user_recent_trades(user_id, hours=1)
        if recent_trades > FREQUENT_TRADE_THRESHOLD:
            fee_multiplier *= (1 + (recent_trades - FREQUENT_TRADE_THRESHOLD) * 0.2)

        # 超高频交易强制惩罚
        if recent_trades > MAX_TRADES_FOR_PENALTY:
            fee_multiplier *= FREQUENT_TRADE_PENALTY

        final_fee_rate = base_fee_rate * fee_multiplier
        fee = gross_amount * final_fee_rate

        penalty_msg = ""
        if fee_multiplier > 1.5:
            tier_info = ""
            for threshold, multiplier in sorted(LARGE_TRADE_FEE_TIERS.items(), reverse=True):
                if total_volume > threshold:
                    tier_info = f"（当前阶梯：{multiplier}x费率）"
                    break
            penalty_msg = f"\n⚠️ 大额交易费用{tier_info}\n当前费率：{final_fee_rate:.2%}（基础费率 {base_fee_rate:.2%}）"
            if recent_trades > FREQUENT_TRADE_THRESHOLD:
                penalty_msg += f"\n⚠️ 频繁交易惩罚：近1小时交易{recent_trades}次"

        return fee, penalty_msg

    async def check_liquidity_and_slippage(self, stock_id: str, order_amount: float,
                                          is_buy: bool) -> Tuple[bool, float, str]:
        """检查流动性并应用动态滑点"""
        daily_volume = await self.get_stock_daily_volume(stock_id)
        remaining_liquidity = DAILY_LIQUIDITY_LIMIT - daily_volume

        if remaining_liquidity <= 0:
            return False, 0, f"❌ {DAILY_LIQUIDITY_LIMIT:,}金币流动性已耗尽，请明日再来！"

        total_order = daily_volume + order_amount

        # 流动性不足惩罚
        if total_order > EXTREME_SLIPPAGE_THRESHOLD:
            # 计算极端滑点
            shortage_ratio = (total_order - EXTREME_SLIPPAGE_THRESHOLD) / EXTREME_SLIPPAGE_THRESHOLD
            extreme_slippage = min(0.8, shortage_ratio * 0.5 + LIQUIDITY_SHORTAGE_PENALTY)
            return True, extreme_slippage, f"⚠️ 流动性紧张！大额订单产生 {extreme_slippage:.1%} 滑点"
        elif order_amount > remaining_liquidity * 0.8:
            # 接近流动性极限
            medium_slippage = LIQUIDITY_SHORTAGE_PENALTY + (order_amount / remaining_liquidity) * 0.05
            return True, medium_slippage, f"⚠️ 接近流动性极限，产生 {medium_slippage:.1%} 滑点"

        return True, 0.0, ""

    async def perform_buy(self, user_id: str, identifier: str, quantity: int) -> Tuple[bool, str]:
        """执行买入操作的核心内部函数。"""
        # 市场状态检查
        current_status, _ = self.plugin.get_market_status_and_wait()
        if current_status != MarketStatus.OPEN:
            return False, f"⏱️ 当前市场状态为【{current_status.value}】，无法交易。"

        if not self.plugin.economy_api:
            return False, "经济系统未启用，无法进行交易！"
        if quantity <= 0:
            return False, "❌ 购买数量必须是一个正整数。"
        stock = await self.plugin.find_stock(identifier)
        if not stock:
            return False, f"❌ 找不到标识符为 '{identifier}' 的股票。"

        # 计算订单金额
        base_cost = round(stock.current_price * quantity, 2)

        # 检查流动性
        can_trade, additional_slippage, liquidity_msg = await self.check_liquidity_and_slippage(
            stock.stock_id, base_cost, is_buy=True
        )
        if not can_trade:
            return False, liquidity_msg

        # 计算交易费用
        trading_fee, fee_msg = await self.calculate_trading_fee(user_id, base_cost, is_sell=False)
        total_cost = base_cost + trading_fee

        balance = await self.plugin.economy_api.get_coins(user_id)
        if balance < total_cost:
            return False, (f"💰 金币不足！需要 {total_cost:.2f}（含手续费 {trading_fee:.2f}），"
                          f"你只有 {balance:.2f}。")

        # 执行扣款
        success = await self.plugin.economy_api.add_coins(user_id, -int(total_cost),
                                                         f"购买 {quantity} 股 {stock.name}（含手续费 {trading_fee:.2f}）")
        if not success:
            return False, "❗ 扣款失败，购买操作已取消。"

        # 记录持仓
        await self.plugin.db_manager.add_holding(user_id, stock.stock_id, quantity, stock.current_price)

        # ======【动态增发机制】======
        # 如果是上市公司，根据买入量增发新股（稀释现有股东股份）
        if stock.is_listed_company:
            # 计算增发股数：买入量 × 增发系数
            dilution_shares = int(quantity * IPO_DILUTION_RATIO)
            # 限制单次增发上限
            dilution_shares = min(dilution_shares, MAX_DILUTION_PER_TRADE)

            if dilution_shares > 0:
                # 调用股票插件的增发API
                if self.plugin.api:
                    await self.plugin.api.handle_dilution(stock.stock_id, dilution_shares)
                    logger.info(f"[增发] {stock.name}({stock.stock_id}) 增发 {dilution_shares:,} 股")

        # 记录交易
        await self.plugin.db_manager.record_trade(
            user_id=user_id,
            stock_id=stock.stock_id,
            trade_type='buy',
            quantity=quantity,
            price_per_share=stock.current_price,
            total_amount=base_cost,
            fee=trading_fee
        )

        # ======【对称性市场压力机制】======
        # 1. 买入压力：降低强度（避免单向上涨）
        buy_pressure = (base_cost ** 0.90) * COST_PRESSURE_FACTOR
        stock.market_pressure += buy_pressure * 0.5  # 初始影响减半

        # 2. 预埋卖压：买入时自动生成反向压力（获利回吐机制）
        pending_sell_pressure = buy_pressure * PENDING_SELL_PRESSURE_RATIO
        stock.pending_sell_pressure += pending_sell_pressure

        # 构建返回消息
        messages = [
            f"✅ 买入成功！",
            f"成交价格: ${stock.current_price:.2f}/股",
            f"成交数量: {quantity} 股",
            f"成交金额: {base_cost:.2f} 金币",
            f"手续费: {trading_fee:.2f} 金币",
            f"总支出: {total_cost:.2f} 金币"
        ]

        # ======【显示增发信息】======
        if stock.is_listed_company:
            dilution_shares = int(quantity * IPO_DILUTION_RATIO)
            if dilution_shares > 0:
                messages.append(f"📈 动态增发: 公司增发 {dilution_shares:,} 股（新总股本将在交易后更新）")

        if additional_slippage > 0:
            messages.append(liquidity_msg)
        if fee_msg:
            messages.append(fee_msg)

        messages.append(f"⏰ 锁定时间: {SELL_LOCK_MINUTES} 分钟后可卖出")

        return True, "\n".join(messages)


    async def perform_sell(self, user_id: str, identifier: str, quantity_to_sell: int) -> Tuple[bool, str, Optional[Dict]]:
        """执行卖出操作的核心内部函数。"""
        # ▼▼▼【核心修正】▼▼▼
        current_status, _ = self.plugin.get_market_status_and_wait()
        if current_status != MarketStatus.OPEN:
            # 注意：此函数返回三个值，所以这里也要返回三个值 (bool, str, None)
            return False, f"⏱️ 当前市场状态为【{current_status.value}】，无法交易。", None
        # ▲▲▲【修正结束】▲▲▲

        if not self.plugin.economy_api:
            return False, "经济系统未启用，无法进行交易！", None
        if quantity_to_sell <= 0:
            return False, "❌ 出售数量必须是一个正整数。", None
        stock = await self.plugin.find_stock(identifier)
        if not stock:
            return False, f"❌ 找不到标识符为 '{identifier}' 的股票。", None
        total_sellable = await self.plugin.db_manager.get_sellable_quantity(user_id, stock.stock_id)
        if total_sellable < quantity_to_sell:
            hint = await self.plugin.db_manager.get_next_unlock_time_str(user_id, stock.stock_id)
            return False, f"❌ 可卖数量不足！\n您想卖 {quantity_to_sell} 股，但只有 {total_sellable} 股可卖。{hint or ''}", None
        success, message, data = await self._execute_sell_order(user_id, stock.stock_id, quantity_to_sell, stock.current_price)
        return success, message, data

    async def _execute_sell_order(self, user_id: str, stock_id: str, quantity_to_sell: int, current_price: float) -> Tuple[bool, str, Dict]:
        """执行卖出操作的核心经济逻辑。"""
        # 计算基础收入和滑点
        price_discount_percent = min(quantity_to_sell * SLIPPAGE_FACTOR, MAX_SLIPPAGE_DISCOUNT)
        actual_sell_price = current_price * (1 - price_discount_percent)
        gross_income = round(actual_sell_price * quantity_to_sell, 2)

        # ======【对称性市场压力机制】======
        # 1. 计算盈亏（需要在扣费前计算）
        total_cost_basis = await self.plugin.db_manager.execute_fifo_sell(user_id, stock_id, quantity_to_sell)
        profit_loss = gross_income - total_cost_basis

        # 2. 根据盈亏计算不同的市场压力
        stock = self.plugin.stocks[stock_id]

        if profit_loss > 0:
            # 盈利卖出：产生强力负向压力（获利了结）
            base_pressure = (gross_income ** 0.95) * SELL_PRESSURE_FACTOR
            profit_pressure = (profit_loss ** 0.95) * SELL_PRESSURE_FACTOR * PROFIT_SELL_PRESSURE_MULTIPLIER
            total_downside_pressure = base_pressure + profit_pressure
            stock.market_pressure -= total_downside_pressure

            # 使用部分预埋卖压
            pending_to_use = min(stock.pending_sell_pressure, total_downside_pressure * 0.5)
            stock.pending_sell_pressure -= pending_to_use

        else:
            # 亏损卖出：减少正向压力但不强加负向压力
            base_pressure = (gross_income ** 0.95) * SELL_PRESSURE_FACTOR * 0.8
            stock.market_pressure -= base_pressure
            # 亏损时不清除预埋卖压

        # ======【交易费用系统】======
        # 计算交易费用
        trading_fee, fee_msg = await self.calculate_trading_fee(user_id, gross_income, is_sell=True)
        net_income = gross_income - trading_fee

        # 执行扣款（给玩家金币）
        await self.plugin.economy_api.add_coins(user_id, int(net_income),
                                               f"出售 {quantity_to_sell} 股 {stock.name}（含手续费 {trading_fee:.2f}）")

        # 记录交易
        await self.plugin.db_manager.record_trade(
            user_id=user_id,
            stock_id=stock_id,
            trade_type='sell',
            quantity=quantity_to_sell,
            price_per_share=actual_sell_price,
            total_amount=gross_income,
            fee=trading_fee
        )

        # 构建返回消息
        pnl_emoji = "🎉" if profit_loss > 0 else "😭" if profit_loss < 0 else "😐"
        slippage_info = f"（大单滑点 {price_discount_percent:.2%}）\n" if price_discount_percent >= 0.001 else "\n"

        message = f"✅ 卖出成功！{slippage_info}" \
                  f"成交数量: {quantity_to_sell} 股\n" \
                  f"当前市价: ${current_price:.2f}\n" \
                  f"成交均价: ${actual_sell_price:.2f}\n" \
                  f"成交总额: {gross_income:.2f} 金币\n" \
                  f"手续费: {trading_fee:.2f} 金币\n" \
                  f"实际收入: {net_income:.2f} 金币\n" \
                  f"{pnl_emoji} 本次盈亏: {profit_loss:+.2f} 金币"

        if fee_msg:
            message += f"\n{fee_msg}"

        return True, message, {
            "net_income": net_income,
            "fee": trading_fee,
            "profit_loss": profit_loss,
            "slippage_percent": price_discount_percent
        }

    async def perform_buy_all_in(self, user_id: str, identifier: str) -> Tuple[bool, str]:
        """执行梭哈买入操作"""
        # ▼▼▼【核心修正】▼▼▼
        current_status, _ = self.plugin.get_market_status_and_wait()
        if current_status != MarketStatus.OPEN:
            return False, f"⏱️ 当前市场状态为【{current_status.value}】，无法交易。"
        # ▲▲▲【修正结束】▲▲▲

        stock = await self.plugin.find_stock(identifier)
        if not stock: return False, f"❌ 找不到标识符为 '{identifier}' 的股票。"
        if stock.current_price <= 0: return False, "❌ 股价异常，无法购买。"
        balance = await self.plugin.economy_api.get_coins(user_id)
        if balance < stock.current_price:
            return False, f"💰 金币不足！\n股价为 ${stock.current_price:.2f}，而您只有 {balance:.2f} 金币，连一股都买不起。"
        quantity_to_buy = int(balance // stock.current_price)
        if quantity_to_buy == 0:
            return False, f"💰 金币不足！\n股价为 ${stock.current_price:.2f}，而您只有 {balance:.2f} 金币，连一股都买不起。"
        return await self.perform_buy(user_id, identifier, quantity_to_buy)

    async def perform_sell_all_for_stock(self, user_id: str, identifier: str) -> Tuple[bool, str]:
        """执行全抛单支股票的操作"""
        # ▼▼▼【核心修正】▼▼▼
        current_status, _ = self.plugin.get_market_status_and_wait()
        if current_status != MarketStatus.OPEN:
            return False, f"⏱️ 当前市场状态为【{current_status.value}】，无法交易。"
        # ▲▲▲【修正结束】▲▲▲

        stock = await self.plugin.find_stock(identifier)
        if not stock: return False, f"❌ 找不到标识符为 '{identifier}' 的股票。"
        quantity_to_sell = await self.plugin.db_manager.get_sellable_quantity(user_id, stock.stock_id)
        if quantity_to_sell == 0:
            return False, f"您当前没有可供卖出的 {stock.name} 股票。"
        success, message, _ = await self.perform_sell(user_id, identifier, quantity_to_sell)
        return success, message

    async def perform_sell_all_portfolio(self, user_id: str) -> Tuple[bool, str]:
        """执行清仓操作"""
        # ▼▼▼【核心修正】▼▼▼
        current_status, _ = self.plugin.get_market_status_and_wait()
        if current_status != MarketStatus.OPEN:
            return False, f"⏱️ 当前市场状态为【{current_status.value}】，无法交易。"
        # ▲▲▲【修正结束】▲▲▲

        sellable_stocks = await self.plugin.db_manager.get_sellable_portfolio(user_id)
        if not sellable_stocks:
            return False, "您当前没有可供卖出的持仓。"
        total_net_income, total_profit_loss, total_fees = 0, 0, 0
        sell_details = []
        for stock_id, quantity_to_sell in sellable_stocks:
            stock = self.plugin.stocks.get(stock_id)
            if not stock: continue
            # perform_sell 内部已经有实时检查了，这里理论上可以不加，但为了逻辑清晰和保险起见，保留顶层检查。
            success, _, result_data = await self.perform_sell(user_id, stock_id, quantity_to_sell)
            if success:
                total_net_income += result_data["net_income"]
                total_profit_loss += result_data["profit_loss"]
                total_fees += result_data["fee"]
                pnl_str = f"盈亏 {result_data['profit_loss']:+.2f}"
                sell_details.append(f" - {stock.name}: {quantity_to_sell}股, 收入 {result_data['net_income']:.2f} ({pnl_str})")
        if not sell_details:
            return False, "清仓失败，未能成功卖出任何股票。"
        pnl_emoji = "🎉" if total_profit_loss > 0 else "😭" if total_profit_loss < 0 else "😐"
        details_str = "\n".join(sell_details)
        final_message = (f"🗑️ 已清仓所有可卖持股！\n{details_str}\n--------------------\n"
                         f"总收入: {total_net_income:.2f} 金币\n"
                         f"总手续费: -{total_fees:.2f} 金币\n"
                         f"{pnl_emoji} 总盈亏: {total_profit_loss:+.2f} 金币")
        return True, final_message
