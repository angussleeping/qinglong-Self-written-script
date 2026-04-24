# -*- coding: utf-8 -*-
"""
阿里云盘自动签到脚本（2025年12月独立版）

功能：
- 自动为多个阿里云盘账号进行签到
- 支持从环境变量或配置文件加载账号信息
- 支持多种推送方式（PushPlus、Server 酱）
- 并行处理多个账号，提高执行效率
- 完善的错误处理和日志记录

使用方法：
1. 在青龙面板配置环境变量 ALIYUN_TOKEN（多个账号用 & 分隔）
2. 可选：配置 PUSH_PLUS_TOKEN 或 PUSH_KEY 用于推送通知
3. 设置定时任务：10 6 * * *

更新日志：
- 2025-12: 适配阿里云盘新的签到机制，无需手动领取奖励
- 2026-04: 优化性能，使用异步请求；加强安全性；改进配置管理
"""

import os
import sys
import traceback
import logging
import asyncio
import aiohttp

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 全局常量
API_ENDPOINTS = {
    'token': 'https://auth.aliyundrive.com/v2/account/token',
    'sign_in': 'https://member.aliyundrive.com/v1/activity/sign_in_list'
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36 acf/1.0.0',
    'x-canary': 'client=Android,app=adrive,version=v3.45.1'
}


# ==================== 推送函数（兼容青龙面板） ====================
async def _push_pushplus(title, content, token):
    """使用 PushPlus 推送消息"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://www.pushplus.plus/send",
                json={
                    "token": token,
                    "title": title,
                    "content": content,
                    "template": "html"
                },
                timeout=10
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("code") == 200:
                        logger.info("✅ PushPlus 推送成功")
                        return True
                logger.warning("❌ PushPlus 推送失败")
                return False
    except Exception as e:
        logger.error(f"❌ PushPlus 异常: {e}")
        return False

async def _push_serverchan(title, content, key):
    """使用 Server 酱推送消息"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://sctapi.ftqq.com/{key}.send",
                data={"title": title, "desp": content},
                timeout=10
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("errno") == 0:
                        logger.info("✅ Server 酱推送成功")
                        return True
                logger.warning("❌ Server 酱推送失败")
                return False
    except Exception as e:
        logger.error(f"❌ Server 酱异常: {e}")
        return False

