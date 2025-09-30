# -*- coding: utf-8 -*-
"""
一个独立的Python脚本，用于读取astrbot虚拟股票插件的数据库，
并生成一个股票市场的“大盘云图”（Treemap）。

版本 11.0 - 错误修复最终版:
1.  修复了 V10 版本中因颜色映射归一化不正确而导致的 "ValueError" 崩溃问题。
2.  现在可以正确地使用用户提供的专业A股色卡进行渲染。
3.  保留了 V10 的所有功能：多时间维度（日线/3小时/1小时）分析、
    专业色卡、更大的字号、专业排序布局等。
"""
import asyncio
import aiosqlite
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import squarify
from matplotlib.font_manager import FontProperties
import os
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from datetime import datetime, timedelta

# --- 配置项 ---
DB_PATH = '/root/AstrBot/data/plugins_db/stock_market/stock_market.db'
FONT_PATH = os.path.join(os.path.dirname(__file__), 'static', 'fonts', 'SimHei.ttf') 

# --- 专业色卡 ---
COLOR_MAP = {
    -4.0: '#28d742', -3.0: '#1da548', -2.0: '#106f2f', -1.0: '#0a5421',
    0.0:  '#424454',
    1.0:  '#6d1414', 2.0:  '#960f0f', 3.0:  '#be1207', 4.0:  '#e41813',
}
COLOR_POINTS = sorted(COLOR_MAP.keys())
COLOR_HEX = [COLOR_MAP[p] for p in COLOR_POINTS]

# --- V11 错误修复 ---
# 正确地将 [-4, 4] 的范围归一化到 [0, 1]
min_val, max_val = min(COLOR_POINTS), max(COLOR_POINTS)
normalized_points = (np.array(COLOR_POINTS) - min_val) / (max_val - min_val)
CUSTOM_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "market_cmap", list(zip(normalized_points, COLOR_HEX))
)
# TwoSlopeNorm 依然是正确的，因为它负责将数据值映射到这个归一化的色带上
NORM = mcolors.TwoSlopeNorm(vmin=-4.0, vcenter=0, vmax=4.0)


async def get_stock_data_with_history(periods_needed: int = 288) -> Optional[pd.DataFrame]:
    """
    异步连接数据库，获取所有股票的最新行情和指定数量的历史K线数据。
    """
    if not os.path.exists(DB_PATH):
        print(f"错误: 数据库文件未找到: {DB_PATH}")
        return None

    all_stock_data = {}
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT stock_id, name, current_price FROM stocks")
            stocks = await cursor.fetchall()
            
            if not stocks:
                print("数据库 'stocks' 表中没有找到任何股票数据。")
                return None

            for stock_id, name, current_price in stocks:
                k_cursor = await db.execute(
                    "SELECT timestamp, close FROM kline_history WHERE stock_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (stock_id, periods_needed)
                )
                history = {row[0]: row[1] for row in await k_cursor.fetchall()}
                all_stock_data[stock_id] = {
                    "name": name, "current_price": current_price, "history": history
                }

    except Exception as e:
        print(f"从数据库读取数据时发生错误: {e}")
        return None

    return all_stock_data


def calculate_change(current_price: float, reference_price: Optional[float]) -> float:
    """计算涨跌幅"""
    if reference_price is None or reference_price == 0:
        return 0.0
    return ((current_price - reference_price) / reference_price) * 100


def get_reference_price(history: Dict[str, float], mode: str, periods: int = 0) -> Optional[float]:
    """根据模式获取用于计算涨跌幅的参考价"""
    if not history:
        return None
        
    sorted_timestamps = sorted(history.keys(), reverse=True)
    
    if mode == 'daily':
        latest_dt = datetime.fromisoformat(sorted_timestamps[0])
        for ts in sorted_timestamps[1:]:
            dt = datetime.fromisoformat(ts)
            if dt.date() < latest_dt.date():
                return history[ts]
        return None
        
    elif mode == 'periods':
        if len(sorted_timestamps) > periods:
            ref_ts = sorted_timestamps[periods]
            return history[ref_ts]
        return None


def generate_market_treemap(df: pd.DataFrame, title: str, filename: str):
    """
    根据提供的DataFrame生成并保存大盘云图。
    """
    if df is None or df.empty:
        print(f"数据为空，无法为 {title} 生成云图。")
        return

    df = df.sort_values(by='price', ascending=False).reset_index(drop=True)

    bg_colors = [mcolors.to_hex(CUSTOM_CMAP(NORM(p))) for p in df['change_percent']]
    text_colors = []
    for color_hex in bg_colors:
        r, g, b = mcolors.to_rgb(color_hex)
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        text_colors.append('black' if luminance > 0.4 else 'white')

    sizes = np.log1p(df['price'].values)
    
    labels = [
        f"{row['name']}\n{row['change_percent']:.2f}%\n¥{row['price']:.2f}"
        for _, row in df.iterrows()
    ]
    
    font_prop = FontProperties(fname=FONT_PATH, size=18, weight='bold') if os.path.exists(FONT_PATH) else FontProperties(size=12, weight='bold')

    plt.style.use('dark_background')
    fig, ax = plt.subplots(1, figsize=(16, 9), dpi=200)
    
    squarify.plot(
        sizes=sizes, color=bg_colors, ax=ax, alpha=0.9,
        label=None, edgecolor="black", linewidth=1.5
    )
    
    drawn_rects = ax.patches
    for i, rect in enumerate(drawn_rects):
        if i < len(labels):
            x, y = rect.get_xy()
            dx, dy = rect.get_width(), rect.get_height()
            
            ax.text(
                x + dx / 2, y + dy / 2, labels[i], ha='center', va='center',
                fontproperties=font_prop, color=text_colors[i]
            )

    title_font_prop = FontProperties(fname=FONT_PATH, size=20) if os.path.exists(FONT_PATH) else FontProperties(size=20)
    plt.title(title, fontproperties=title_font_prop, color='white', pad=20)
    plt.axis('off')
    plt.tight_layout()

    try:
        plt.savefig(filename, bbox_inches='tight', pad_inches=0.1, facecolor=fig.get_facecolor(), edgecolor='none')
        print(f"成功生成大盘云图，已保存为: {filename}")
    except Exception as e:
        print(f"保存图片时发生错误: {e}")
    plt.close(fig)


async def main():
    print("正在从数据库加载股票及历史数据...")
    all_stock_data = await get_stock_data_with_history(periods_needed=288) 
    
    if not all_stock_data:
        print("获取股票数据失败，程序终止。")
        return

    time_frames = {
        "日线级 (vs 昨日收盘)": ("daily", 0, "market_treemap_daily.png"),
        "10分钟": ("periods", 2, "market_treemap_3_hour.png"),
        "30分钟": ("periods", 6, "market_treemap_1_hour.png")
    }

    for title, (mode, periods, filename) in time_frames.items():
        print(f"\n--- 正在处理: {title} ---")
        
        processed_data = []
        for stock_id, data in all_stock_data.items():
            ref_price = get_reference_price(data['history'], mode, periods)
            if ref_price is None:
                print(f"警告: 股票 {data['name']} 缺少足够的历史数据来计算 {title}，涨跌幅计为0。")
            
            change_percent = calculate_change(data['current_price'], ref_price)
            
            processed_data.append({
                "name": data['name'], "price": data['current_price'], "change_percent": change_percent,
            })
        
        df = pd.DataFrame(processed_data)
        generate_market_treemap(df, f"虚拟股票市场 - 大盘云图 ({title})", filename)


if __name__ == "__main__":
    asyncio.run(main())