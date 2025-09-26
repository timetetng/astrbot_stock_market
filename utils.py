# stock_market/utils.py

import hashlib
import jwt
from functools import wraps
from passlib.context import CryptContext
from aiohttp import web
from typing import TYPE_CHECKING

from .config import JWT_SECRET_KEY, JWT_ALGORITHM

# 仅用于类型提示，避免循环导入
if TYPE_CHECKING:
    from .web_server import WebServer

# --- 安全与认证 ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def jwt_required(handler):
    """JWT Token 验证装饰器"""
    @wraps(handler)
    async def wrapper(web_server_instance: "WebServer", request: web.Request):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return web.json_response({'error': '未提供认证Token'}, status=401)
        
        token = auth_header.split(' ')[1]
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            request['jwt_payload'] = payload
        except jwt.ExpiredSignatureError:
            return web.json_response({'error': 'Token已过期'}, status=401)
        except jwt.InvalidTokenError:
            return web.json_response({'error': '无效的Token'}, status=401)
            
        return await handler(web_server_instance, request)
    return wrapper

# --- 数据格式化与生成 ---
def generate_user_hash(user_id: str) -> str:
    """根据用户ID生成唯一的、URL友好的哈希字符串。"""
    if not isinstance(user_id, str):
        user_id = str(user_id)
    hash_object = hashlib.md5(user_id.encode('utf-8'))
    return hash_object.hexdigest()[:10]

def format_large_number(num: float) -> str:
    """将一个较大的数字格式化为带有 K, M, B, T, Q 后缀的易读字符串。"""
    if num is None:
        return "0.00"
    suffixes = {
        1_000_000_000_000_000: 'Q',
        1_000_000_000_000: 'T',
        1_000_000_000: 'B',
        1_000_000: 'M',
        1_000: 'K'
    }
    for magnitude, suffix in suffixes.items():
        if abs(num) >= magnitude:
            value = num / magnitude
            return f"{value:.2f} {suffix}"
    return f"{num:,.2f}"