async def _push_telegram(title, content, bot_token, chat_id):
    """使用 Telegram Bot 推送消息"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            params = {
                "chat_id": chat_id,
                "text": f"{title}\n\n{content}",
                "parse_mode": "HTML"
            }
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        logger.info("✅ Telegram 推送成功")
                        return True
                logger.warning("❌ Telegram 推送失败")
                return False
    except Exception as e:
        logger.error(f"❌ Telegram 异常: {e}")
        return False

async def send(title, content):
    """支持 PushPlus、Server 酱和 Telegram Bot（青龙面板标准环境变量）"""
    push_plus_token = os.getenv("PUSH_PLUS_TOKEN")
    push_key = os.getenv("PUSH_KEY")  # Server 酱 SCKEY（sct开头）
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    sent = False

    # PushPlus 推送
    if push_plus_token:
        sent = await _push_pushplus(title, content, push_plus_token)

    # Server 酱推送（SCT）
    if not sent and push_key:
        sent = await _push_serverchan(title, content, push_key)

    # Telegram 推送
    if not sent and telegram_bot_token and telegram_chat_id:
        sent = await _push_telegram(title, content, telegram_bot_token, telegram_chat_id)

    if not sent:
        logger.warning("⚠️ 未配置有效推送方式（请设置 PUSH_KEY、PUSH_PLUS_TOKEN 或 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID）")


# ==================== 阿里云盘签到类 ====================
class ALiYun:
    def __init__(self, tokens):
        self.tokens = tokens
        self.results = []

    async def _get_access_token(self, refresh_token):
        """通过 refresh_token 获取 access_token 和昵称"""
        """
        参数:
            refresh_token (str): 阿里云盘的 refresh_token
        返回:
            tuple: (成功状态, 昵称, access_token, 消息)
        """
        data = {'grant_type': 'refresh_token', 'refresh_token': refresh_token}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(API_ENDPOINTS['token'], json=data, timeout=10) as resp:
                    res = await resp.json()
                    if 'code' in res and res['code'] in ['RefreshTokenExpired', 'InvalidParameter.RefreshToken']:
                        return False, "", "", res.get('message', 'Token 无效或已过期')
                    nick_name = res.get('nick_name') or res.get('user_name', '未知用户')
                    access_token = res['access_token']
                    return True, nick_name, access_token, '成功'
        except Exception as e:
            logger.error(f"获取 access_token 异常: {str(e)}")
            return False, "", "", f"请求异常: {str(e)}"

    async def _check_in(self, access_token):
        """签到（模拟移动端，含 x-canary）"""
        """
        参数:
            access_token (str): 阿里云盘的 access_token
        返回:
            tuple: (成功状态, 签到次数, 消息)
        """
        payload = {'isReward': False}
        params = {'_rx-s': 'mobile'}
        headers = {
            'Authorization': f'Bearer {access_token}',
            **HEADERS
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(API_ENDPOINTS['sign_in'], json=payload, params=params, headers=headers, timeout=10) as resp:
                    data = await resp.json()
                    if not data.get('success'):
                        return False, -1, data.get('message', '未知错误')
                    signin_count = data['result']['signInCount']
                    return True, signin_count, '签到成功'
        except Exception as e:
            logger.error(f"签到异常: {str(e)}")
            return False, -1, f"签到异常: {str(e)}"

    async def _process_account(self, index, refresh_token):
        """处理单个账号的签到"""
        name = f"账号{index+1}"
        refresh_token = refresh_token.strip()
        
        if not refresh_token:
            error_msg = f"👤 账号：{name}\n状态: ❌ 缺少 refresh_token\n\n"
            logger.warning(error_msg.strip())
            return error_msg, False

        try:
            flag, user_name, access_token, msg = await self._get_access_token(refresh_token)
            if not flag:
                error_msg = f"👤 账号：{name}\n状态: ❌ Token 失效 → {msg}\n\n"
                logger.warning(error_msg.strip())
                return error_msg, False

            flag, signin_count, msg = await self._check_in(access_token)
            if not flag:
                error_msg = f"👤 账号：{user_name}\n签到: ❌ {msg}\n\n"
                logger.warning(error_msg.strip())
                return error_msg, False

            # ✅ 2025年起：签到成功即自动发放奖励，无需领奖接口
            success_msg = (
                f"👤 账号：{user_name}\n"
                f"签到: ✅ 本月第 {signin_count} 次\n"
                f"奖励: ✅ 系统已自动发放 1T 容量 1天\n\n"
            )
            logger.info(success_msg.strip())
            return success_msg, True

        except Exception as e:
            error_info = f"执行异常: {str(e)}"
            error_msg = f"👤 账号：{name}\n状态: ❌ {error_info}\n\n"
            logger.error(f"{name}: {error_info}")
            logger.debug(traceback.format_exc())
            return error_msg, False

    async def run(self):
        logger.info("开始执行阿里云盘签到")
        self.results = ["【阿里云盘 执行结果】\n\n"]
        all_success = True

        # 并行处理所有账号
        tasks = []
        for i, token in enumerate(self.tokens):
            tasks.append(self._process_account(i, token))

        results = await asyncio.gather(*tasks)

        for result, success in results:
            self.results.append(result)
            if not success:
                all_success = False

        output = "".join(self.results)
        logger.info("\n" + output)
        await send("✅ 阿里云盘签到成功" if all_success else "❌ 阿里云盘签到失败", output.replace("\n", "<br>"))
        logger.info("阿里云盘签到执行完成")


# ==================== 从环境变量加载配置 ====================
def load_config():
    """加载配置，支持从环境变量或文件加载"""
    # 优先从环境变量加载
    env_tokens = os.getenv("ALIYUN_TOKEN", "")
    
    if env_tokens:
        # 支持多种分隔符
        separators = ["&", "|", ",", "\n"]
        tokens = []
        
        for sep in separators:
            if sep in env_tokens:
                tokens = [t.strip() for t in env_tokens.split(sep) if t.strip()]
                break
        
        if not tokens:
            # 如果没有找到分隔符，整个字符串作为一个 token
            tokens = [env_tokens.strip()]
        
        if not tokens:
            logger.error("❌ ALIYUN_TOKEN 格式错误，未检测到有效 token")
            sys.exit(1)
        
        # 验证 token 格式（简单验证，确保不是明显的恶意输入）
        for token in tokens:
            if not isinstance(token, str) or len(token) < 10:
                logger.error(f"❌ Token 格式错误: {token[:10]}...")
                sys.exit(1)
        
        logger.info(f"✅ 从环境变量加载到 {len(tokens)} 个 token")
        return tokens
    
    # 尝试从配置文件加载
    config_file = os.path.join(os.path.dirname(__file__), "aliyun_config.json")
    if os.path.exists(config_file):
        try:
            # 检查配置文件权限（仅在类 Unix 系统上有效）
            if os.name != 'nt':  # 不是 Windows
                import stat
                file_stat = os.stat(config_file)
                if file_stat.st_mode & stat.S_IROTH:
                    logger.warning("⚠️ 配置文件权限过于宽松，建议设置为 600")
            
            import json
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                tokens = config.get("tokens", [])
                if tokens and isinstance(tokens, list):
                    tokens = [t.strip() for t in tokens if t.strip()]
                    if tokens:
                        # 验证 token 格式
                        for token in tokens:
                            if not isinstance(token, str) or len(token) < 10:
                                logger.error(f"❌ Token 格式错误: {token[:10]}...")
                                sys.exit(1)
                        logger.info(f"✅ 从配置文件加载到 {len(tokens)} 个 token")
                        return tokens
        except Exception as e:
            logger.error(f"❌ 读取配置文件异常: {e}")
    
    logger.error("❌ 请在青龙面板配置环境变量 ALIYUN_TOKEN（多个用 & 分隔）")
    sys.exit(1)


# ==================== 主程序入口 ====================
if __name__ == '__main__':
    tokens = load_config()
    aly = ALiYun(tokens)
    asyncio.run(aly.run())
